import SwiftUI
import UIKit

// MARK: - 重ねて出す枠

/// アプリのキーボード。映像の上に重ねて出し、動かす・大きさを変える・透かすことができる。
/// 上の帯を引くと動き、右端のつまみを引くと大きさが変わる。キーの外は、下の映像に触れる。
struct OverlayKeyboard: View {
    let session: RemoteSession
    /// 閉じるボタンを押したとき
    var onClose: () -> Void
    /// 下端に固定するボタンを押したとき
    var onDock: () -> Void = {}

    static let layoutKey = "overlayKeyboardLayout"
    static let opacityKey = "overlayKeyboardOpacity"

    @AppStorage(Self.layoutKey) private var layout = OverlayKeyboardLayout.us
    @AppStorage(Self.opacityKey) private var opacity = 0.8
    /// キーボードの幅（ポイント）。0 のときは、画面の幅に合わせる
    @AppStorage("overlayKeyboardWidth") private var savedWidth = 0.0
    /// キーボードの真ん中の位置（画面の幅・高さを1とした割合）
    @AppStorage("overlayKeyboardX") private var savedX = 0.5
    @AppStorage("overlayKeyboardY") private var savedY = 0.75

    /// 動かしている途中・大きさを変えている途中の値
    @State private var dragStart: CGPoint?
    @State private var resizeStart: CGFloat?

    private static let barHeight: CGFloat = 26
    private static let minWidth: CGFloat = 260
    /// キー1つの、高さと幅の比
    static let keyAspect: CGFloat = 1.05

    var body: some View {
        GeometryReader { proxy in
            let area = proxy.size
            let width = clampedWidth(in: area)
            let keysHeight = width / OverlayKeyboardLayout.rowWidth * Self.keyAspect * CGFloat(layout.rows.count)
            let height = keysHeight + Self.barHeight
            let center = clampedCenter(width: width, height: height, in: area)

            VStack(spacing: 0) {
                bar(width: width, height: height, area: area)
                OverlayKeysView(session: session, layout: layout)
                    .frame(width: width, height: keysHeight)
            }
            .padding(.horizontal, 4)
            .padding(.bottom, 4)
            .glass(in: RoundedRectangle(cornerRadius: 20, style: .continuous))
            .opacity(opacity)
            .frame(width: width + 8, height: height + 4)
            .position(center)
        }
    }

