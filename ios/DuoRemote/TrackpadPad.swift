import SwiftUI
import UIKit

/// ノートパソコンのような、映像とは別の場所に置くトラックパッド。
/// 半開きの Duo の下の画面と、重ねて出す形（OverlayTrackpad）で使う。
///
/// - 1本指で動かす＝カーソルを動かす（速く動かすほど、多めに動く）
/// - 1本指でタップ＝左クリック、2本指でタップ＝右クリック
/// - 2本指で動かす＝スクロール（指を離したあとも、少し流れ続ける）
/// - 右端の細い帯を、1本指で上下になぞる＝スクロール
/// - 長押ししてから動かす＝左ボタンを押したまま動かす（ドラッグ）
/// - クリックの場所（文字は出さず、薄い線で分ける）。押している間だけ、ボタンを押した状態にする。
///   押したまま、パッドを別の指で動かすと、ドラッグになる。場所は、使い方で変える（ClickZoneLayout）
///   - 半開きの下の画面: 左端が左ボタン、右端が右ボタン（両手の親指で持つ形に合わせる）
///   - 重ねて出す形: パッドの下の3割を、左半分と右半分に分ける
struct TrackpadPad: View {
    /// 制御メッセージを送る（RemoteSession.send と同じ形）
    let send: @MainActor ([String: Any]) -> Void
    /// クリックの場所を、どこに置くか
    var zones: ClickZoneLayout = .bottom

    /// 下に置くときの、パッド全体に対する高さの割合と、高さの下限（ポイント）
    private static let zoneFraction: CGFloat = 0.3
    private static let zoneMinHeight: CGFloat = 48
    /// 左右の端に置くときの、パッド全体に対する幅の割合と、幅の下限（ポイント）
    private static let sideFraction: CGFloat = 0.2
    private static let sideMinWidth: CGFloat = 56
    /// スクロールの帯の幅（ポイント）
    private static let scrollStripWidth: CGFloat = 40

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: 16, style: .continuous)
        ZStack {
            Color.clear
                .glass(in: shape)
            // カーソルを動かす部分と、クリックの場所は、重ならないように分ける。
            // 重なると、クリックの場所を押したとき、下のパッドが、カーソルを動かしてしまう
            GeometryReader { proxy in
                switch zones {
                case .bottom:
                    let zone = min(max(proxy.size.height * Self.zoneFraction, Self.zoneMinHeight), proxy.size.height * 0.5)
                    VStack(spacing: 0) {
                        movePad.frame(height: proxy.size.height - zone)
                        ClickZones(send: send)
                            .frame(height: zone)
                    }
                case .sides:
                    let zone = min(max(proxy.size.width * Self.sideFraction, Self.sideMinWidth), proxy.size.width * 0.3)
                    HStack(spacing: 0) {
                        ClickZone(button: "left", send: send)
                            .frame(width: zone)
                        divider(vertical: true)
                        movePad
                        divider(vertical: true)
                        ClickZone(button: "right", send: send)
                            .frame(width: zone)
                    }
                }
            }
        }
        .clipShape(shape)
        .contentShape(shape)
    }

    /// カーソルを動かす部分（アイコンと、タッチを受け取る面）
    private var movePad: some View {
        HStack(spacing: 0) {
            ZStack {
                Image(systemName: "rectangle.and.hand.point.up.left")
                    .font(.system(size: 44, weight: .light))
                    .foregroundStyle(.white.opacity(0.18))
                    .allowsHitTesting(false)
                TrackpadSurface(send: send)
            }
            // 右端は、1本指でスクロールする細い帯。2本指のスクロールが難しい、小さいパッドでも使える
            divider(vertical: true)
            ScrollStrip(send: send)
                .frame(width: Self.scrollStripWidth)
        }
    }

    private func divider(vertical: Bool) -> some View {
        Rectangle()
            .fill(Color.white.opacity(0.14))
            .frame(width: vertical ? 1 : nil, height: vertical ? nil : 1)
    }
}

/// パッドの右端の、1本指でスクロールする帯。上下になぞると、ホイールを回す。
/// 向きは、2本指のスクロールと同じ（指を下に動かすと、画面が上へ動く）
private struct ScrollStrip: View {
    let send: @MainActor ([String: Any]) -> Void

