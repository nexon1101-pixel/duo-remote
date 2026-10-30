import AVFoundation
import SwiftUI

/// H264Rendererのレイヤーを、SwiftUIの中に置くための入れ物。タッチ操作も受け付ける。
struct VideoView: UIViewRepresentable {
    let renderer: H264Renderer
    let videoSize: CGSize
    let send: @MainActor ([String: Any]) -> Void

    func makeUIView(context: Context) -> VideoContainerView {
        let view = VideoContainerView()
        view.attach(renderer.layer)
        updateUIView(view, context: context)
        return view
    }

    func updateUIView(_ uiView: VideoContainerView, context: Context) {
        uiView.touchInput.videoSize = videoSize
        uiView.touchInput.send = send
    }
}

final class VideoContainerView: UIView {
    let touchInput = TouchInput()
    private weak var videoLayer: CALayer?

    override init(frame: CGRect) {
        super.init(frame: frame)
        touchInput.install(on: self)
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        touchInput.install(on: self)
    }

    func attach(_ layer: CALayer) {
        layer.frame = bounds
        self.layer.addSublayer(layer)
        videoLayer = layer
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        videoLayer?.frame = bounds
        CATransaction.commit()
    }
}
