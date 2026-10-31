import SwiftUI

/// 半開きの Duo の、下の画面。アプリのキーボードとトラックパッドを置く。
/// 上の帯のボタンで、キーボードとトラックパッドを、それぞれ出したりしまったりできる。「…」のメニューも、この帯に置く。
///
/// 並べ方は、下の画面の形で決める。
/// - 縦に余りがあるとき: ノートパソコンのように、キーボードを上に、トラックパッドを下に置く。トラックパッドは、残りの高さと横幅いっぱいまで広げる
/// - 縦が足りないとき: キーボードを左に、トラックパッドを右に置く。トラックパッドは、キーボードと同じ高さ
/// - 片方だけのとき: その1つを、できるだけ広くする
struct DockedInput: View {
    @EnvironmentObject private var session: RemoteSession
    @AppStorage(OverlayKeyboard.layoutKey) private var layout = OverlayKeyboardLayout.us
    @AppStorage(Self.showKeysKey) private var showKeys = true
    @AppStorage(Self.showPadKey) private var showPad = true

    /// 「…」のメニュー。上の画面の角に出すより、下の画面の帯にある方が、押しやすい
    let menuButton: AnyView
    /// ウィンドウの一覧を開くボタン。切り替えは何度もするので、帯に置く
    let windowsButton: AnyView

    static let showKeysKey = "dockShowKeys"
    static let showPadKey = "dockShowPad"

    private static let spacing: CGFloat = 8
    private static let toolbarHeight: CGFloat = 34
    /// キーボードの周りの余白（上下左右の 6 ずつ）
    private static let panelInset: CGFloat = 12
    /// 縦に並べるとき、トラックパッドに残したい高さの下限（ポイント）
    private static let minPadHeight: CGFloat = 120
    /// 横に並べるとき、トラックパッドの幅の下限（ポイント）
    private static let minPadWidth: CGFloat = 140
    /// キーボードだけのとき、キーの縦を、自然な形から伸ばしてよい倍率
    private static let maxStretch: CGFloat = 1.3

    var body: some View {
        VStack(spacing: 8) {
            toolbar
            GeometryReader { proxy in
                content(in: proxy.size)
                    .frame(width: proxy.size.width, height: proxy.size.height, alignment: .top)
            }
        }
        .animation(.spring(duration: 0.35), value: showKeys)
        .animation(.spring(duration: 0.35), value: showPad)
    }

    @ViewBuilder
    private func content(in size: CGSize) -> some View {
        let inset = Self.panelInset
        // キーボードの、横幅1に対する高さ（自然な形）
        let ratio = OverlayKeyboard.keyAspect * CGFloat(layout.rows.count) / OverlayKeyboardLayout.rowWidth

        if showKeys && showPad {
            let keysWidth = max(size.width - inset, 1)
            let keysHeight = keysWidth * ratio
            let padHeight = size.height - keysHeight - inset - Self.spacing
            if padHeight >= Self.minPadHeight {
                // ノートパソコンのように、キーボードの下に、広いトラックパッド
                VStack(spacing: Self.spacing) {
                    keyboard(width: keysWidth, height: keysHeight)
                    TrackpadPad(send: session.send, zones: .sides)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            } else {
                // 縦が足りないときは、横に並べる。トラックパッドは、キーボードと同じ高さ
                let limit = size.width - inset - Self.spacing - Self.minPadWidth
                let width = max(min(limit, (size.height - inset) / ratio), 1)
                let height = width * ratio
                HStack(spacing: Self.spacing) {
                    keyboard(width: width, height: height)
                    TrackpadPad(send: session.send, zones: .sides)
                        .frame(maxHeight: height + inset)
                }
            }
        } else if showKeys {
            let width = max(size.width - inset, 1)
            let height = min(width * ratio * Self.maxStretch, max(size.height - inset, 1))
            keyboard(width: width, height: height)
        } else if showPad {
            TrackpadPad(send: session.send, zones: .sides)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func keyboard(width: CGFloat, height: CGFloat) -> some View {
        OverlayKeysView(session: session, layout: layout)
            .frame(width: width, height: height)
            .padding(6)
            .glass(in: RoundedRectangle(cornerRadius: 20, style: .continuous))
            .transition(.move(edge: .leading).combined(with: .opacity))
    }

    /// 上の帯。キーボードとトラックパッドの出し入れ、IME の切り替え、メニュー、全部しまうボタン
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
            windowsButton
            menuButton
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
