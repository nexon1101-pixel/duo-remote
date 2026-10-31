import AVFoundation
import SwiftUI

/// H264Rendererのレイヤーを、SwiftUIの中に置くための入れ物。タッチ操作も受け付ける。
struct VideoView: UIViewRepresentable {
    let renderer: H264Renderer
    let videoSize: CGSize
    let pointerMode: PointerMode
    let send: @MainActor ([String: Any]) -> Void

    func makeUIView(context: Context) -> VideoContainerView {
        let view = VideoContainerView()
        view.attach(renderer.layer)
        updateUIView(view, context: context)
        return view
    }

    func updateUIView(_ uiView: VideoContainerView, context: Context) {
        uiView.videoSize = videoSize
        uiView.touchInput.mode = pointerMode
        uiView.touchInput.send = send
    }
}

final class VideoContainerView: UIView {
    let touchInput = TouchInput()
    private weak var videoLayer: CALayer?

    /// 映像の大きさ（画素）。変わったら拡大を戻す
    var videoSize: CGSize = .zero {
        didSet {
            guard videoSize != oldValue else { return }
            zoomedRect = nil
            layoutVideo()
        }
    }

    /// 拡大しているときの、映像を置く範囲。nilなら画面に収める
    private var zoomedRect: CGRect?
    private var lastBounds = CGRect.zero

    /// 拡大の上限（画面に収めたときの何倍か）
    static let maxZoom: CGFloat = 6

    override init(frame: CGRect) {
        super.init(frame: frame)
        touchInput.install(on: self)
        observeZoom()
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        touchInput.install(on: self)
        observeZoom()
    }

    /// トラックパッドのピンチ（映像の拡大・縮小の設定のとき）から、拡大・縮小を頼まれる
    private func observeZoom() {
        NotificationCenter.default.addObserver(
            self, selector: #selector(handleZoomRequest(_:)), name: .duoZoomVideo, object: nil
        )
    }

    @objc private func handleZoomRequest(_ note: Notification) {
        guard let factor = note.userInfo?["factor"] as? CGFloat, factor > 0 else { return }
        zoom(by: factor, around: CGPoint(x: bounds.midX, y: bounds.midY))
    }

    func attach(_ layer: CALayer) {
        layer.frame = bounds
        self.layer.addSublayer(layer)
        videoLayer = layer
    }

    // MARK: - 映像を置く範囲

    /// 縦横比を保って、画面に収めた範囲
    var fitRect: CGRect {
        guard videoSize.width > 0, videoSize.height > 0 else { return bounds }
        return AVMakeRect(aspectRatio: videoSize, insideRect: bounds)
    }

    /// 今、映像を置いている範囲（拡大していれば、画面からはみ出す）
    var videoRect: CGRect { zoomedRect ?? fitRect }

    var isZoomed: Bool { zoomedRect != nil }

    /// pの位置を中心に、factor倍にする
    func zoom(by factor: CGFloat, around p: CGPoint) {
        let current = videoRect
        let fit = fitRect
        guard fit.width > 0, current.width > 0 else { return }
        let scale = min(max(current.width / fit.width * factor, 1), Self.maxZoom)
        if scale < 1.02 {
            zoomedRect = nil
        } else {
            let k = fit.width * scale / current.width
            let rect = CGRect(
                x: p.x - (p.x - current.minX) * k,
                y: p.y - (p.y - current.minY) * k,
                width: fit.width * scale,
                height: fit.height * scale
            )
            zoomedRect = clamped(rect)
        }
        layoutVideo()
    }

    /// 拡大しているとき、映像を動かす
    func pan(by delta: CGPoint) {
        guard let rect = zoomedRect else { return }
        zoomedRect = clamped(rect.offsetBy(dx: delta.x, dy: delta.y))
        layoutVideo()
    }

    /// 拡大しているとき、pが画面の中に入るよう映像を動かす
    func reveal(_ p: CGPoint, margin: CGFloat = 24) {
        guard isZoomed else { return }
        let area = bounds.insetBy(dx: margin, dy: margin)
        var dx: CGFloat = 0
        var dy: CGFloat = 0
        if p.x < area.minX { dx = area.minX - p.x } else if p.x > area.maxX { dx = area.maxX - p.x }
        if p.y < area.minY { dy = area.minY - p.y } else if p.y > area.maxY { dy = area.maxY - p.y }
        if dx != 0 || dy != 0 { pan(by: CGPoint(x: dx, y: dy)) }
    }

    /// 映像が画面より大きい向きは、画面の端に隙間ができないようにする。小さい向きは中央に置く
    private func clamped(_ rect: CGRect) -> CGRect {
        var r = rect
        if r.width <= bounds.width {
            r.origin.x = bounds.midX - r.width / 2
        } else {
            r.origin.x = min(max(r.origin.x, bounds.maxX - r.width), bounds.minX)
        }
        if r.height <= bounds.height {
            r.origin.y = bounds.midY - r.height / 2
        } else {
            r.origin.y = min(max(r.origin.y, bounds.maxY - r.height), bounds.minY)
        }
        return r
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        if bounds != lastBounds {
            // 回したり、キーボードが出たりして大きさが変わったら、拡大を戻す
            lastBounds = bounds
            zoomedRect = nil
        }
        layoutVideo()
    }

    private func layoutVideo() {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        videoLayer?.frame = videoRect
        CATransaction.commit()
    }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        touchInput.stopInertia()
        super.touchesBegan(touches, with: event)
    }
}

extension Notification.Name {
    /// 映像を、拡大・縮小する頼み（userInfo の "factor": 今の大きさに掛ける倍率）。トラックパッドのピンチが送る
    static let duoZoomVideo = Notification.Name("duoZoomVideo")
}
