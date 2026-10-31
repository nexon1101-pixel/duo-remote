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
        guard !buttons.isEmpty, let ctx = UIGraphicsGetCurrentContext() else { return }
        let k = preset.keys
        let d = unit
        let accent = tintColor

        drawDPad(ctx: ctx, keys: k, d: d, accent: accent)

        // 丸いボタン、肩のボタン、Start と Select
        for b in buttons {
            let pressed = isPressed(b.key)
            if b.round {
                // 4つのボタンは、位置ごとに色を付ける（下: 緑、右: 赤、左: 青、上: 黄）
                let tint = faceTint(b.key, keys: k)
                let r = pressed ? b.rect.insetBy(dx: -2, dy: -2) : b.rect
                ctx.saveGState()
                ctx.setShadow(offset: CGSize(width: 0, height: 2), blur: 5, color: UIColor(white: 0, alpha: 0.35).cgColor)
                tint.withAlphaComponent(pressed ? 0.9 : 0.3).setFill()
                UIBezierPath(ovalIn: r).fill()
                ctx.restoreGState()
                tint.withAlphaComponent(pressed ? 1 : 0.75).setStroke()
                let ring = UIBezierPath(ovalIn: r.insetBy(dx: 0.75, dy: 0.75))
                ring.lineWidth = 1.5
                ring.stroke()
                drawLabel(b.key.label, size: d * 0.38, weight: .bold, color: .white, in: r)
            } else {
                let isShoulder = b.key.label.count == 1
                ctx.saveGState()
                ctx.setShadow(offset: CGSize(width: 0, height: 2), blur: 4, color: UIColor(white: 0, alpha: 0.3).cgColor)
                (pressed ? accent.withAlphaComponent(0.7) : UIColor(white: 1, alpha: 0.16)).setFill()
                UIBezierPath(roundedRect: b.rect, cornerRadius: b.rect.height / 2).fill()
                ctx.restoreGState()
                UIColor(white: 1, alpha: 0.18).setStroke()
                let edge = UIBezierPath(roundedRect: b.rect.insetBy(dx: 0.25, dy: 0.25), cornerRadius: b.rect.height / 2)
                edge.lineWidth = 0.5
                edge.stroke()
                drawLabel(
                    b.key.label, size: isShoulder ? d * 0.34 : d * 0.22,
                    weight: isShoulder ? .bold : .semibold, color: UIColor(white: 1, alpha: 0.92), in: b.rect
                )
            }
        }
    }

    /// 十字は、1つの十字の形に描く。押している方向だけ、色を付ける。矢印は、三角で描く
    private func drawDPad(ctx: CGContext, keys k: ControllerKeys, d: CGFloat, accent: UIColor) {
        let c = dpadCenter
        let corner = d * 0.3
        let horizontal = CGRect(x: c.x - d * 1.5, y: c.y - d / 2, width: d * 3, height: d)
        let vertical = CGRect(x: c.x - d / 2, y: c.y - d * 1.5, width: d, height: d * 3)
        let cross = UIBezierPath(roundedRect: horizontal, cornerRadius: corner)
        cross.append(UIBezierPath(roundedRect: vertical, cornerRadius: corner))
        ctx.saveGState()
        ctx.setShadow(offset: CGSize(width: 0, height: 2), blur: 6, color: UIColor(white: 0, alpha: 0.4).cgColor)
        UIColor(white: 1, alpha: 0.16).setFill()
        cross.fill()
        ctx.restoreGState()
        // 真ん中の、くぼみ
        UIColor(white: 0, alpha: 0.18).setFill()
        UIBezierPath(ovalIn: CGRect(x: c.x - d * 0.26, y: c.y - d * 0.26, width: d * 0.52, height: d * 0.52)).fill()

        let arms: [(key: ControllerKey, dir: CGPoint, offset: CGPoint)] = [
            (k.up, CGPoint(x: 0, y: -1), CGPoint(x: 0, y: -1)),
            (k.down, CGPoint(x: 0, y: 1), CGPoint(x: 0, y: 1)),
            (k.left, CGPoint(x: -1, y: 0), CGPoint(x: -1, y: 0)),
            (k.right, CGPoint(x: 1, y: 0), CGPoint(x: 1, y: 0)),
        ]
        for arm in arms {
            let box = CGRect(x: c.x + arm.offset.x * d - d / 2, y: c.y + arm.offset.y * d - d / 2, width: d, height: d)
            let pressed = isPressed(arm.key)
            if pressed {
                accent.withAlphaComponent(0.7).setFill()
                UIBezierPath(roundedRect: box.insetBy(dx: 2, dy: 2), cornerRadius: corner * 0.8).fill()
            }
            drawArrow(direction: arm.dir, in: box, bright: pressed)
        }
    }

    private func drawArrow(direction dir: CGPoint, in box: CGRect, bright: Bool) {
        let s = box.width * 0.17
        let m = CGPoint(x: box.midX, y: box.midY)
        let perp = CGPoint(x: -dir.y, y: dir.x)
        let tip = CGPoint(x: m.x + dir.x * s, y: m.y + dir.y * s)
        let baseCenter = CGPoint(x: m.x - dir.x * s * 0.7, y: m.y - dir.y * s * 0.7)
        let p1 = CGPoint(x: baseCenter.x + perp.x * s * 1.1, y: baseCenter.y + perp.y * s * 1.1)
        let p2 = CGPoint(x: baseCenter.x - perp.x * s * 1.1, y: baseCenter.y - perp.y * s * 1.1)
        let path = UIBezierPath()
        path.move(to: tip)
        path.addLine(to: p1)
        path.addLine(to: p2)
        path.close()
        path.lineJoinStyle = .round
        path.lineWidth = 2
        let color = UIColor(white: 1, alpha: bright ? 1 : 0.7)
        color.setFill()
        color.setStroke()
        path.fill()
        path.stroke()
    }

    /// 4つのボタンの色。位置で決める
    private func faceTint(_ key: ControllerKey, keys k: ControllerKeys) -> UIColor {
        switch key.sc {
        case k.faceDown.sc: return .systemGreen
        case k.faceRight.sc: return .systemRed
        case k.faceLeft.sc: return .systemBlue
        case k.faceUp.sc: return .systemYellow
        default: return .white
        }
    }

    private func drawLabel(_ text: String, size: CGFloat, weight: UIFont.Weight, color: UIColor, in box: CGRect) {
        let style = NSMutableParagraphStyle()
        style.alignment = .center
        style.lineBreakMode = .byClipping
        let base = UIFont.systemFont(ofSize: size, weight: weight)
        let font = base.fontDescriptor.withDesign(.rounded).map { UIFont(descriptor: $0, size: size) } ?? base
        let attrs: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: color,
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
