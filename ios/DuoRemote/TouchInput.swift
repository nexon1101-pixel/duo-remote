import UIKit

/// 画面を触ったときの、マウスの動かし方。
enum PointerMode: String, CaseIterable, Identifiable {
    /// 触った場所をクリックする
    case direct
    /// ノートパソコンのトラックパッドのように、指の動きでカーソルを動かす
    case trackpad

    var id: String { rawValue }

    var label: String {
        switch self {
        case .direct: "直接触る"
        case .trackpad: "トラックパッド"
        }
    }
}

/// 映像の上のタッチを、PCのマウス操作に変えて送る。
///
/// どちらの方式でも共通:
/// - 2本指でタップ＝右クリック、3本指でタップ＝中ボタンのクリック
/// - 3本指でスワイプ＝左右: アプリの切り替え（Alt+Tab）、上: タスク ビュー（Win+Tab）、下: デスクトップを表示（Win+D）
/// - 2本指で動かす＝スクロール（指を離したあとも、少し流れ続ける）
/// - ピンチ＝映像の拡大・縮小（PCには送らない）。拡大中に2本指で動かすと、映像が動く
///
/// 直接触る: タップ＝左クリック、長押し＝右クリック、1本指で動かす＝左ボタンを押したまま動かす
/// トラックパッド: 1本指で動かす＝カーソルを動かす、タップ＝左クリック、長押ししてから動かす＝左ボタンを押したまま動かす
@MainActor
final class TouchInput: NSObject, UIGestureRecognizerDelegate, UIPointerInteractionDelegate {
    /// 2本指でこれだけ動かすと、ホイール1目盛りになる（ポイント）
    static let pointsPerWheelStep: CGFloat = 40
    /// ホイールを送る細かさ（1目盛りの何分の1か）。2の累乗にして、サーバー側の計算で端数が出ないようにする
    static let wheelSubdivisions: CGFloat = 8
    /// 続けてタップしたとき、前と同じ位置とみなす距離と時間。ダブルクリックが位置のずれで外れないようにする
    static let doubleTapDistance: CGFloat = 24
    static let doubleTapInterval: TimeInterval = 0.5
    /// トラックパッドのとき、指の動きの何倍カーソルを動かすか
    static let trackpadSpeed: CGFloat = 1.5
    /// 2本指を、スクロールかピンチかに決めるまでの動きの大きさ
    static let scrollThreshold: CGFloat = 12
    static let pinchThreshold: CGFloat = 0.12
    /// 3本指のスワイプとみなす、指を離したときの移動の大きさ（ポイント）
    static let swipeDistance: CGFloat = 60

    var mode: PointerMode = .direct
    var send: @MainActor ([String: Any]) -> Void = { _ in }

    private weak var view: VideoContainerView?
    private var lastTap: (point: CGPoint, time: TimeInterval)?
    private var dragging = false
    private var lastDragTranslation = CGPoint.zero
    private var lastPressLocation = CGPoint.zero
    /// カーソルの位置の目安（映像の中の位置。左上0、右下1）。拡大中にカーソルを画面の中に入れるためだけに使う。
    /// PCには、相対の動き（move_rel）を送るので、実際の位置とずれていても、カーソルは飛ばない
    private var cursor = CGPoint(x: 0.5, y: 0.5)

    private enum TwoFinger { case undecided, scroll, zoom }
    private var twoFinger = TwoFinger.undecided
    private var scrollRecognizer: UIPanGestureRecognizer?
    private var pinchRecognizer: UIPinchGestureRecognizer?
    private var lastScrollTranslation = CGPoint.zero
    private var lastPinchScale: CGFloat = 1
    private var scrollSent = CGPoint.zero // 送り済みのホイール量（wheelSubdivisions 単位）
    private var inertia: Task<Void, Never>?

    private let haptics = UIImpactFeedbackGenerator(style: .medium)