    /// 上の帯。引くと動く。閉じるボタンと、大きさを変えるつまみ
    private func bar(width: CGFloat, height: CGFloat, area: CGSize) -> some View {
        HStack(spacing: 0) {
            Button(action: onClose) {
                Image(systemName: "xmark")
                    .font(.system(size: 11, weight: .bold))
                    .frame(width: 22, height: 22)
                    .background(Color.white.opacity(0.15), in: Circle())
                    .frame(width: 40, height: Self.barHeight)
            }
            Button(action: onDock) {
                Image(systemName: "arrow.down.to.line.compact")
                    .font(.system(size: 11, weight: .bold))
                    .frame(width: 22, height: 22)
                    .background(Color.white.opacity(0.15), in: Circle())
                    .frame(width: 32, height: Self.barHeight)
            }
            .accessibilityLabel("下端に固定する")
            Spacer()
            // 引いて動かせることが分かるように、シートと同じつまみを出す
            Capsule()
                .fill(Color.white.opacity(0.45))
                .frame(width: 40, height: 5)
            Text(layout.label)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.white.opacity(0.6))
                .padding(.leading, 6)
            Spacer()
            Image(systemName: "arrow.left.and.right")
                .font(.system(size: 11, weight: .bold))
                .frame(width: 22, height: 22)
                .background(Color.white.opacity(0.15), in: Circle())
                .frame(width: 44, height: Self.barHeight)
                .contentShape(Rectangle())
                .gesture(
                    DragGesture(coordinateSpace: .global)
                        .onChanged { value in
                            let start = resizeStart ?? width
                            resizeStart = start
                            // 真ん中を中心に広がるので、引いた量の2倍だけ幅を変える
                            savedWidth = Double(min(max(start + value.translation.width * 2, Self.minWidth), area.width))
                        }
                        .onEnded { _ in resizeStart = nil }
                )
        }
        .font(.footnote)
        .foregroundStyle(.white)
        .frame(width: width, height: Self.barHeight)
        .contentShape(Rectangle())
        .gesture(
            DragGesture(coordinateSpace: .global)
                .onChanged { value in
                    let start = dragStart ?? clampedCenter(width: width, height: height, in: area)
                    dragStart = start
                    savedX = Double((start.x + value.translation.width) / max(area.width, 1))
                    savedY = Double((start.y + value.translation.height) / max(area.height, 1))
                }
                .onEnded { _ in
                    // 画面の外に出たままにならないよう、収まる位置で覚え直す
                    let c = clampedCenter(width: width, height: height, in: area)
                    savedX = Double(c.x / max(area.width, 1))
                    savedY = Double(c.y / max(area.height, 1))
                    dragStart = nil
                }
        )
    }

    private func clampedWidth(in area: CGSize) -> CGFloat {
        let w = savedWidth > 0 ? CGFloat(savedWidth) : area.width
        return min(max(w, min(Self.minWidth, area.width)), area.width)
    }

    private func clampedCenter(width: CGFloat, height: CGFloat, in area: CGSize) -> CGPoint {
        func clamp(_ v: CGFloat, _ half: CGFloat, _ limit: CGFloat) -> CGFloat {
            half * 2 >= limit ? limit / 2 : min(max(v, half), limit - half)
        }
        return CGPoint(
            x: clamp(CGFloat(savedX) * area.width, width / 2, area.width),
            y: clamp(CGFloat(savedY) * area.height, height / 2, area.height)
        )
    }
}

// MARK: - 下端に固定

/// 普通の iPhone のキーボードのように、画面の下に横いっぱいで出すアプリのキーボード。
/// 広い画面では、背が高くなりすぎないよう、高さの上限に合わせて幅を狭め、真ん中に置く
struct BottomKeyboard: View {
    let session: RemoteSession
    /// 高さの上限（帯と、下の余白を除く）
    let maxKeysHeight: CGFloat
    /// ホームの線の分の余白
    let bottomInset: CGFloat
    /// 浮かせるボタンを押したとき
    var onUndock: () -> Void

    @AppStorage(OverlayKeyboard.layoutKey) private var layout = OverlayKeyboardLayout.us
    @AppStorage(OverlayKeyboard.opacityKey) private var opacity = 0.8

    private static let barHeight: CGFloat = 28

    var body: some View {
        // キーの縦横の比（横幅1に対する高さ）
        let ratio = OverlayKeyboard.keyAspect * CGFloat(layout.rows.count) / OverlayKeyboardLayout.rowWidth

        VStack(spacing: 0) {
            HStack {
                Text(layout.label)
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.white.opacity(0.6))
                Spacer()
                Button(action: onUndock) {
                    Image(systemName: "arrow.up.and.down.and.arrow.left.and.right")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(.white)
                        .frame(width: 22, height: 22)
                        .background(Color.white.opacity(0.15), in: Circle())
                }
                .accessibilityLabel("浮かせて、動かせるようにする")
            }
            .padding(.horizontal, 14)
            .frame(height: Self.barHeight)
            // 幅は、渡された数字ではなく、実際に使える幅に合わせる（広すぎて、左右のキーが画面の外に出ないように）。
            // 高さは、その幅と、キーの縦横の比で決まる。高さの上限を超えるときは、幅を狭めて、真ん中に置く
            OverlayKeysView(session: session, layout: layout)
                .aspectRatio(1 / ratio, contentMode: .fit)
                .frame(maxWidth: .infinity)
                .frame(maxHeight: maxKeysHeight)
                .padding(.horizontal, 6)
        }
        .padding(.bottom, 6 + bottomInset)
        .frame(maxWidth: .infinity)
        .glass(in: UnevenRoundedRectangle(topLeadingRadius: 22, topTrailingRadius: 22, style: .continuous))
        .opacity(opacity)
    }
}