    @State private var sent: CGFloat = 0 // 送り済みのホイール量（wheelSubdivisions 単位）
    @State private var active = false

    /// 指の動きに対する、スクロール量の倍率（帯が短いので、2本指より少し多めにする）
    private static let gain: CGFloat = 1.5

    var body: some View {
        ZStack {
            (active ? Color.accentColor.opacity(0.2) : Color.white.opacity(0.04))
            VStack(spacing: 10) {
                Image(systemName: "chevron.up")
                Image(systemName: "arrow.up.and.down")
                Image(systemName: "chevron.down")
            }
            .font(.system(size: 12, weight: .semibold))
            .foregroundStyle(active ? Color.accentColor : Color.white.opacity(0.3))
            .allowsHitTesting(false)
        }
        .animation(.easeOut(duration: 0.12), value: active)
        .contentShape(Rectangle())
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { value in
                    active = true
                    let scale = TouchInput.wheelSubdivisions / TouchInput.pointsPerWheelStep * Self.gain
                    let total = (value.translation.height * scale).rounded(.towardZero)
                    let delta = total - sent
                    guard delta != 0 else { return }
                    sent = total
                    send(["t": "scroll", "dx": 0.0, "dy": Double(delta / TouchInput.wheelSubdivisions)])
                }
                .onEnded { _ in
                    active = false
                    sent = 0
                }
        )
        .accessibilityLabel("スクロール")
    }
}

/// クリックの場所の置き方
enum ClickZoneLayout {
    /// パッドの下の3割を、左半分と右半分に分ける
    case bottom
    /// パッドの左端と右端
    case sides
}

/// パッドの下の、左右のクリックの場所。文字は出さず、薄い線で分ける。押している間は、薄く光る
private struct ClickZones: View {
    let send: @MainActor ([String: Any]) -> Void

    var body: some View {
        HStack(spacing: 0) {
            ClickZone(button: "left", send: send)
            Rectangle()
                .fill(Color.white.opacity(0.14))
                .frame(width: 1)
            ClickZone(button: "right", send: send)
        }
        .overlay(alignment: .top) {
            Rectangle()
                .fill(Color.white.opacity(0.14))
                .frame(height: 1)
        }
    }
}

/// クリックの場所の1つ。押している間だけ、ボタンを押した状態にする
private struct ClickZone: View {
    let button: String
    let send: @MainActor ([String: Any]) -> Void

    @State private var pressed = false
    private let haptics = UIImpactFeedbackGenerator(style: .light)

    var body: some View {
        Rectangle()
            .fill(pressed ? Color.accentColor.opacity(0.28) : Color.clear)
            .overlay {
                // 左右が分かるように、薄いアイコンを出す。押している間は、明るくして色を付ける
                Image(systemName: button == "left" ? "cursorarrow.click" : "contextualmenu.and.cursorarrow")
                    .font(.system(size: 16, weight: .regular))
                    .foregroundStyle(pressed ? Color.accentColor : Color.white.opacity(0.28))
                    .allowsHitTesting(false)
            }
            .animation(.easeOut(duration: 0.12), value: pressed)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { _ in
                        guard !pressed else { return }
                        pressed = true
                        haptics.impactOccurred()
                        send(["t": "button", "b": button, "d": "down"])
                    }
                    .onEnded { _ in
                        guard pressed else { return }
                        pressed = false
                        send(["t": "button", "b": button, "d": "up"])
                    }
            )
            .onDisappear {
                // 押したまま消えたときに、PCのボタンが押されたままにならないようにする
                guard pressed else { return }
                pressed = false
                send(["t": "button", "b": button, "d": "up"])
            }
            .accessibilityLabel(button == "left" ? "左クリック" : "右クリック")
            .accessibilityAddTraits(.isButton)
    }
}

/// タッチを受け取る UIView。見た目は持たない
private struct TrackpadSurface: UIViewRepresentable {
    let send: @MainActor ([String: Any]) -> Void

    func makeCoordinator() -> TrackpadCoordinator {
        TrackpadCoordinator()
    }

