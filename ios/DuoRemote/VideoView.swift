import AVFoundation
import SwiftUI

/// H264Rendererのレイヤーを、SwiftUIの中に置くための入れ物。
struct VideoView: UIViewRepresentable {
    let renderer: H264Renderer

    func makeUIView(context: Context) -> VideoContainerView {
        let view = VideoContainerView()
        view.attach(renderer.layer)
        return view
    }

    func updateUIView(_ uiView: VideoContainerView, context: Context) {}
}

final class VideoContainerView: UIView {
    private weak var videoLayer: CALayer?

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
