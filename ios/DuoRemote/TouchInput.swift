import AVFoundation
import UIKit

/// 映像の上のタッチを、PCのマウス操作に変えて送る。
/// タップ＝左クリック、長押し＝右クリック、1本指で動かす＝左ボタンを押したまま動かす、2本指で動かす＝スクロール。
@MainActor
final class TouchInput: NSObject {
    /// 2本指でこれだけ動かすと、ホイール1目盛りになる（ポイント）
    static let pointsPerWheelStep: CGFloat = 40
    /// ホイールを送る細かさ（1目盛りの何分の1か）。2の累乗にして、サーバー側の計算で端数が出ないようにする
    static let wheelSubdivisions: CGFloat = 8
    /// 続けてタップしたとき、前と同じ位置とみなす距離と時間。ダブルクリックが位置のずれで外れないようにする
    static let doubleTapDistance: CGFloat = 24
    static let doubleTapInterval: TimeInterval = 0.5

    var videoSize: CGSize = .zero
    var send: @MainActor ([String: Any]) -> Void = { _ in }

    private weak var view: UIView?
    private var lastTap: (point: CGPoint, time: TimeInterval)?
    private var dragging = false
    private var scrollSent = CGPoint.zero // 送り済みのホイール量（wheelSubdivisions 単位）

    func install(on view: UIView) {
        self.view = view
        view.isMultipleTouchEnabled = true

        let tap = UITapGestureRecognizer(target: self, action: #selector(handleTap))
        let longPress = UILongPressGestureRecognizer(target: self, action: #selector(handleLongPress))
        let drag = UIPanGestureRecognizer(target: self, action: #selector(handleDrag))
        drag.maximumNumberOfTouches = 1
        let scroll = UIPanGestureRecognizer(target: self, action: #selector(handleScroll))
        scroll.minimumNumberOfTouches = 2
        scroll.maximumNumberOfTouches = 2

        for recognizer in [tap, longPress, drag, scroll] {
            view.addGestureRecognizer(recognizer)
        }
    }

    // MARK: - 位置の変換

    /// 画面上の位置を、映像の中の位置（左上0、右下1）に変える。
    /// clampがfalseのとき、映像の外ならnilを返す。trueのときは、映像の端に寄せる。
    func normalized(_ point: CGPoint, clamp: Bool) -> CGPoint? {
        guard let view, videoSize.width > 0, videoSize.height > 0 else { return nil }
        // H264Rendererは .resizeAspect で表示するので、同じ計算で映像の範囲を出す
        let rect = AVMakeRect(aspectRatio: videoSize, insideRect: view.bounds)
        guard rect.width > 0, rect.height > 0 else { return nil }
        let x = (point.x - rect.minX) / rect.width
        let y = (point.y - rect.minY) / rect.height
        if clamp {
            return CGPoint(x: min(max(x, 0), 1), y: min(max(y, 0), 1))
        }
        guard (0...1).contains(x), (0...1).contains(y) else { return nil }
        return CGPoint(x: x, y: y)
    }

    // MARK: - 操作

    @objc private func handleTap(_ recognizer: UITapGestureRecognizer) {
        guard recognizer.state == .ended else { return }
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

    @objc private func handleLongPress(_ recognizer: UILongPressGestureRecognizer) {
        guard recognizer.state == .began else { return }
        lastTap = nil
        guard let p = normalized(recognizer.location(in: view), clamp: false) else { return }
        click("right", at: p)
    }

    @objc private func handleDrag(_ recognizer: UIPanGestureRecognizer) {
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

    @objc private func handleScroll(_ recognizer: UIPanGestureRecognizer) {
        switch recognizer.state {
        case .began:
            lastTap = nil
            scrollSent = .zero
            // ホイールは、マウスの下にあるウィンドウに届く。先に2本指の中心へマウスを動かす
            moveMouse(to: recognizer.location(in: view))
        case .changed:
            let t = recognizer.translation(in: view)
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
        default:
            break
        }
    }

    // MARK: - 送信

    private func click(_ button: String, at p: CGPoint) {
        sendButton(button, down: true, at: p)
        sendButton(button, down: false, at: nil)
    }

    private func sendButton(_ button: String, down: Bool, at p: CGPoint?) {
        var message: [String: Any] = ["t": "button", "b": button, "d": down ? "down" : "up"]
        if let p {
            message["x"] = Double(p.x)
            message["y"] = Double(p.y)
        }
        send(message)
    }

    private func moveMouse(to location: CGPoint) {
        guard let p = normalized(location, clamp: true) else { return }
        send(["t": "move", "x": Double(p.x), "y": Double(p.y)])
    }
}
