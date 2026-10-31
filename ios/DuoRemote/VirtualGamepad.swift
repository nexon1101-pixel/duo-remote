import SwiftUI
import UIKit

// MARK: - コントローラの種類

/// ゲームモードのコントローラの種類
enum ControllerKind: String, CaseIterable, Identifiable {
    /// キーボード入力。キーボードのキーとして送る（VirtualController.swift）
    case simple
    /// ゲームパッド。仮想の Xbox 360 ゲームパッドに、状態を送る（VirtualGamepad.swift、PhysicalController.swift）
    case pad

    static let storageKey = "controllerKind"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .simple: "キーボード入力"
        case .pad: "ゲームパッド"
        }
    }
}

// MARK: - SwiftUI の入れ物

/// 画面に描くゲームパッド。PC には、仮想の Xbox 360 ゲームパッドの状態として送る
struct VirtualGamepadView: UIViewRepresentable {
    let session: RemoteSession
    /// false のときは、触れないようにして、押している分を離す（本物のコントローラを使うとき）
    let enabled: Bool
    /// ボタンを押したときの振動（設定で切り替える）
    @AppStorage(OverlayKeyboard.hapticsKey) private var haptics = true

    func makeUIView(context: Context) -> VirtualGamepadUIView {
        let view = VirtualGamepadUIView()
        view.session = session
        view.hapticsOn = haptics
        view.isEnabledForTouch = enabled
        return view
    }

    func updateUIView(_ uiView: VirtualGamepadUIView, context: Context) {
        uiView.session = session
        uiView.hapticsOn = haptics
        uiView.isEnabledForTouch = enabled
    }

    static func dismantleUIView(_ uiView: VirtualGamepadUIView, coordinator: ()) {
        uiView.releaseAll()
    }
}

// MARK: - 描いて、タッチを受ける

/// 2本のスティック、十字、A B X Y、LB RB、LT RT、Start、Back、L3、R3 を描いて、タッチを受け取る。
/// 何本の指でも同時に触れる。指が離れたとき、画面の外に出たとき、見えなくなったとき、アプリが裏に回ったときは、
/// 全部離した状態（スティックは 0）を送り、仮想のゲームパッドを取り外す
final class VirtualGamepadUIView: UIView {
    weak var session: RemoteSession?
    var hapticsOn = true
    var isEnabledForTouch = true {
        didSet {
            guard isEnabledForTouch != oldValue else { return }
            isUserInteractionEnabled = isEnabledForTouch
            if !isEnabledForTouch { releaseAll() }
        }
    }

    // ボタンの値は XInput と同じ（docs/protocol.md）。トリガーは、16ビットの外に置く
    private static let up = 0x0001
    private static let down = 0x0002
    private static let left = 0x0004
    private static let right = 0x0008
    private static let start = 0x0010
    private static let back = 0x0020
    private static let l3 = 0x0040
    private static let r3 = 0x0080
    private static let lb = 0x0100
    private static let rb = 0x0200
    private static let a = 0x1000
    private static let b = 0x2000
    private static let x = 0x4000
    private static let y = 0x8000
    private static let lt = 0x10000
    private static let rt = 0x20000

    /// 指を動かせる半径は、スティックの直径のこの倍
    private static let travelFactor: CGFloat = 0.8

    private struct PadItem {
        let code: Int
        let label: String
        let rect: CGRect
        /// 丸ならtrue、横長の丸角ならfalse
        let round: Bool
        /// 4つのボタンの色。ほかは nil
        let tint: UIColor?
        let isShoulder: Bool

        func contains(_ p: CGPoint) -> Bool {
            if round {
                let c = CGPoint(x: rect.midX, y: rect.midY)
                return hypot(p.x - c.x, p.y - c.y) <= rect.width / 2 + 6
            }
            return rect.insetBy(dx: -4, dy: -4).contains(p)
        }
    }

    private enum Side {
        case left
        case right
    }

    /// スティック。指が触れた所を中心にして、引いた向きと量を -1.0〜1.0 にする
    private struct StickState {
        var touch: ObjectIdentifier?
        var origin: CGPoint = .zero
        /// 画面の向きのまま（y は下が正）。長さは 1 まで
        var value: CGPoint = .zero
    }

    private var items: [PadItem] = []
    private var dpadCenter: CGPoint = .zero
    private var restLeft: CGPoint = .zero
    private var restRight: CGPoint = .zero
    /// ボタンの直径（十字の1マスの大きさ）
    private var unit: CGFloat = 56
    private var stickRadius: CGFloat = 56
    private var leftStick = StickState()
    private var rightStick = StickState()
    /// 指ごとの、いま押しているボタン
    private var touchMasks: [ObjectIdentifier: Int] = [:]
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

