import SwiftUI
import UIKit

// MARK: - キーの割り当て

/// コントローラのボタンに割り当てる、キーの組
enum ControllerPreset: String, CaseIterable, Identifiable {
    /// 十字が矢印キー
    case arrows
    /// 十字が W、A、S、D
    case wasd

    var id: String { rawValue }

    var label: String {
        switch self {
        case .arrows: "矢印キー"
        case .wasd: "WASD"
        }
    }

    var keys: ControllerKeys {
        switch self {
        case .arrows:
            ControllerKeys(
                up: ControllerKey(label: "↑", sc: 0xE048),
                down: ControllerKey(label: "↓", sc: 0xE050),
                left: ControllerKey(label: "←", sc: 0xE04B),
                right: ControllerKey(label: "→", sc: 0xE04D),
                faceDown: ControllerKey(label: "Z", sc: 0x2C),
                faceRight: ControllerKey(label: "X", sc: 0x2D),
                faceUp: ControllerKey(label: "C", sc: 0x2E),
                faceLeft: ControllerKey(label: "V", sc: 0x2F),
                l: ControllerKey(label: "A", sc: 0x1E),
                r: ControllerKey(label: "S", sc: 0x1F),
                start: ControllerKey(label: "Start", sc: 0x1C),
                select: ControllerKey(label: "Select", sc: 0x01)
            )
        case .wasd:
            ControllerKeys(
                up: ControllerKey(label: "W", sc: 0x11),
                down: ControllerKey(label: "S", sc: 0x1F),
                left: ControllerKey(label: "A", sc: 0x1E),
                right: ControllerKey(label: "D", sc: 0x20),
                faceDown: ControllerKey(label: "J", sc: 0x24),
                faceRight: ControllerKey(label: "K", sc: 0x25),
                faceUp: ControllerKey(label: "L", sc: 0x26),
                faceLeft: ControllerKey(label: "I", sc: 0x17),
                l: ControllerKey(label: "Q", sc: 0x10),
                r: ControllerKey(label: "E", sc: 0x12),
                start: ControllerKey(label: "Start", sc: 0x1C),
                select: ControllerKey(label: "Select", sc: 0x01)
            )
        }
    }
}

/// ボタンの文字と、送るスキャンコード（OverlayKeyboardTables と同じ値）
struct ControllerKey {
    let label: String
    let sc: Int
}

struct ControllerKeys {
    let up: ControllerKey
    let down: ControllerKey
    let left: ControllerKey
    let right: ControllerKey
    let faceDown: ControllerKey
    let faceRight: ControllerKey
    let faceUp: ControllerKey
    let faceLeft: ControllerKey
    let l: ControllerKey
    let r: ControllerKey
    let start: ControllerKey
    let select: ControllerKey
}

// MARK: - SwiftUI の入れ物

/// 画面に描くゲームパッド。PC には、キーボードのキーとして送る
struct VirtualControllerView: UIViewRepresentable {
    let session: RemoteSession
    let preset: ControllerPreset
    /// キーを押したときの振動（設定で切り替える）
    @AppStorage(OverlayKeyboard.hapticsKey) private var haptics = true

    func makeUIView(context: Context) -> VirtualControllerUIView {
        let view = VirtualControllerUIView()
        view.session = session
        view.preset = preset
        view.hapticsOn = haptics
        return view
    }

    func updateUIView(_ uiView: VirtualControllerUIView, context: Context) {
        uiView.session = session
        uiView.hapticsOn = haptics
        if uiView.preset != preset {
            uiView.preset = preset
        }
    }

    static func dismantleUIView(_ uiView: VirtualControllerUIView, coordinator: ()) {
        uiView.releaseAll()
    }
}

// MARK: - 描いて、タッチを受ける

/// 十字、4つのボタン、Start と Select、肩のボタン（L、R）を描いて、タッチを受け取る。
/// 何本の指でも同時に押せる。押している間だけ、キーを押した状態にする。
/// 指を離したとき、画面の外に出たとき、見えなくなったとき、アプリが裏に回ったときは、押したキーを全部離す
final class VirtualControllerUIView: UIView {
    weak var session: RemoteSession?
    var hapticsOn = true
    var preset: ControllerPreset = .arrows {
        didSet {
            guard preset != oldValue else { return }
            releaseAll()
            rebuild()
            setNeedsDisplay()
        }
    }

    private struct PadButton {
        let key: ControllerKey
        let rect: CGRect
        /// true なら丸、false なら横長の丸角
        let round: Bool

        func contains(_ p: CGPoint) -> Bool {
            if round {
                let c = CGPoint(x: rect.midX, y: rect.midY)
                return hypot(p.x - c.x, p.y - c.y) <= rect.width / 2 + 6
            }
            return rect.insetBy(dx: -4, dy: -4).contains(p)
        }
    }

