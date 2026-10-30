import SwiftUI

/// PCのウィンドウの一覧。選ぶと、そのウィンドウだけを表示する。
struct WindowPicker: View {
    @EnvironmentObject private var session: RemoteSession
    @Environment(\.dismiss) private var dismiss

    /// ウィンドウの大きさを合わせる先（映像を出している範囲のポイント数）
    let fitSize: CGSize

    @AppStorage("fitWindow") private var fitWindow = true

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Toggle("この画面に大きさを合わせる", isOn: $fitWindow)
                } footer: {
                    Text("選んだウィンドウの大きさを、この画面と同じ縦横比に変えます。文字は、PCで見るのとほぼ同じ大きさになります。")
                }

                Section("ウィンドウ") {
                    if session.windows.isEmpty {
                        Text(session.isLoadingWindows ? "読み込んでいます…" : "ウィンドウがありません")
                            .foregroundStyle(.secondary)
                    }
                    ForEach(session.windows) { window in
                        Button {
                            session.showWindow(window, fit: fitWindow ? fitSize : nil)
                            dismiss()
                        } label: {
                            row(window)
                        }
                        .tint(.primary)
                    }
                }

                if session.currentWindow != nil {
                    Section {
                        Button("画面全体を表示") {
                            session.showScreen()
                            dismiss()
                        }
                    }
                }
            }
            .navigationTitle("ウィンドウを選ぶ")
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

    private func row(_ window: RemoteWindow) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(window.title).lineLimit(1)
                if !window.app.isEmpty {
                    Text(window.app).font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer()
            if window.id == session.currentWindow?.id {
                Image(systemName: "checkmark").foregroundStyle(.tint)
            }
        }
        .contentShape(Rectangle())
    }
}