    /// 板の大きさから、部品の大きさを決める。
    /// 高さが足りないときは、Xbox と同じように、左は スティックを上、十字を右の下へずらし、右は ボタンを上、スティックを左の下へずらす
    private func rebuild() {
        let w = bounds.width
        let h = bounds.height
        guard w > 0, h > 0 else { return }
        let margin: CGFloat = 12
        let top: CGFloat = 6

        let shoulderH = max(26, min(40, h * 0.13))
        let areaTop = top + shoulderH + 8
        let areaBottom = h - 6
        let areaH = max(areaBottom - areaTop, 60)
        let halfW = max(w / 2 - 30, 60)

        // 縦に並べるときは、スティック（直径 2u）と十字（3u）で 5u。ずらすときは 3u
        let stackedU = max(0, min((areaH - 8) / 5, halfW / 3))
        let staggeredU = max(0, min(areaH / 3, halfW / 5))
        let staggered = staggeredU > stackedU
        let u = max(24, min(64, staggered ? staggeredU : stackedU))
        unit = u
        stickRadius = u

        let reach = u * 1.5
        let leftStickX = staggered ? margin + u : margin + reach
        let dpadX = staggered ? leftStickX + u + reach + 4 : margin + reach
        let facesX = w - margin - reach
        let rightStickX = staggered ? facesX - reach - 4 - u : w - margin - reach
        let facesY = areaTop + reach
        restLeft = CGPoint(x: leftStickX, y: areaTop + u)
        dpadCenter = CGPoint(x: dpadX, y: areaBottom - reach)
        restRight = CGPoint(x: rightStickX, y: areaBottom - u)

        func face(_ code: Int, _ label: String, _ color: UIColor, _ dx: CGFloat, _ dy: CGFloat) -> PadItem {
            let cx = facesX + dx
            let cy = facesY + dy
            return PadItem(
                code: code, label: label, rect: CGRect(x: cx - u / 2, y: cy - u / 2, width: u, height: u),
                round: true, tint: color, isShoulder: false
            )
        }

        // 肩のボタンとトリガー。上の角に置く
        let gap: CGFloat = 6
        let sw = u * 1.6
        func shoulder(_ code: Int, _ label: String, _ x: CGFloat) -> PadItem {
            PadItem(
                code: code, label: label, rect: CGRect(x: x, y: top, width: sw, height: shoulderH),
                round: false, tint: nil, isShoulder: true
            )
        }

        // 真ん中の上: L3、Back、Start、R3
        let pillW = u * 1.1
        let pillH = max(22, u * 0.5)
        let small = max(26, u * 0.55)
        let rowW = small * 2 + pillW * 2 + gap * 3
        let rowX = w / 2 - rowW / 2
        func center(_ code: Int, _ label: String, x: CGFloat, width: CGFloat, height: CGFloat, round: Bool) -> PadItem {
            PadItem(
                code: code, label: label,
                rect: CGRect(x: x, y: top + (shoulderH - height) / 2, width: width, height: height),
                round: round, tint: nil, isShoulder: false
            )
        }

        items = [
            face(Self.a, "A", .systemGreen, 0, u),
            face(Self.b, "B", .systemRed, u, 0),
            face(Self.x, "X", .systemBlue, -u, 0),
            face(Self.y, "Y", .systemYellow, 0, -u),
            shoulder(Self.lt, "LT", margin),
            shoulder(Self.lb, "LB", margin + sw + gap),
            shoulder(Self.rt, "RT", w - margin - sw),
            shoulder(Self.rb, "RB", w - margin - sw * 2 - gap),
            center(Self.l3, "L3", x: rowX, width: small, height: small, round: true),
            center(Self.back, "Back", x: rowX + small + gap, width: pillW, height: pillH, round: false),
            center(Self.start, "Start", x: rowX + small + gap * 2 + pillW, width: pillW, height: pillH, round: false),
            center(Self.r3, "R3", x: rowX + small + gap * 3 + pillW * 2, width: small, height: small, round: true),
        ]
    }

    // MARK: 描く

    private var heldMask: Int {
        touchMasks.values.reduce(0) { $0 | $1 }
    }

