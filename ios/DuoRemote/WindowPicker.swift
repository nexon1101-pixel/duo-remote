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

    private var bringing: Bool { session.viewMode == .desktop }

    var body: some View {
        NavigationStack {
            List {
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

                Section(bringing ? "仮想画面に持ってくるウィンドウ" : "ウィンドウ") {
                    if session.windows.isEmpty {
                        Text(session.isLoadingWindows ? "読み込んでいます…" : "ウィンドウがありません")
                            .foregroundStyle(.secondary)
                    }
                    ForEach(session.windows) { window in
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
                        Button("本物の画面を表示") {
                            session.showScreen()
                            dismiss()
                        }
                    }
                }
            }
            .navigationTitle(bringing ? "ウィンドウを持ってくる" : "ウィンドウを選ぶ")
            .navigationBarTitleDisplayMode(.inline)
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

    private func row(_ window: RemoteWindow) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(window.title).lineLimit(1)
                HStack(spacing: 6) {
                    if window.place == "virtual" {
                        Label("仮想画面", systemImage: "rectangle.on.rectangle")
                    }
                    if !window.app.isEmpty {
                        Text(window.app)
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            Spacer()
            if window.id == session.currentWindow?.id {
                Image(systemName: "checkmark").foregroundStyle(.tint)
            }
        }
        .contentShape(Rectangle())
    }
}