// MARK: - キーの並び

struct OverlayKeysView: UIViewRepresentable {
    let session: RemoteSession
    let layout: OverlayKeyboardLayout
    /// IME の状態が変わったら、キーの表示（あ / A）を描き直すために、ここで見ておく
    @EnvironmentObject private var observed: RemoteSession

    func makeUIView(context: Context) -> OverlayKeysUIView {
        let view = OverlayKeysUIView()
        view.session = session
        view.layout = layout
        view.imeOn = observed.imeOn
        return view
    }

    func updateUIView(_ uiView: OverlayKeysUIView, context: Context) {
        uiView.session = session
        uiView.imeOn = observed.imeOn
        if uiView.layout != layout {
            uiView.layout = layout
        }
    }

    static func dismantleUIView(_ uiView: OverlayKeysUIView, coordinator: ()) {
        uiView.releaseAll()
    }
}

/// キーを描いて、タッチを受け取る。何本の指でも同時に押せる（Ctrl を押したまま C、など）。
///
/// 修飾キー（Shift、Ctrl、Alt、Win、Fn）の使い方:
/// - 押したまま、ほかのキーを押す: 一緒に押したことになる
/// - 1回たたく: 次のキー1つだけ、一緒に押したことになる
/// - 2回続けてたたく: 固定。もう一度たたくまで、押したまま
/// - Win だけは、1回たたくと、Windowsキーを押して離す（スタートメニュー）
final class OverlayKeysUIView: UIView {
    weak var session: RemoteSession?
    /// PC の IME の状態（あ / A のキーに出す）。変わったら描き直す
    var imeOn: Bool? {
        didSet { if imeOn != oldValue { setNeedsDisplay() } }
    }
    var layout: OverlayKeyboardLayout = .us {
        didSet {
            releaseAll()
            setNeedsLayout()
            setNeedsDisplay()
        }
    }

    private enum ModifierState {
        /// 指で押している。usedは、押している間にほかのキーを押したか
        case held(used: Bool)
        /// 1回たたいた。次のキー1つだけ
        case oneShot
        /// 2回たたいた。もう一度たたくまで
        case locked
    }

    /// キーの位置（段、列）と、画面の上の四角
    private var frames: [(row: Int, col: Int, rect: CGRect)] = []
    /// 指ごとの、押しているキーの位置
    private var touchKeys: [ObjectIdentifier: (row: Int, col: Int)] = [:]
    /// 普通のキーを押している指ごとの、送ったスキャンコードと、繰り返し
    private var touchScans: [ObjectIdentifier: (sc: Int, repeating: Task<Void, Never>)] = [:]
    private var modifiers: [OverlayModifier: ModifierState] = [:]
    /// PCに「押した」と送ってある修飾キー（スキャンコード）
    private var sentModifiers: [OverlayModifier: Int] = [:]
    /// 1回たたいて oneShot にしたときの時刻（続けてたたいたら固定にする）
    private var lastTap: [OverlayModifier: Date] = [:]
    private let haptic = UIImpactFeedbackGenerator(style: .light)

    private static let repeatDelay: Duration = .milliseconds(450)
    private static let repeatInterval: Duration = .milliseconds(33)
    private static let doubleTap: TimeInterval = 0.35
    private static let gap: CGFloat = 4

    override init(frame: CGRect) {
        super.init(frame: frame)
        isMultipleTouchEnabled = true
        isOpaque = false
        backgroundColor = .clear
        contentMode = .redraw
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        if window == nil { releaseAll() }
    }

    // MARK: 並べる

    override func layoutSubviews() {
        super.layoutSubviews()
        let rows = layout.rows
        guard !rows.isEmpty else { return }
        let unit = bounds.width / OverlayKeyboardLayout.rowWidth
        let rowHeight = bounds.height / CGFloat(rows.count)
        frames = []
        for (r, row) in rows.enumerated() {
            var x: CGFloat = 0
            for (c, key) in row.enumerated() {
                let w = key.width * unit
                let rect = CGRect(x: x, y: CGFloat(r) * rowHeight, width: w, height: rowHeight)
                frames.append((row: r, col: c, rect: rect))
                x += w
            }
        }
        setNeedsDisplay()
    }