    override func draw(_ rect: CGRect) {
        guard !items.isEmpty, let ctx = UIGraphicsGetCurrentContext() else { return }
        let d = unit
        let accent: UIColor = tintColor ?? .systemBlue
        let held = heldMask

        drawDPad(ctx: ctx, held: held, d: d, accent: accent)
        drawStick(ctx: ctx, rest: restLeft, state: leftStick, accent: accent)
        drawStick(ctx: ctx, rest: restRight, state: rightStick, accent: accent)

        for item in items {
            let pressed = held & item.code != 0
            if let tint = item.tint {
                // 4つのボタンは、色を付ける（A 緑、B 赤、X 青、Y 黄）
                let r = pressed ? item.rect.insetBy(dx: -2, dy: -2) : item.rect
                ctx.saveGState()
                ctx.setShadow(offset: CGSize(width: 0, height: 2), blur: 5, color: UIColor(white: 0, alpha: 0.35).cgColor)
                tint.withAlphaComponent(pressed ? 0.9 : 0.3).setFill()
                UIBezierPath(ovalIn: r).fill()
                ctx.restoreGState()
                tint.withAlphaComponent(pressed ? 1 : 0.75).setStroke()
                let ring = UIBezierPath(ovalIn: r.insetBy(dx: 0.75, dy: 0.75))
                ring.lineWidth = 1.5
                ring.stroke()
                drawLabel(item.label, size: d * 0.38, weight: .bold, color: .white, in: r)
            } else {
                let radius = item.rect.height / 2
                ctx.saveGState()
                ctx.setShadow(offset: CGSize(width: 0, height: 2), blur: 4, color: UIColor(white: 0, alpha: 0.3).cgColor)
                (pressed ? accent.withAlphaComponent(0.7) : UIColor(white: 1, alpha: 0.16)).setFill()
                UIBezierPath(roundedRect: item.rect, cornerRadius: radius).fill()
                ctx.restoreGState()
                UIColor(white: 1, alpha: 0.18).setStroke()
                let edge = UIBezierPath(roundedRect: item.rect.insetBy(dx: 0.25, dy: 0.25), cornerRadius: radius)
                edge.lineWidth = 0.5
                edge.stroke()
                drawLabel(
                    item.label, size: item.isShoulder ? max(11, d * 0.3) : max(10, d * 0.2),
                    weight: item.isShoulder ? .bold : .semibold, color: UIColor(white: 1, alpha: 0.92), in: item.rect
                )
            }
        }
    }

    /// スティック。触っていないときは、決まった位置に置く。触っているときは、触った所を中心にして、つまみを動かす
    private func drawStick(ctx: CGContext, rest: CGPoint, state: StickState, accent: UIColor) {
        let active = state.touch != nil
        let r = stickRadius
        let c = active ? state.origin : rest
        let base = CGRect(x: c.x - r, y: c.y - r, width: r * 2, height: r * 2)
        ctx.saveGState()
        ctx.setShadow(offset: CGSize(width: 0, height: 2), blur: 6, color: UIColor(white: 0, alpha: 0.4).cgColor)
        UIColor(white: 1, alpha: active ? 0.2 : 0.14).setFill()
        UIBezierPath(ovalIn: base).fill()
        ctx.restoreGState()
        UIColor(white: 1, alpha: 0.18).setStroke()
        let edge = UIBezierPath(ovalIn: base.insetBy(dx: 0.25, dy: 0.25))
        edge.lineWidth = 0.5
        edge.stroke()

        let knobR = r * 0.5
        let k = CGPoint(x: c.x + state.value.x * r, y: c.y + state.value.y * r)
        let knob = CGRect(x: k.x - knobR, y: k.y - knobR, width: knobR * 2, height: knobR * 2)
        ctx.saveGState()
        ctx.setShadow(offset: CGSize(width: 0, height: 2), blur: 4, color: UIColor(white: 0, alpha: 0.35).cgColor)
        (active ? accent.withAlphaComponent(0.7) : UIColor(white: 1, alpha: 0.28)).setFill()
        UIBezierPath(ovalIn: knob).fill()
        ctx.restoreGState()
        UIColor(white: 1, alpha: 0.3).setStroke()
        let ring = UIBezierPath(ovalIn: knob.insetBy(dx: 0.25, dy: 0.25))
        ring.lineWidth = 0.5
        ring.stroke()
    }