    private var buttons: [PadButton] = []
    private var dpadCenter: CGPoint = .zero
    /// ボタンの直径
    private var unit: CGFloat = 56
    /// 指ごとの、いま押しているスキャンコード
    private var touchKeys: [ObjectIdentifier: Set<Int>] = [:]
    /// スキャンコードごとの、押している指の数。0 から 1 になったときに down、1 から 0 になったときに up を送る
    private var keyCount: [Int: Int] = [:]
    private let haptic = UIImpactFeedbackGenerator(style: .light)

    override init(frame: CGRect) {
        super.init(frame: frame)
        isMultipleTouchEnabled = true
        isOpaque = false
        backgroundColor = .clear
        contentMode = .redraw
        NotificationCenter.default.addObserver(
            self, selector: #selector(appInactive),
            name: UIApplication.willResignActiveNotification, object: nil
        )
        NotificationCenter.default.addObserver(
            self, selector: #selector(appInactive),
            name: UIApplication.didEnterBackgroundNotification, object: nil
        )
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    @objc private func appInactive() {
        releaseAll()
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        if window == nil { releaseAll() }
    }

    // MARK: 並べる

    override func layoutSubviews() {
        super.layoutSubviews()
        rebuild()
        setNeedsDisplay()
    }

    private func rebuild() {
        let w = bounds.width
        let h = bounds.height
        guard w > 0, h > 0 else { return }
        let k = preset.keys
        let d = max(56, min(72, min(h * 0.2, w * 0.15)))
        unit = d
        let margin: CGFloat = 12
        let top: CGFloat = 6

        // 肩のボタン
        let shoulderW = d * 1.5
        let shoulderH = d * 0.8
        // 真ん中の上の、Start と Select
        let pillW = d * 1.2
        let pillH = d * 0.7
        let pillY = top + (shoulderH - pillH) / 2

        // 十字と、右の4つは、同じ高さに置く
        let reach = d * 1.5
        let lower = top + shoulderH + 8 + reach
        let upper = h - top - reach
        let cy = upper >= lower ? min(max(h * 0.6, lower), upper) : lower
        let leftX = margin + reach
        let rightX = w - margin - reach
        dpadCenter = CGPoint(x: leftX, y: cy)

        func circle(_ key: ControllerKey, _ x: CGFloat, _ y: CGFloat) -> PadButton {
            PadButton(key: key, rect: CGRect(x: x - d / 2, y: y - d / 2, width: d, height: d), round: true)
        }

        buttons = [
            circle(k.faceDown, rightX, cy + d),
            circle(k.faceRight, rightX + d, cy),
            circle(k.faceUp, rightX, cy - d),
            circle(k.faceLeft, rightX - d, cy),
            PadButton(key: k.l, rect: CGRect(x: margin, y: top, width: shoulderW, height: shoulderH), round: false),
            PadButton(key: k.r, rect: CGRect(x: w - margin - shoulderW, y: top, width: shoulderW, height: shoulderH), round: false),
            PadButton(key: k.select, rect: CGRect(x: w / 2 - 5 - pillW, y: pillY, width: pillW, height: pillH), round: false),
            PadButton(key: k.start, rect: CGRect(x: w / 2 + 5, y: pillY, width: pillW, height: pillH), round: false),
        ]
    }

    // MARK: 描く

    private func isPressed(_ key: ControllerKey) -> Bool {
        (keyCount[key.sc] ?? 0) > 0
    }

    override func draw(_ rect: CGRect) {
        guard !buttons.isEmpty else { return }
        let k = preset.keys
        let d = unit
        let base = UIColor(white: 1, alpha: 0.18)
        let accent = tintColor.withAlphaComponent(0.65)
        let edge = UIColor(white: 1, alpha: 0.12)

        // 十字
        let c = dpadCenter
        let centerRect = CGRect(x: c.x - d / 2, y: c.y - d / 2, width: d, height: d)
        let arms: [(key: ControllerKey, rect: CGRect, glyph: String)] = [
            (k.up, CGRect(x: c.x - d / 2, y: c.y - d * 1.5, width: d, height: d), "↑"),
            (k.down, CGRect(x: c.x - d / 2, y: c.y + d / 2, width: d, height: d), "↓"),
            (k.left, CGRect(x: c.x - d * 1.5, y: c.y - d / 2, width: d, height: d), "←"),
            (k.right, CGRect(x: c.x + d / 2, y: c.y - d / 2, width: d, height: d), "→"),
        ]
        fillRounded(centerRect, radius: 10, color: base, edge: edge)
        for arm in arms {
            fillRounded(arm.rect, radius: 10, color: isPressed(arm.key) ? accent : base, edge: edge)
            drawLabel(arm.glyph, size: d * 0.4, in: arm.rect)
        }

        // 丸いボタン、肩のボタン、Start と Select
        for b in buttons {
            let color = isPressed(b.key) ? accent : base
            if b.round {
                color.setFill()
                let path = UIBezierPath(ovalIn: b.rect)
                path.fill()
                edge.setStroke()
                path.lineWidth = 0.5
                path.stroke()
                drawLabel(b.key.label, size: d * 0.4, in: b.rect)
            } else {
                fillRounded(b.rect, radius: b.rect.height / 2, color: color, edge: edge)
                let isShoulder = b.key.label.count == 1
                drawLabel(b.key.label, size: isShoulder ? d * 0.34 : d * 0.24, in: b.rect)
            }
        }
    }

    private func fillRounded(_ rect: CGRect, radius: CGFloat, color: UIColor, edge: UIColor) {
        color.setFill()
        let path = UIBezierPath(roundedRect: rect, cornerRadius: radius)
        path.fill()
        edge.setStroke()
        path.lineWidth = 0.5
        path.stroke()
    }

    private func drawLabel(_ text: String, size: CGFloat, in box: CGRect) {
        let style = NSMutableParagraphStyle()
        style.alignment = .center
        style.lineBreakMode = .byClipping
        let attrs: [NSAttributedString.Key: Any] = [
            .font: UIFont.systemFont(ofSize: size, weight: .semibold),
            .foregroundColor: UIColor.white,
            .paragraphStyle: style,
        ]
        let textSize = (text as NSString).size(withAttributes: attrs)
        let r = CGRect(x: box.minX, y: box.midY - textSize.height / 2, width: box.width, height: textSize.height)
        (text as NSString).draw(in: r, withAttributes: attrs)
    }

    // MARK: 触る

    /// 指の位置で押していることになるキー（スキャンコード）。十字は、ななめも含む
    private func scans(at p: CGPoint) -> Set<Int> {
        var result = Set<Int>()
        let k = preset.keys
        let dx = p.x - dpadCenter.x
        let dy = p.y - dpadCenter.y
        if hypot(dx, dy) <= unit * 1.9 {
            let t = unit * 0.3
            let ax = abs(dx)
            let ay = abs(dy)
            // ほぼ真横、ほぼ真上のときは、その方向だけ。ななめに寄ったときは、2方向
            if ax > t && ay < ax * 2 { result.insert(dx < 0 ? k.left.sc : k.right.sc) }
            if ay > t && ax < ay * 2 { result.insert(dy < 0 ? k.up.sc : k.down.sc) }
        }
        if let b = buttons.first(where: { $0.contains(p) }) {
            result.insert(b.key.sc)
        }
        return result
    }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        for touch in touches {
            apply(ObjectIdentifier(touch), scans(at: touch.location(in: self)))
        }
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        for touch in touches {
            let p = touch.location(in: self)
            // 画面の外に出たら、離したことにする
            apply(ObjectIdentifier(touch), bounds.contains(p) ? scans(at: p) : [])
        }
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) {
        for touch in touches {
            apply(ObjectIdentifier(touch), [])
        }
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) {
        touchesEnded(touches, with: event)
    }