    func makeUIView(context: Context) -> UIView {
        let view = UIView()
        view.backgroundColor = .clear
        context.coordinator.send = send
        context.coordinator.install(on: view)
        return view
    }

    func updateUIView(_ uiView: UIView, context: Context) {
        context.coordinator.send = send
    }

    static func dismantleUIView(_ uiView: UIView, coordinator: TrackpadCoordinator) {
        coordinator.releaseHeld()
    }
}

/// パッドの上のタッチを、マウス操作に変えて送る。動かし方は TouchInput のトラックパッドの方式に合わせる
@MainActor
final class TrackpadCoordinator: NSObject, UIGestureRecognizerDelegate {
    /// ピンチの使い方（設定）。video: 映像の拡大・縮小（iPhone の中だけ）、pc: PC に Ctrl+ホイールを送る
    static let pinchModeKey = "trackpadPinchMode"
    /// pc のとき、ホイール1目盛りにする、ピンチの変わり具合（1.12 倍ごと）
    private static let pinchStep: CGFloat = 1.12
    /// ピンチとして動き出す、ピンチの変わり具合（これより小さいうちは、スクロールとして扱う）
    private static let pinchStart: CGFloat = 0.06
    private static let vkControl = 0x11

    /// パッドの幅のこの倍数を、「表示している範囲の幅」1として割合にする。
    /// 2.5 にすると、ゆっくり動かしたとき、パッドの端から端で画面の幅の0.4ほど動く
    static let referenceWidthFactor: CGFloat = 2.5
    /// 映像の縦横の比（高さ÷幅）。縦の動きの割合を、横と同じ長さの動きになるよう直すのに使う
    static let videoAspect: CGFloat = 9.0 / 16.0
    /// 加速の倍率の範囲と、加速が始まる速さ・倍率が上限になる速さ（ポイント／秒）
    static let minGain: CGFloat = 1
    static let maxGain: CGFloat = 2.5
    static let gainStartSpeed: CGFloat = 200
    static let gainFullSpeed: CGFloat = 2000
    /// 長押しでドラッグを始めるまでの時間（秒）
    static let dragHoldDuration: TimeInterval = 0.35

    var send: @MainActor ([String: Any]) -> Void = { _ in }

    private weak var view: UIView?
    private let haptics = UIImpactFeedbackGenerator(style: .light)

    // 1本指の動き
    private var lastMoveTranslation = CGPoint.zero
    private var lastMoveTime: TimeInterval = 0
    private var smoothedSpeed: CGFloat = 0
    // ドラッグ（長押ししてから動かす）
    private var dragging = false
    private var lastPressLocation = CGPoint.zero
    // 2本指のスクロール
    private var lastScrollTranslation = CGPoint.zero
    private var scrollSent = CGPoint.zero // 送り済みのホイール量（wheelSubdivisions 単位）
    private var inertia: Task<Void, Never>?
    private var scrollRecognizer: UIPanGestureRecognizer?
    private var pinchRecognizer: UIPinchGestureRecognizer?
    /// ピンチの途中か。動き出したあとは、2本指のスクロールを送らない
    private var pinching = false
    /// 前に反映した、ピンチの拡大率
    private var pinchApplied: CGFloat = 1
    /// pc のとき、Ctrl を押しているか（途中で消えたときに、離すため）
    private var controlDown = false