    private func key(at point: CGPoint) -> (row: Int, col: Int)? {
        frames.first { $0.rect.contains(point) }.map { (row: $0.row, col: $0.col) }
    }

    private func isActive(_ m: OverlayModifier) -> Bool { modifiers[m] != nil }

    // MARK: 描く

    override func draw(_ rect: CGRect) {
        let rows = layout.rows
        let fn = isActive(.fn)
        let shift = isActive(.shift)
        let pressed = Set(touchKeys.values.map { "\($0.row),\($0.col)" })
        let unit = bounds.width / OverlayKeyboardLayout.rowWidth
        let mainFont = UIFont.systemFont(ofSize: max(unit * 0.36, 9), weight: .medium)
        let smallFont = UIFont.systemFont(ofSize: max(unit * 0.24, 7))
        // 記号（`、;、, など）は、字が小さくて見つけにくいので、大きく太くする
        let symbolFont = UIFont.systemFont(ofSize: max(unit * 0.46, 11), weight: .semibold)

        for f in frames {
            let key = rows[f.row][f.col]
            let box = f.rect.insetBy(dx: Self.gap / 2, dy: Self.gap / 2)
            let state = key.modifier.flatMap { modifiers[$0] }
            let fill: UIColor
            if pressed.contains("\(f.row),\(f.col)") {
                fill = UIColor(white: 1, alpha: 0.5)
            } else if key.ime && imeOn == true {
                fill = UIColor.systemBlue.withAlphaComponent(0.6)
            } else if case .locked? = state {
                fill = .systemBlue
            } else if state != nil {
                fill = UIColor.systemBlue.withAlphaComponent(0.6)
            } else if key.modifier != nil || key.width > 1 {
                fill = UIColor(white: 1, alpha: 0.09)
            } else {
                fill = UIColor(white: 1, alpha: 0.2)
            }
            let radius = min(8, box.height * 0.2)
            // 下に薄い影を付けて、キーが少し浮いて見えるようにする
            UIColor(white: 0, alpha: 0.25).setFill()
            UIBezierPath(roundedRect: box.offsetBy(dx: 0, dy: 1), cornerRadius: radius).fill()
            fill.setFill()
            UIBezierPath(roundedRect: box, cornerRadius: radius).fill()

            let main: String
            var small: String?
            if key.ime {
                main = imeOn == true ? "あ" : "A"
            } else if fn, let alt = key.fn {
                main = alt.label
            } else if shift, let s = key.shiftLabel {
                main = s
                small = key.label
            } else {
                main = key.label
                small = key.shiftLabel
            }
            let isSymbol = main.count == 1 && !(main.first?.isLetter ?? true) && !(main.first?.isNumber ?? true)
            let font = isSymbol ? symbolFont : mainFont
            if let small {
                drawLabel(small, font: smallFont, color: UIColor(white: 1, alpha: 0.55),
                     in: CGRect(x: box.minX, y: box.minY + 2, width: box.width, height: box.height * 0.35))
                drawLabel(main, font: font, color: .white,
                     in: CGRect(x: box.minX, y: box.minY + box.height * 0.3, width: box.width, height: box.height * 0.7))
            } else {
                drawLabel(main, font: font, color: .white, in: box)
            }
        }
    }

