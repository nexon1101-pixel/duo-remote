import SwiftUI
import UIKit

/// ノートパソコンのような、映像とは別の場所に置くトラックパッド。
/// 半開きの Duo の下の画面と、重ねて出す形（OverlayTrackpad）で使う。
///
/// - 1本指で動かす＝カーソルを動かす（速く動かすほど、多めに動く）
/// - 1本指でタップ＝左クリック、2本指でタップ＝右クリック
/// - 2本指で動かす＝スクロール（指を離したあとも、少し流れ続ける）
/// - 長押ししてから動かす＝左ボタンを押したまま動かす（ドラッグ）
/// - パッドの下の3割は、クリックの場所（文字は出さない）。左半分＝左ボタン、右半分＝右ボタン。
///   押している間だけ、ボタンを押した状態にする。押したまま、上のパッドを別の指で動かすと、ドラッグになる
struct TrackpadPad: View {
    /// 制御メッセージを送る（RemoteSession.send と同じ形）
    let send: @MainActor ([String: Any]) -> Void

    /// クリックの場所の、パッド全体に対する高さの割合と、高さの下限（ポイント）
    private static let zoneFraction: CGFloat = 0.3
    private static let zoneMinHeight: CGFloat = 48

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: 16, style: .continuous)
        ZStack {
            Color.clear
                .glass(in: shape)
            GeometryReader { proxy in
                // 上のパッド（カーソルを動かす）と、下のクリックの場所は、重ならないように、縦に分ける。
                // 重なると、クリックの場所を押したとき、下のパッドが、カーソルを動かしてしまう
                let zone = min(max(proxy.size.height * Self.zoneFraction, Self.zoneMinHeight), proxy.size.height * 0.5)
                VStack(spacing: 0) {
                    ZStack {
                        Image(systemName: "rectangle.and.hand.point.up.left")
                            .font(.system(size: 44, weight: .light))
                            .foregroundStyle(.white.opacity(0.18))
                            .allowsHitTesting(false)
                        TrackpadSurface(send: send)
                    }
                    .frame(height: proxy.size.height - zone)
                    ClickZones(send: send)
                        .frame(height: zone)
                }
            }
        }
        .clipShape(shape)
        .contentShape(shape)
    }
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
            .fill(pressed ? Color.white.opacity(0.14) : Color.clear)
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
        coordinator.stopInertia()
    }
}

/// パッドの上のタッチを、マウス操作に変えて送る。動かし方は TouchInput のトラックパッドの方式に合わせる
@MainActor
final class TrackpadCoordinator: NSObject {
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

        for recognizer in [tap, twoFingerTap, longPress, move, scroll] {
            view.addGestureRecognizer(recognizer)
        }
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
            scroll(total: t)
        case .ended:
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