    /// 十字は、1つの十字の形に描く。押している方向だけ、色を付ける。矢印は、三角で描く
    private func drawDPad(ctx: CGContext, held: Int, d: CGFloat, accent: UIColor) {
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

        let arms: [(code: Int, dir: CGPoint)] = [
            (Self.up, CGPoint(x: 0, y: -1)),
            (Self.down, CGPoint(x: 0, y: 1)),
            (Self.left, CGPoint(x: -1, y: 0)),
            (Self.right, CGPoint(x: 1, y: 0)),
        ]
        for arm in arms {
            let box = CGRect(x: c.x + arm.dir.x * d - d / 2, y: c.y + arm.dir.y * d - d / 2, width: d, height: d)
            let pressed = held & arm.code != 0
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

    /// 指の位置で押していることになるボタン。十字は、ななめも含む
    private func mask(at p: CGPoint) -> Int {
        var result = 0
        let dx = p.x - dpadCenter.x
        let dy = p.y - dpadCenter.y
        if hypot(dx, dy) <= unit * 1.9 {
            let t = unit * 0.3
            let ax = abs(dx)
            let ay = abs(dy)
            // ほぼ真横、ほぼ真上のときは、その方向だけ。ななめに寄ったときは、2方向
            if ax > t && ay < ax * 2 { result |= dx < 0 ? Self.left : Self.right }
            if ay > t && ax < ay * 2 { result |= dy < 0 ? Self.up : Self.down }
        }
        if let item = items.first(where: { $0.contains(p) }) {
            result |= item.code
        }
        return result
    }

    private func stickSide(of id: ObjectIdentifier) -> Side? {
        if leftStick.touch == id { return .left }
        if rightStick.touch == id { return .right }
        return nil
    }

    /// 触り始めた指が、スティックになるか。板の左半分は左スティック、右半分は右スティック。
    /// スティックの範囲の外や、ほかのボタンの上では、ならない
    private func stickToStart(at p: CGPoint) -> Side? {
        let side: Side = p.x < bounds.midX ? .left : .right
        let rest = side == .left ? restLeft : restRight
        let taken = side == .left ? leftStick.touch != nil : rightStick.touch != nil
        guard !taken, hypot(p.x - rest.x, p.y - rest.y) <= stickRadius * 1.25, mask(at: p) == 0 else { return nil }
        return side
    }

    private func updateStick(_ side: Side, at p: CGPoint) {
        var s = side == .left ? leftStick : rightStick
        let travel = stickRadius * 2 * Self.travelFactor
        var vx = (p.x - s.origin.x) / travel
        var vy = (p.y - s.origin.y) / travel
        let len = hypot(vx, vy)
        if len > 1 {
            vx /= len
            vy /= len
        }
        s.value = CGPoint(x: vx, y: vy)
        if side == .left { leftStick = s } else { rightStick = s }
    }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        let before = heldMask
        for touch in touches {
            let id = ObjectIdentifier(touch)
            let p = touch.location(in: self)
            if let side = stickToStart(at: p) {
                var s = StickState()
                s.touch = id
                s.origin = p
                if side == .left { leftStick = s } else { rightStick = s }
            } else {
                setMask(id, mask(at: p))
            }
        }
        changed(from: before)
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        let before = heldMask
        for touch in touches {
            let id = ObjectIdentifier(touch)
            let p = touch.location(in: self)
            if let side = stickSide(of: id) {
                updateStick(side, at: p)
            } else {
                // 画面の外に出たら、離したことにする
                setMask(id, bounds.contains(p) ? mask(at: p) : 0)
            }
        }
        changed(from: before)
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) {
        let before = heldMask
        for touch in touches {
            let id = ObjectIdentifier(touch)
            if let side = stickSide(of: id) {
                // 離したら 0 に戻す
                if side == .left { leftStick = StickState() } else { rightStick = StickState() }
            }
            setMask(id, 0)
        }
        changed(from: before)
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) {
        touchesEnded(touches, with: event)
    }

    private func setMask(_ id: ObjectIdentifier, _ value: Int) {
        touchMasks[id] = value == 0 ? nil : value
    }

    /// 状態が変わったので、描き直して、PC に送る。新しく押したボタンがあれば、振動させる
    private func changed(from before: Int) {
        let now = heldMask
        if hapticsOn && (now & ~before) != 0 { haptic.impactOccurred() }
        setNeedsDisplay()
        emit()
    }

    private func emit() {
        let held = heldMask
        session?.sendPad(
            buttons: held & 0xFFFF,
            lx: Double(leftStick.value.x), ly: Double(-leftStick.value.y),
            rx: Double(rightStick.value.x), ry: Double(-rightStick.value.y),
            lt: held & Self.lt != 0 ? 1 : 0, rt: held & Self.rt != 0 ? 1 : 0
        )
    }

    /// 触っている分を全部離す。全部 0 の状態を送り、仮想のゲームパッドを取り外す。
    /// 見えなくなったときや、アプリが裏に回ったとき、種類を替えたときに呼ぶ
    func releaseAll() {
        leftStick = StickState()
        rightStick = StickState()
        touchMasks.removeAll()
        session?.sendPad(buttons: 0, lx: 0, ly: 0, rx: 0, ry: 0, lt: 0, rt: 0)
        session?.sendPadOff()
        setNeedsDisplay()
    }
}