    /// 1本の指が押しているキーを、新しい組に置き換える。増えたものは押し、減ったものは離す
    private func apply(_ id: ObjectIdentifier, _ new: Set<Int>) {
        let old = touchKeys[id] ?? []
        guard new != old else { return }
        for sc in new.subtracting(old) { press(sc) }
        for sc in old.subtracting(new) { release(sc) }
        touchKeys[id] = new.isEmpty ? nil : new
        setNeedsDisplay()
    }

    private func press(_ sc: Int) {
        let count = (keyCount[sc] ?? 0) + 1
        keyCount[sc] = count
        if count == 1 {
            if hapticsOn { haptic.impactOccurred() }
            session?.sendScan(sc, down: true)
        }
    }

    private func release(_ sc: Int) {
        guard let count = keyCount[sc] else { return }
        if count <= 1 {
            keyCount[sc] = nil
            session?.sendScan(sc, down: false)
        } else {
            keyCount[sc] = count - 1
        }
    }

    /// 押しているキーを、全部離す。見えなくなったときや、アプリが裏に回ったときに呼ぶ
    func releaseAll() {
        for sc in keyCount.keys {
            session?.sendScan(sc, down: false)
        }
        keyCount.removeAll()
        touchKeys.removeAll()
        setNeedsDisplay()
    }
}
