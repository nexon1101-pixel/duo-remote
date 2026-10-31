import SwiftUI

/// PCのウィンドウの一覧。
/// - 仮想画面を映しているとき: 選んだウィンドウを、仮想画面に持ってくる
/// - それ以外: 選んだウィンドウだけを映す
struct WindowPicker: View {
    @EnvironmentObject private var session: RemoteSession
    @Environment(\.dismiss) private var dismiss

    /// ウィンドウの大きさを合わせる先（映像を出している範囲のポイント数）
    let fitSize: CGSize

    @AppStorage("fitWindow") private var fitWindow = true
    @AppStorage("toVirtual") private var toVirtual = true
    /// 探す文字
    @State private var query = ""

    /// 今映しているウィンドウを先頭にして、探す文字で絞った一覧
    private var shownWindows: [RemoteWindow] {
        let words = query.split(separator: " ").map(String.init)
        let matched = session.windows.filter { window in
            words.allSatisfy { window.title.localizedCaseInsensitiveContains($0) || window.app.localizedCaseInsensitiveContains($0) }
        }
        let currentID = session.currentWindow?.id
        return matched.filter { $0.id == currentID } + matched.filter { $0.id != currentID }
    }

    private var bringing: Bool { session.viewMode == .desktop }

    var body: some View {
        NavigationStack {
            List {
                Section(bringing ? "仮想画面に持ってくるウィンドウ" : "ウィンドウ") {
                    if session.windows.isEmpty {
                        if session.isLoadingWindows {
                            ProgressView()
                                .frame(maxWidth: .infinity)
                        } else {
                            ContentUnavailableView(
                                "ウィンドウがありません",
                                systemImage: "macwindow",
                                description: Text("下に引くか、右上のボタンで読み直します")
                            )
                        }
                    }
                    ForEach(shownWindows) { window in
                        Button {
                            choose(window)
                            dismiss()
                        } label: {
                            row(window)
                        }
                        .tint(.primary)
                    }
                }

                if session.viewMode != .screen {
                    Section {
                        Button {
                            session.showScreen()
                            dismiss()
                        } label: {
                            Label("本物の画面を表示", systemImage: "display")
                        }
                    }
                }

                if !bringing {
                    Section {
                        if session.virtualAvailable {
                            Toggle("仮想画面に移して映す", isOn: $toVirtual)
                        }
                        Toggle("この画面に大きさを合わせる", isOn: $fitWindow)
                            .disabled(session.virtualAvailable && toVirtual)
                    } footer: {
                        Text(footerText)
                    }
                }
            }
            .navigationTitle(bringing ? "ウィンドウを持ってくる" : "ウィンドウを選ぶ")
            .navigationBarTitleDisplayMode(.inline)
            .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .automatic), prompt: "ウィンドウを探す")
            .refreshable { session.requestWindows() }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("閉じる") { dismiss() }
                }
                ToolbarItem(placement: .primaryAction) {
                    Button("読み直す", systemImage: "arrow.clockwise") { session.requestWindows() }
                }
            }
        }
        .presentationDetents([.fraction(0.6), .large])
        .presentationDragIndicator(.visible)
    }

    private var footerText: String {
        if session.virtualAvailable && toVirtual {
            return "選んだウィンドウを、仮想画面に移して、この画面と同じ縦横比で映します。切断すると、元の場所に戻ります。"
        }
        return "選んだウィンドウの大きさを、この画面と同じ縦横比に変えます。文字は、PCで見るのとほぼ同じ大きさになります。"
    }

    private func choose(_ window: RemoteWindow) {
        if bringing {
            session.bringWindow(window)
            return
        }
        let useVirtual = session.virtualAvailable && toVirtual
        // 仮想画面に移すときは、仮想画面の縦横比を決めるために、大きさを送る
        let fit = (fitWindow || useVirtual) ? fitSize : nil
        session.showWindow(window, fit: fit, toVirtual: useVirtual && window.place != "virtual")
    }

    /// 場所ごとのアイコンの記号と色
    private func iconStyle(_ place: String) -> (symbol: String, color: Color) {
        switch place {
        case "virtual": ("rectangle.on.rectangle", Color.purple)
        case "other": ("display.2", Color.orange)
        case "none": ("eye.slash", Color.gray)
        default: ("macwindow", Color.blue)
        }
    }

    private func placeName(_ place: String) -> String? {
        switch place {
        case "virtual": "仮想画面"
        case "other": "ほかのモニター"
        case "none": "表示されていない"
        default: nil
        }
    }

    private func row(_ window: RemoteWindow) -> some View {
        let icon = iconStyle(window.place)
        let detail = [window.app, placeName(window.place)]
            .compactMap { $0 }
            .filter { !$0.isEmpty }
            .joined(separator: " ・ ")
        return HStack(spacing: 12) {
            Image(systemName: icon.symbol)
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 32, height: 32)
                .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(icon.color.gradient))
            VStack(alignment: .leading, spacing: 2) {
                Text(window.title).font(.body).lineLimit(1)
                if !detail.isEmpty {
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            Spacer()
            if window.id == session.currentWindow?.id {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.tint)
            }
        }
        .padding(.vertical, 6)
        .frame(minHeight: 52)
        .contentShape(Rectangle())
    }
}