    func install(on view: VideoContainerView) {
        self.view = view
        view.isMultipleTouchEnabled = true

        let tap = UITapGestureRecognizer(target: self, action: #selector(handleTap))
        let twoFingerTap = UITapGestureRecognizer(target: self, action: #selector(handleTwoFingerTap))
        twoFingerTap.numberOfTouchesRequired = 2
        let threeFingerTap = UITapGestureRecognizer(target: self, action: #selector(handleThreeFingerTap))
        threeFingerTap.numberOfTouchesRequired = 3
        // 3本指のスワイプは、指を離したときに方向を決める。指で触れたものだけ（マウスは対象外）
        let threeFingerSwipe = UIPanGestureRecognizer(target: self, action: #selector(handleThreeFingerSwipe))
        threeFingerSwipe.minimumNumberOfTouches = 3
        threeFingerSwipe.maximumNumberOfTouches = 3
        threeFingerSwipe.allowedTouchTypes = [NSNumber(value: UITouch.TouchType.direct.rawValue)]
        // 3本指のタップは、スワイプが始まらないまま指が離れたときだけ成り立つ。
        // パンは、動き出さないまま指が離れるとすぐに failed になるので、タップはほとんど遅れない
        threeFingerTap.require(toFail: threeFingerSwipe)
        let longPress = UILongPressGestureRecognizer(target: self, action: #selector(handleLongPress))
        // マウスで押し続けたときは、右クリックにしない（右クリックは、マウスの右ボタンで行う）
        longPress.allowedTouchTypes = [NSNumber(value: UITouch.TouchType.direct.rawValue)]
        let drag = UIPanGestureRecognizer(target: self, action: #selector(handleDrag))
        drag.maximumNumberOfTouches = 1
        let scroll = UIPanGestureRecognizer(target: self, action: #selector(handleScroll))
        scroll.minimumNumberOfTouches = 2
        scroll.maximumNumberOfTouches = 2
        scroll.delegate = self
        let pinch = UIPinchGestureRecognizer(target: self, action: #selector(handlePinch))
        pinch.delegate = self
        scrollRecognizer = scroll
        pinchRecognizer = pinch

        // --- マウス（AssistiveTouch や、つないだマウス・トラックパッド）
        // 左ボタンのクリックとドラッグは、指のタップとドラッグと同じ扱いで、すでに動く。ここでは、残りを足す
        tap.buttonMaskRequired = .primary
        let rightClick = UITapGestureRecognizer(target: self, action: #selector(handleRightClick))
        rightClick.buttonMaskRequired = .secondary
        rightClick.allowedTouchTypes = [NSNumber(value: UITouch.TouchType.indirectPointer.rawValue)]
        // ホイールと、トラックパッドの2本指のスクロール（指のタッチは受け取らない）
        let pointerScroll = UIPanGestureRecognizer(target: self, action: #selector(handlePointerScroll))
        pointerScroll.allowedTouchTypes = []
        pointerScroll.allowedScrollTypesMask = .all
        // ボタンを押さずに動かしたとき（カーソルが追いかける。マウスのときだけ呼ばれる）
        let hover = UIHoverGestureRecognizer(target: self, action: #selector(handleHover))

        for recognizer in [tap, twoFingerTap, threeFingerTap, threeFingerSwipe, longPress, drag, scroll, pinch, rightClick, pointerScroll, hover] {
            view.addGestureRecognizer(recognizer)
        }
        // iPhoneのカーソルは隠す（映像の中に、PCのカーソルが映るので、二重にならないように）
        view.addInteraction(UIPointerInteraction(delegate: self))
    }

    func pointerInteraction(_ interaction: UIPointerInteraction, styleFor region: UIPointerRegion) -> UIPointerStyle? {
        UIPointerStyle.hidden()
    }

    // MARK: - マウス

    @objc private func handleRightClick(_ recognizer: UITapGestureRecognizer) {
        guard recognizer.state == .ended else { return }
        lastTap = nil
        guard let p = normalized(recognizer.location(in: view), clamp: false) else { return }
        click("right", at: p)
    }

    @objc private func handleHover(_ recognizer: UIHoverGestureRecognizer) {
        switch recognizer.state {
        case .began, .changed:
            moveMouse(to: recognizer.location(in: view))
        default:
            break
        }
    }

    @objc private func handlePointerScroll(_ recognizer: UIPanGestureRecognizer) {
        switch recognizer.state {
        case .began:
            stopInertia()
            scrollSent = .zero
        case .changed:
            scroll(total: recognizer.translation(in: view))
        default:
            break
        }
    }

    /// 3本指が触れているときは、2本指のスクロールとピンチを始めない
    /// （3本の指は同時に着かないことが多く、先に2本が動き出して、スクロールやピンチと取り違えるのを防ぐ）
    func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
        if gestureRecognizer === scrollRecognizer || gestureRecognizer === pinchRecognizer {
            return gestureRecognizer.numberOfTouches < 3
        }
        return true
    }

    /// スクロールとピンチは、同時に受け取ってから、どちらかに決める
    func gestureRecognizer(
        _ gestureRecognizer: UIGestureRecognizer,
        shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer
    ) -> Bool {
        let pair = [gestureRecognizer, other]
        return pair.contains { $0 is UIPinchGestureRecognizer }
            && pair.contains { ($0 as? UIPanGestureRecognizer)?.minimumNumberOfTouches == 2 }
    }

    // MARK: - 位置の変換

    /// 画面上の位置を、映像の中の位置（左上0、右下1）に変える。
    /// clampがfalseのとき、映像の外ならnilを返す。trueのときは、映像の端に寄せる。
    func normalized(_ point: CGPoint, clamp: Bool) -> CGPoint? {
        guard let view, view.videoSize.width > 0, view.videoSize.height > 0 else { return nil }
        let rect = view.videoRect
        guard rect.width > 0, rect.height > 0 else { return nil }
        let x = (point.x - rect.minX) / rect.width
        let y = (point.y - rect.minY) / rect.height
        if clamp {
            return CGPoint(x: min(max(x, 0), 1), y: min(max(y, 0), 1))
        }
        guard (0...1).contains(x), (0...1).contains(y) else { return nil }
        return CGPoint(x: x, y: y)
    }

    // MARK: - タップと長押し

    @objc private func handleTap(_ recognizer: UITapGestureRecognizer) {
        guard recognizer.state == .ended else { return }
        if mode == .trackpad {
            click("left", at: nil) // 今のカーソルの位置で押す
            return
        }
        var location = recognizer.location(in: view)
        let now = ProcessInfo.processInfo.systemUptime
        if let last = lastTap,
           now - last.time < Self.doubleTapInterval,
           hypot(location.x - last.point.x, location.y - last.point.y) < Self.doubleTapDistance {
            location = last.point
        }
        lastTap = (location, now)
        guard let p = normalized(location, clamp: false) else { return }
        click("left", at: p)
    }

    @objc private func handleTwoFingerTap(_ recognizer: UITapGestureRecognizer) {
        guard recognizer.state == .ended else { return }
        clickAtTouch("right", recognizer)
    }

    @objc private func handleThreeFingerTap(_ recognizer: UITapGestureRecognizer) {
        guard recognizer.state == .ended else { return }
        clickAtTouch("middle", recognizer)
    }

    /// 3本指のスワイプ。指を離したときに、移動が 60pt 以上なら、縦と横の大きい方で方向を決める。
    /// 左右はどちらも Alt+Tab（直前のウィンドウとの行き来）にする。右を Alt+Shift+Tab にすると、
    /// いちばん古いウィンドウに飛んでしまい、どれが出るか分かりにくいため
    @objc private func handleThreeFingerSwipe(_ recognizer: UIPanGestureRecognizer) {
        switch recognizer.state {
        case .began:
            lastTap = nil
            stopInertia()
        case .ended:
            let t = recognizer.translation(in: view)
            guard hypot(t.x, t.y) >= Self.swipeDistance else { return }
            let shortcut: Shortcut
            if abs(t.x) > abs(t.y) {
                shortcut = .appSwitch
            } else if t.y < 0 {
                shortcut = .taskView
            } else {
                shortcut = .showDesktop
            }
            haptics.impactOccurred()
            shortcut.perform(send: send)
        default:
            break
        }
    }

    private func clickAtTouch(_ button: String, _ recognizer: UIGestureRecognizer) {
        lastTap = nil
        haptics.impactOccurred()
        if mode == .trackpad {
            click(button, at: nil)
        } else if let p = normalized(recognizer.location(in: view), clamp: false) {
            click(button, at: p)
        }
    }

    @objc private func handleLongPress(_ recognizer: UILongPressGestureRecognizer) {
        let location = recognizer.location(in: view)
        switch (mode, recognizer.state) {
        case (.direct, .began):
            lastTap = nil
            haptics.impactOccurred()
            guard let p = normalized(location, clamp: false) else { return }
            click("right", at: p)
        case (.trackpad, .began):
            // 長押ししてから動かすと、左ボタンを押したまま動かす
            haptics.impactOccurred()
            lastPressLocation = location
            dragging = true
            sendButton("left", down: true, at: nil)
        case (.trackpad, .changed):
            moveCursor(by: CGPoint(x: location.x - lastPressLocation.x, y: location.y - lastPressLocation.y))
            lastPressLocation = location
        case (.trackpad, .ended), (.trackpad, .cancelled), (.trackpad, .failed):
            guard dragging else { return }
            dragging = false
            sendButton("left", down: false, at: nil)
        default:
            break
        }
    }

    // MARK: - 1本指で動かす

    @objc private func handleDrag(_ recognizer: UIPanGestureRecognizer) {
        if mode == .trackpad {
            trackpadDrag(recognizer)
            return
        }
        let current = recognizer.location(in: view)
        switch recognizer.state {
        case .began:
            lastTap = nil
            // 動き出したと判定されるまでに少し動いているので、触れた位置で押す
            let t = recognizer.translation(in: view)
            let start = CGPoint(x: current.x - t.x, y: current.y - t.y)
            guard let p = normalized(start, clamp: false) else { return }
            dragging = true
            sendButton("left", down: true, at: p)
            moveMouse(to: current)
        case .changed:
            if dragging { moveMouse(to: current) }
        case .ended, .cancelled, .failed:
            guard dragging else { return }
            dragging = false
            moveMouse(to: current)
            sendButton("left", down: false, at: nil)
        default:
            break
        }
    }

    private func trackpadDrag(_ recognizer: UIPanGestureRecognizer) {
        let t = recognizer.translation(in: view)
        switch recognizer.state {
        case .began:
            lastDragTranslation = .zero
            fallthrough
        case .changed:
            moveCursor(by: CGPoint(x: t.x - lastDragTranslation.x, y: t.y - lastDragTranslation.y))
            lastDragTranslation = t
        default:
            break
        }
    }

    /// トラックパッドのとき、カーソルを指の動きの分だけ動かす
    private func moveCursor(by delta: CGPoint) {
        guard let view else { return }
        let rect = view.videoRect
        guard rect.width > 0, rect.height > 0 else { return }
        // 表示している範囲の幅・高さを1とした割合で送る
        let dx = delta.x * Self.trackpadSpeed / rect.width
        let dy = delta.y * Self.trackpadSpeed / rect.height
        guard dx != 0 || dy != 0 else { return }
        send(["t": "move_rel", "dx": Double(dx), "dy": Double(dy)])
        cursor.x = min(max(cursor.x + dx, 0), 1)
        cursor.y = min(max(cursor.y + dy, 0), 1)
        // 拡大しているときは、カーソルが画面の外に出ないよう映像を動かす
        view.reveal(CGPoint(x: rect.minX + cursor.x * rect.width, y: rect.minY + cursor.y * rect.height))
    }

    // MARK: - 2本指（スクロールとピンチ）

    @objc private func handleScroll(_ recognizer: UIPanGestureRecognizer) {
        let t = recognizer.translation(in: view)
        switch recognizer.state {
        case .began:
            lastTap = nil
            stopInertia()
            if pinchRecognizer?.state != .changed { twoFinger = .undecided }
            scrollSent = .zero
            lastScrollTranslation = .zero
            if mode == .direct {
                // ホイールは、マウスの下にあるウィンドウに届く。先に2本指の中心へマウスを動かす
                moveMouse(to: recognizer.location(in: view))
            }
        case .changed:
            let delta = CGPoint(x: t.x - lastScrollTranslation.x, y: t.y - lastScrollTranslation.y)
            lastScrollTranslation = t
            switch twoFinger {
            case .zoom:
                view?.pan(by: delta)
            case .scroll:
                scroll(total: t)
            case .undecided:
                if hypot(t.x, t.y) > Self.scrollThreshold {
                    twoFinger = .scroll
                    scroll(total: t)
                }
            }
        case .ended:
            if twoFinger == .scroll {
                startInertia(from: t, velocity: recognizer.velocity(in: view))
            }
            resetTwoFingerIfIdle()
        case .cancelled, .failed:
            resetTwoFingerIfIdle()
        default:
            break
        }
    }

    @objc private func handlePinch(_ recognizer: UIPinchGestureRecognizer) {
        switch recognizer.state {
        case .began:
            lastPinchScale = 1
        case .changed:
            if twoFinger == .undecided, abs(recognizer.scale - 1) > Self.pinchThreshold {
                twoFinger = .zoom
            }
            guard twoFinger == .zoom else { return }
            view?.zoom(by: recognizer.scale / lastPinchScale, around: recognizer.location(in: view))
            lastPinchScale = recognizer.scale
        case .ended, .cancelled, .failed:
            resetTwoFingerIfIdle()
        default:
            break
        }
    }

    private func resetTwoFingerIfIdle() {
        let active: [UIGestureRecognizer.State] = [.began, .changed]
        if !active.contains(scrollRecognizer?.state ?? .possible), !active.contains(pinchRecognizer?.state ?? .possible) {
            twoFinger = .undecided
        }
    }

    /// 指の動きの合計から、まだ送っていない分のホイールを送る
    private func scroll(total t: CGPoint) {
        let scale = Self.wheelSubdivisions / Self.pointsPerWheelStep
        // iPhoneと同じ向きにする: 指を下に動かすと上へ、右に動かすと左へスクロールする
        let total = CGPoint(x: (-t.x * scale).rounded(.towardZero), y: (t.y * scale).rounded(.towardZero))
        let dx = total.x - scrollSent.x
        let dy = total.y - scrollSent.y
        guard dx != 0 || dy != 0 else { return }
        scrollSent = total
        send([
            "t": "scroll",
            "dx": Double(dx / Self.wheelSubdivisions),
            "dy": Double(dy / Self.wheelSubdivisions),
        ])
    }

    /// 指を離したあとも、だんだん遅くしながらスクロールを続ける
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

    private func click(_ button: String, at p: CGPoint?) {
        sendButton(button, down: true, at: p)
        sendButton(button, down: false, at: nil)
    }

    private func sendButton(_ button: String, down: Bool, at p: CGPoint?) {
        var message: [String: Any] = ["t": "button", "b": button, "d": down ? "down" : "up"]
        if let p {
            cursor = p
            message["x"] = Double(p.x)
            message["y"] = Double(p.y)
        }
        send(message)
    }

    private func moveMouse(to location: CGPoint) {
        guard let p = normalized(location, clamp: true) else { return }
        cursor = p
        send(["t": "move", "x": Double(p.x), "y": Double(p.y)])
    }
}
