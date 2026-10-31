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
    @AppStorage(ControllerKind.storageKey) private var controllerKind = ControllerKind.simple
    @ObservedObject private var physical = PhysicalController.shared

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

    /// ガチを選んでいて、PC も対応しているときだけ、ガチで動かす。PC が対応していないときは、簡易で動かす
    private var usesPad: Bool {
        controllerKind == .pad && session.padUsable
    }

    /// 本物のコントローラを使っている。このときは、画面のコントローラを薄くして、触れないようにする
    private var physicalInUse: Bool {
        usesPad && physical.isConnected
    }

    private var controllerPane: some View {
        ZStack {
            if usesPad {
                VirtualGamepadView(session: session, enabled: !physicalInUse)
                    .opacity(physicalInUse ? 0.25 : 1)
            } else {
                VirtualControllerView(session: session, preset: preset)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .glass(in: RoundedRectangle(cornerRadius: UIMetrics.panelRadius, style: .continuous))
        .overlay(alignment: .bottom) {
            VStack(spacing: 2) {
                if let text = controllerNote {
                    Text(text)
                        .font(.caption2)
                        .foregroundStyle(.white.opacity(0.7))
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: 240)
                        .allowsHitTesting(false)
                }
                presetMenu
            }
            .padding(.bottom, 6)
        }
    }

    /// コントローラの板の隅の、小さい案内
    private var controllerNote: String? {
        if controllerKind == .pad && !session.padUsable {
            return "PC にゲームパッドの部品（ViGEmBus）が入っていません。簡易で動きます"
        }
        guard physical.isConnected else { return nil }
        return usesPad ? "コントローラ接続中" : "コントローラ接続中（ガチにすると使えます）"
    }

    /// コントローラの隅の小さい歯車。種類と、キーの割り当てを切り替える
    private var presetMenu: some View {
        Menu {
            Picker("コントローラの種類", selection: $controllerKind) {
                ForEach(ControllerKind.allCases) { item in
                    Text(item.label).tag(item)
                }
            }
            .pickerStyle(.inline)
            Picker("キーの割り当て", selection: $preset) {
                ForEach(ControllerPreset.allCases) { item in
                    Text(item.label).tag(item)
                }
            }
            .pickerStyle(.inline)
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "gearshape")
                Text(usesPad ? "ガチ" : preset.label)
            }
            .font(.caption.weight(.semibold))
            .foregroundStyle(.white.opacity(0.8))
            .padding(.horizontal, 12)
            .frame(height: 34)
            .contentShape(Capsule())
        }
        .accessibilityLabel("コントローラの種類とキーの割り当て")
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
