import SwiftUI

/// 半開きの Duo の、下の画面。アプリのキーボードとトラックパッドを置く。
/// 上の帯のボタンで、キーボードとトラックパッドを、それぞれ出したりしまったりできる。
/// 下の画面は横に広く縦が短いので、キーボードは高さに合わせた大きさにし、横の余りにトラックパッドを置く
struct DockedInput: View {
    @EnvironmentObject private var session: RemoteSession
    @AppStorage(OverlayKeyboard.layoutKey) private var layout = OverlayKeyboardLayout.us
    @AppStorage(Self.showKeysKey) private var showKeys = true
    @AppStorage(Self.showPadKey) private var showPad = true

    static let showKeysKey = "dockShowKeys"
    static let showPadKey = "dockShowPad"

    /// トラックパッドの、いちばん狭い幅（ポイント）
    private static let minTrackpadWidth: CGFloat = 140
    private static let spacing: CGFloat = 10
    private static let toolbarHeight: CGFloat = 34

    var body: some View {
        VStack(spacing: 8) {
            toolbar
            GeometryReader { proxy in
                content(in: proxy.size)
            }
        }
        .animation(.spring(duration: 0.35), value: showKeys)
        .animation(.spring(duration: 0.35), value: showPad)
    }

    @ViewBuilder
    private func content(in size: CGSize) -> some View {
        let keysHeightPerWidth = OverlayKeyboard.keyAspect * CGFloat(layout.rows.count) / OverlayKeyboardLayout.rowWidth
        // キーボードの周りの余白（padding の 6 の両側）
        let inset: CGFloat = 12
        // 高さいっぱいにしたときの幅
        let fullWidth = max(size.height - inset, 1) / keysHeightPerWidth
        // トラックパッドも出すときは、トラックパッドの分を残す
        let limit = showPad ? size.width - inset - Self.spacing - Self.minTrackpadWidth : size.width - inset
        let keysWidth = max(min(fullWidth, limit), 1)
        let keysHeight = keysWidth * keysHeightPerWidth

        HStack(spacing: Self.spacing) {
            if showKeys {
                OverlayKeysView(session: session, layout: layout)
                    .frame(width: keysWidth, height: keysHeight)
                    .padding(6)
                    .glass(in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                    .transition(.move(edge: .leading).combined(with: .opacity))
            }
            if showPad {
                TrackpadPad(send: session.send)
                    .frame(maxHeight: showKeys ? keysHeight + inset : .infinity)
                    .transition(.move(edge: .trailing).combined(with: .opacity))
            }
        }
        .frame(width: size.width, height: size.height)
    }

    /// 上の帯。キーボードとトラックパッドの出し入れ、IME の切り替え、全部しまうボタン
    private var toolbar: some View {
        HStack(spacing: 8) {
            toggle(systemImage: "keyboard", isOn: $showKeys, label: "キーボード")
            toggle(systemImage: "rectangle.and.hand.point.up.left", isOn: $showPad, label: "トラックパッド")
            Spacer()
            Button {
                session.toggleIME()
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "globe")
                    Text(session.imeOn == true ? "あ" : "A").font(.callout.bold())
                }
                .padding(.horizontal, 14)
                .frame(height: Self.toolbarHeight)
                .glass(in: Capsule(), tint: session.imeOn == true ? .accentColor : nil, interactive: true)
            }
            .buttonStyle(.plain)
            Button {
                showKeys = false
                showPad = false
            } label: {
                Image(systemName: "chevron.down")
                    .glassCircle(size: Self.toolbarHeight)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("キーボードとトラックパッドをしまう")
        }
        .font(.callout.weight(.semibold))
        .foregroundStyle(.white)
    }

    private func toggle(systemImage: String, isOn: Binding<Bool>, label: String) -> some View {
        Button {
            isOn.wrappedValue.toggle()
        } label: {
            Image(systemName: systemImage)
                .frame(width: 48, height: Self.toolbarHeight)
                .glass(in: Capsule(), tint: isOn.wrappedValue ? .accentColor : nil, interactive: true)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }
}

/// 半開きで、キーボードとトラックパッドを両方しまったときに、下の端に出すボタン。押すと両方出す
struct DockedInputRestoreButton: View {
    @AppStorage(DockedInput.showKeysKey) private var showKeys = true
    @AppStorage(DockedInput.showPadKey) private var showPad = true

    var body: some View {
        Button {
            showKeys = true
            showPad = true
        } label: {
            HStack(spacing: 10) {
                Image(systemName: "chevron.up")
                Image(systemName: "keyboard")
                Image(systemName: "rectangle.and.hand.point.up.left")
            }
            .font(.callout.weight(.semibold))
            .foregroundStyle(.white)
            .padding(.horizontal, 18)
            .padding(.vertical, 10)
            .glass(in: Capsule(), interactive: true)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("キーボードとトラックパッドを出す")
    }
}
