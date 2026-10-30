import SwiftUI

/// 半開きの Duo の、下の画面。アプリのキーボードを置き、横に余りがあれば、トラックパッドも置く。
/// 下の画面は横に広く縦が短いので、キーボードは高さに合わせた大きさにする
struct DockedInput: View {
    @EnvironmentObject private var session: RemoteSession
    @AppStorage(OverlayKeyboard.layoutKey) private var layout = OverlayKeyboardLayout.us

    /// これより狭いなら、トラックパッドは置かない（ポイント）
    private static let minTrackpadWidth: CGFloat = 140
    private static let spacing: CGFloat = 10

    var body: some View {
        GeometryReader { proxy in
            let size = proxy.size
            let keysHeightPerWidth = OverlayKeyboard.keyAspect * CGFloat(layout.rows.count) / OverlayKeyboardLayout.rowWidth
            // 高さいっぱいにしたときの幅。画面の幅を超えるなら、幅に合わせる
            // キーボードの周りの余白（padding の 6 の両側）
            let inset: CGFloat = 12
            let fullWidth = max(size.height - inset, 1) / keysHeightPerWidth
            let rest = size.width - (fullWidth + inset) - Self.spacing
            let showTrackpad = rest >= Self.minTrackpadWidth
            let keysWidth = showTrackpad ? fullWidth : min(fullWidth, max(size.width - inset, 1))
            let keysHeight = keysWidth * keysHeightPerWidth

            HStack(spacing: Self.spacing) {
                OverlayKeysView(session: session, layout: layout)
                    .frame(width: keysWidth, height: keysHeight)
                    .padding(6)
                    .glass(in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                if showTrackpad {
                    VStack(spacing: 8) {
                        imeButton
                        TrackpadPad(send: session.send)
                    }
                    .frame(maxHeight: keysHeight + 12)
                }
            }
            .frame(width: size.width, height: size.height)
        }
    }

    /// PCのIMEの状態（あ / A）。押すと切り替える
    private var imeButton: some View {
        Button {
            session.toggleIME()
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "globe")
                Text(session.imeOn == true ? "あ" : "A")
                    .font(.callout.bold())
            }
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 8)
            .glass(in: Capsule(), tint: session.imeOn == true ? .accentColor : nil, interactive: true)
        }
        .buttonStyle(.plain)
    }
}