    func install(on view: UIView) {
        self.view = view
        view.isMultipleTouchEnabled = true

        let tap = UITapGestureRecognizer(target: self, action: #selector(handleTap))
        let twoFingerTap = UITapGestureRecognizer(target: self, action: #selector(handleTwoFingerTap))
        twoFingerTap.numberOfTouchesRequired = 2
        let longPress = UILongPressGestureRecognizer(target: self, action: #selector(handleLongPress))
        longPress.minimumPressDuration = Self.dragHoldDuration
        let move = UIPanGestureRecognizer(target: self, action: #selector(handleMove))
        move.minimumNumberOfTouches = 1
        move.maximumNumberOfTouches = 1
        let scroll = UIPanGestureRecognizer(target: self, action: #selector(handleScroll))
        scroll.minimumNumberOfTouches = 2
        scroll.maximumNumberOfTouches = 2

        let pinch = UIPinchGestureRecognizer(target: self, action: #selector(handlePinch))
        // スクロールとピンチは、同時に受け取ってから、動きの大きい方に決める
        scroll.delegate = self
        pinch.delegate = self
        scrollRecognizer = scroll
        pinchRecognizer = pinch

        for recognizer in [tap, twoFingerTap, longPress, move, scroll, pinch] {
            view.addGestureRecognizer(recognizer)
        }
    }

    func gestureRecognizer(
        _ gestureRecognizer: UIGestureRecognizer,
        shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer
    ) -> Bool {
        (gestureRecognizer === scrollRecognizer && other === pinchRecognizer)
            || (gestureRecognizer === pinchRecognizer && other === scrollRecognizer)
    }

    // MARK: - ピンチ

    @objc private func handlePinch(_ recognizer: UIPinchGestureRecognizer) {
        switch recognizer.state {
        case .began:
            pinching = false
            pinchApplied = 1
        case .changed:
            if !pinching {
                guard abs(recognizer.scale - 1) > Self.pinchStart else { return }
                pinching = true
                stopInertia()
            }
            let mode = UserDefaults.standard.string(forKey: Self.pinchModeKey) ?? "video"
            let ratio = recognizer.scale / pinchApplied
            if mode == "pc" {
                // 1.12 倍ごとに、ホイール1目盛り。広げると拡大（上へ）、狭めると縮小（下へ）
                if ratio >= Self.pinchStep {
                    wheelWithControl(up: true)
                    pinchApplied = recognizer.scale
                } else if ratio <= 1 / Self.pinchStep {
                    wheelWithControl(up: false)
                    pinchApplied = recognizer.scale
                }
            } else {
                pinchApplied = recognizer.scale
                NotificationCenter.default.post(name: .duoZoomVideo, object: nil, userInfo: ["factor": ratio])
            }
        default:
            // 終わった・取り消された。Ctrl を押していれば、離す
            pinching = false
            releaseControl()
        }
    }

    /// Ctrl を押したまま、ホイールを1目盛り回す（PC のアプリの拡大・縮小）
    private func wheelWithControl(up: Bool) {
        if !controlDown {
            controlDown = true
            send(["t": "key", "vk": Self.vkControl, "d": "down"])
        }
        send(["t": "scroll", "dx": 0.0, "dy": up ? 1.0 : -1.0])
    }

    private func releaseControl() {
        guard controlDown else { return }
        controlDown = false
        send(["t": "key", "vk": Self.vkControl, "d": "up"])
    }

    /// 消えるときに、押したままのものを離す
    func releaseHeld() {
        stopInertia()
        releaseControl()
    }

    // MARK: - タップと長押し

    @objc private func handleTap(_ recognizer: UITapGestureRecognizer) {
        guard recognizer.state == .ended else { return }
        haptics.impactOccurred()
        click("left")
    }

    @objc private func handleTwoFingerTap(_ recognizer: UITapGestureRecognizer) {
        guard recognizer.state == .ended else { return }
        haptics.impactOccurred()
        click("right")
    }

    /// 長押ししてから動かすと、左ボタンを押したまま動かす
    @objc private func handleLongPress(_ recognizer: UILongPressGestureRecognizer) {
        let location = recognizer.location(in: view)
        switch recognizer.state {
        case .began:
            haptics.impactOccurred()
            lastPressLocation = location
            lastMoveTime = ProcessInfo.processInfo.systemUptime
            smoothedSpeed = 0
            dragging = true
            sendButton("left", down: true)
        case .changed:
            guard dragging else { return }
            moveCursor(by: CGPoint(x: location.x - lastPressLocation.x, y: location.y - lastPressLocation.y))
            lastPressLocation = location
        case .ended, .cancelled, .failed:
            guard dragging else { return }
            dragging = false
            sendButton("left", down: false)
        default:
            break
        }
    }

    // MARK: - 1本指で動かす

    @objc private func handleMove(_ recognizer: UIPanGestureRecognizer) {
        let t = recognizer.translation(in: view)
        switch recognizer.state {
        case .began:
            stopInertia()
            lastMoveTranslation = .zero
            lastMoveTime = ProcessInfo.processInfo.systemUptime
            smoothedSpeed = 0
            fallthrough
        case .changed:
            moveCursor(by: CGPoint(x: t.x - lastMoveTranslation.x, y: t.y - lastMoveTranslation.y))
            lastMoveTranslation = t
        default:
            break
        }
    }

    /// カーソルを、指の動きの分だけ動かす。速いほど多めに動かす
    private func moveCursor(by delta: CGPoint) {
        guard let view, view.bounds.width > 0 else { return }

        // 指の速さ（ポイント／秒）を、少しならして求める
        let now = ProcessInfo.processInfo.systemUptime
        let dt = max(now - lastMoveTime, 1.0 / 240)
        lastMoveTime = now
        let instant = hypot(delta.x, delta.y) / CGFloat(dt)
        smoothedSpeed = smoothedSpeed * 0.6 + instant * 0.4

        let ramp = (smoothedSpeed - Self.gainStartSpeed) / (Self.gainFullSpeed - Self.gainStartSpeed)
        let gain = Self.minGain + (Self.maxGain - Self.minGain) * min(max(ramp, 0), 1)

        // 表示している範囲の幅・高さを1とした割合で送る
        let reference = view.bounds.width * Self.referenceWidthFactor
        let dx = delta.x * gain / reference
        let dy = delta.y * gain / (reference * Self.videoAspect)
        guard dx != 0 || dy != 0 else { return }
        send(["t": "move_rel", "dx": Double(dx), "dy": Double(dy)])
    }

    // MARK: - 2本指（スクロール）

    @objc private func handleScroll(_ recognizer: UIPanGestureRecognizer) {
        let t = recognizer.translation(in: view)
        switch recognizer.state {
        case .began:
            stopInertia()
            scrollSent = .zero
            lastScrollTranslation = .zero
        case .changed:
            lastScrollTranslation = t
            guard !pinching else { return }
            scroll(total: t)
        case .ended:
            guard !pinching else { return }
            startInertia(from: t, velocity: recognizer.velocity(in: view))
        default:
            break
        }
    }

    /// 指の動きの合計から、まだ送っていない分のホイールを送る（TouchInput.scroll と同じ計算）
    private func scroll(total t: CGPoint) {
        let scale = TouchInput.wheelSubdivisions / TouchInput.pointsPerWheelStep
        // iPhoneと同じ向きにする: 指を下に動かすと上へ、右に動かすと左へスクロールする
        let total = CGPoint(x: (-t.x * scale).rounded(.towardZero), y: (t.y * scale).rounded(.towardZero))
        let dx = total.x - scrollSent.x
        let dy = total.y - scrollSent.y
        guard dx != 0 || dy != 0 else { return }
        scrollSent = total
        send([
            "t": "scroll",
            "dx": Double(dx / TouchInput.wheelSubdivisions),
            "dy": Double(dy / TouchInput.wheelSubdivisions),
        ])
    }

    /// 指を離したあとも、だんだん遅くしながらスクロールを続ける（TouchInput.startInertia と同じ）
    private func startInertia(from translation: CGPoint, velocity: CGPoint) {
        guard hypot(velocity.x, velocity.y) > 300 else { return }
        inertia = Task { [weak self] in
            var t = translation
            var v = velocity
            let frame = 1.0 / 60
            while !Task.isCancelled, hypot(v.x, v.y) > 30 {
                try? await Task.sleep(for: .seconds(frame))
                guard let self, !Task.isCancelled else { return }
                t.x += v.x * frame
                t.y += v.y * frame
                self.scroll(total: t)
                v.x *= 0.93
                v.y *= 0.93
            }
        }
    }

    func stopInertia() {
        inertia?.cancel()
        inertia = nil
    }

    // MARK: - 送信

    private func click(_ button: String) {
        sendButton(button, down: true)
        sendButton(button, down: false)
    }

    /// 位置は付けない（今のカーソルの位置で押す）
    private func sendButton(_ button: String, down: Bool) {
        send(["t": "button", "b": button, "d": down ? "down" : "up"])
    }
}