    private func drawLabel(_ text: String, font: UIFont, color: UIColor, in box: CGRect) {
        let style = NSMutableParagraphStyle()
        style.alignment = .center
        style.lineBreakMode = .byClipping
        let attrs: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: color, .paragraphStyle: style]
        let size = (text as NSString).size(withAttributes: attrs)
        let r = CGRect(x: box.minX, y: box.midY - size.height / 2, width: box.width, height: size.height)
        (text as NSString).draw(in: r, withAttributes: attrs)
    }

    // MARK: 触る

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        for touch in touches {
            guard let pos = key(at: touch.location(in: self)) else { continue }
            let id = ObjectIdentifier(touch)
            touchKeys[id] = pos
            let key = layout.rows[pos.row][pos.col]
            haptic.impactOccurred()
            if key.ime {
                session?.toggleIME()
            } else if let m = key.modifier {
                modifierDown(m, sc: key.sc)
            } else {
                keyDown(key, touch: id)
            }
        }
        setNeedsDisplay()
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) {
        for touch in touches {
            let id = ObjectIdentifier(touch)
            guard let pos = touchKeys.removeValue(forKey: id) else { continue }
            let key = layout.rows[pos.row][pos.col]
            if let m = key.modifier {
                modifierUp(m, sc: key.sc)
            } else {
                keyUp(touch: id)
            }
        }
        setNeedsDisplay()
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) {
        touchesEnded(touches, with: event)
    }

    private func modifierDown(_ m: OverlayModifier, sc: Int?) {
        switch modifiers[m] {
        case .locked?:
            // 固定を外す（指を離したときは、何もしない）
            modifiers[m] = nil
            sendModifierUp(m)
        case .oneShot?:
            if let t = lastTap[m], Date().timeIntervalSince(t) < Self.doubleTap {
                modifiers[m] = .locked
            } else {
                // しばらくたってから、もう一度たたいたら、やめる
                modifiers[m] = nil
                sendModifierUp(m)
            }
        default:
            modifiers[m] = .held(used: false)
        }
    }

    private func modifierUp(_ m: OverlayModifier, sc: Int?) {
        guard case .held(let used)? = modifiers[m] else { return }
        if used {
            modifiers[m] = nil
            sendModifierUp(m)
        } else if m == .win, let sc {
            // Win だけは、たたくと、Windowsキーを押して離す
            modifiers[m] = nil
            session?.sendScan(sc, down: true)
            session?.sendScan(sc, down: false)
        } else {
            modifiers[m] = .oneShot
            lastTap[m] = Date()
        }
    }

    private func keyDown(_ key: OverlayKey, touch: ObjectIdentifier) {
        let sc: Int
        if isActive(.fn), let alt = key.fn {
            sc = alt.sc
        } else if let s = key.sc {
            sc = s
        } else {
            return
        }
        // 効いている修飾キーを、先に押す
        let rows = layout.rows
        for (m, state) in modifiers where m != .fn {
            if case .held = state { modifiers[m] = .held(used: true) }
            guard sentModifiers[m] == nil,
                  let msc = rows.joined().first(where: { $0.modifier == m })?.sc else { continue }
            session?.sendScan(msc, down: true)
            sentModifiers[m] = msc
        }
        if case .held? = modifiers[.fn] { modifiers[.fn] = .held(used: true) }

        session?.sendScan(sc, down: true)
        let repeating = Task { [weak self] in
            try? await Task.sleep(for: Self.repeatDelay)
            while !Task.isCancelled {
                self?.session?.sendScan(sc, down: true)
                try? await Task.sleep(for: Self.repeatInterval)
            }
        }
        touchScans[touch] = (sc, repeating)
    }

    private func keyUp(touch: ObjectIdentifier) {
        guard let pressed = touchScans.removeValue(forKey: touch) else { return }
        pressed.repeating.cancel()
        session?.sendScan(pressed.sc, down: false)
        // 1回たたいた修飾キーは、キー1つで終わり
        for (m, state) in modifiers {
            if case .oneShot = state {
                modifiers[m] = nil
                sendModifierUp(m)
            }
        }
    }

    private func sendModifierUp(_ m: OverlayModifier) {
        if let sc = sentModifiers.removeValue(forKey: m) {
            session?.sendScan(sc, down: false)
        }
    }

    /// 押しているキーを、全部離す。閉じたときや、配列を替えたときに呼ぶ
    func releaseAll() {
        for (_, pressed) in touchScans {
            pressed.repeating.cancel()
            session?.sendScan(pressed.sc, down: false)
        }
        touchScans.removeAll()
        touchKeys.removeAll()
        for m in Array(sentModifiers.keys) { sendModifierUp(m) }
        modifiers.removeAll()
        setNeedsDisplay()
    }
}
