import SwiftUI

/// ゲームモードの板の、中身の切り替え
enum GameModePane: String, CaseIterable, Identifiable {
    case keyboard
    case controller
    case trackpad

    var id: String { rawValue }

    var systemImage: String {
        switch self {
        case .keyboard: "keyboard"
        case .controller: "gamecontroller"
        case .trackpad: "rectangle.and.hand.point.up.left"
        }
    }

    var label: String {
        switch self {
        case .keyboard: "キーボード"
        case .controller: "コントローラ"
        case .trackpad: "トラックパッド"
        }
    }
}

/// ゲームモードの、下半分の板。上の帯に切り替えとメニューを置き、下に、キーボード、コントローラ、トラックパッドのどれかを出す。
/// 半開きの Duo では、下の画面（DockedInput と同じ位置）に出る
struct GameModePanel: View {
    @EnvironmentObject private var session: RemoteSession
    @AppStorage(OverlayKeyboard.layoutKey) private var layout = OverlayKeyboardLayout.us
    @AppStorage("gameModePane") private var pane = GameModePane.controller
    @AppStorage("gameMode") private var gameMode = false
    @AppStorage("controllerPreset") private var preset = ControllerPreset.arrows

    /// 「…」のメニュー
    let menuButton: AnyView
    /// ウィンドウの一覧を開くボタン
    let windowsButton: AnyView

    private static let toolbarHeight: CGFloat = UIMetrics.compactButtonHeight
    /// キーボードの周りの余白（上下左右の 6 ずつ）
    private static let panelInset: CGFloat = 12

    var body: some View {
        VStack(spacing: 8) {
            toolbar
            GeometryReader { proxy in
                content(in: proxy.size)
                    .frame(width: proxy.size.width, height: proxy.size.height, alignment: .top)
            }
        }
        .animation(.spring(duration: 0.35), value: pane)
    }

    @ViewBuilder
    private func content(in size: CGSize) -> some View {
        switch pane {
        case .keyboard:
            keyboardPane(in: size)
        case .controller:
            controllerPane
        case .trackpad:
            TrackpadPad(send: session.send, zones: .sides)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    /// 板いっぱいのキーボード。横幅から縦を決め、高さの上限を超えるときは、横幅を狭める
    private func keyboardPane(in size: CGSize) -> some View {
        let inset = Self.panelInset
        let ratio = OverlayKeyboard.keyAspect * CGFloat(layout.rows.count) / OverlayKeyboardLayout.rowWidth
        let maxHeight = max(size.height - inset, 1)
        var width = max(size.width - inset, 1)
        var height = width * ratio
        if height > maxHeight {
            height = maxHeight
            width = height / ratio
        }
        return OverlayKeysView(session: session, layout: layout)
            .frame(width: width, height: height)
            .padding(6)
            .glass(in: RoundedRectangle(cornerRadius: UIMetrics.panelRadius, style: .continuous))
    }

    private var controllerPane: some View {
        VirtualControllerView(session: session, preset: preset)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .glass(in: RoundedRectangle(cornerRadius: UIMetrics.panelRadius, style: .continuous))
            .overlay(alignment: .bottom) {
                presetMenu
                    .padding(.bottom, 6)
            }
    }

    /// コントローラの隅の小さい歯車。キーの割り当てを切り替える
    private var presetMenu: some View {
        Menu {
            Picker("キーの割り当て", selection: $preset) {
                ForEach(ControllerPreset.allCases) { item in
                    Text(item.label).tag(item)
                }
            }
            .pickerStyle(.inline)
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "gearshape")
                Text(preset.label)
            }
            .font(.caption.weight(.semibold))
            .foregroundStyle(.white.opacity(0.8))
            .padding(.horizontal, 12)
            .frame(height: 34)
            .contentShape(Capsule())
        }
        .accessibilityLabel("コントローラのキーの割り当て")
    }

    /// 上の帯。切り替え、ウィンドウ、メニュー、ゲームモードを終わるボタン
    private var toolbar: some View {
        HStack(spacing: UIMetrics.spacing) {
            ForEach(GameModePane.allCases) { item in
                paneButton(item)
            }
            Spacer()
            windowsButton
            menuButton
            Button {
                gameMode = false
            } label: {
                Image(systemName: "xmark")
                    .glassCircle(size: Self.toolbarHeight)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("ゲームモードを終わる")
        }
        .font(.callout.weight(.semibold))
        .foregroundStyle(.white)
    }

    private func paneButton(_ item: GameModePane) -> some View {
        Button {
            pane = item
        } label: {
            Image(systemName: item.systemImage)
                .frame(width: 48, height: Self.toolbarHeight)
                .glass(in: Capsule(), tint: pane == item ? .accentColor : nil, interactive: true)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(item.label)
    }
}
