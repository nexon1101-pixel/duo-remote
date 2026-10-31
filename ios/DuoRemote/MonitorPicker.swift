import SwiftUI

/// PCのモニター（一覧の1行）。
struct RemoteMonitor: Identifiable, Equatable {
    /// サーバーが決める、モニターを区別する文字列（Windows のデバイス名。例: \\.\DISPLAY1）
    let id: String
    let name: String
    let width: Int
    let height: Int
    /// 主モニター
    var primary = false
    /// 仮想ディスプレイ
    var virtual = false

    init(id: String, name: String, width: Int = 0, height: Int = 0, primary: Bool = false, virtual: Bool = false) {
        self.id = id
        self.name = name
        self.width = width
        self.height = height
        self.primary = primary
        self.virtual = virtual
    }

    /// サーバーの monitors の1項目から作る
    init?(json: [String: Any]) {
        guard let id = json["id"] as? String else { return nil }
        self.init(
            id: id,
            name: json["name"] as? String ?? id,
            width: json["w"] as? Int ?? 0,
            height: json["h"] as? Int ?? 0,
            primary: json["primary"] as? Bool ?? false,
            virtual: json["virtual"] as? Bool ?? false
        )
    }
}

extension RemoteSession {
    /// モニターの一覧を読み直す
    func requestMonitors() {
        isLoadingMonitors = true
        send(["t": "list_monitors"])
    }

    /// monitors の返事を受け取る
    func handleMonitors(_ json: [String: Any]) {
        let list = json["list"] as? [[String: Any]] ?? []
        monitors = list.compactMap(RemoteMonitor.init(json:))
        isLoadingMonitors = false
    }
}

/// PCのモニターの一覧。選んだモニターだけを映す。
struct MonitorPicker: View {
    @EnvironmentObject private var session: RemoteSession
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section("モニター") {
                    if session.monitors.isEmpty {
                        if session.isLoadingMonitors {
                            ProgressView()
                                .frame(maxWidth: .infinity)
                        } else {
                            ContentUnavailableView(
                                "モニターがありません",
                                systemImage: "display",
                                description: Text("下にスワイプして更新")
                            )
                        }
                    }
                    ForEach(session.monitors) { monitor in
                        Button {
                            session.showMonitor(monitor)
                            dismiss()
                        } label: {
                            row(monitor)
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
                            Label("メイン画面を表示", systemImage: "display")
                        }
                    }
                }
            }
            .navigationTitle("モニターを選択")
            .navigationBarTitleDisplayMode(.inline)
            .refreshable { session.requestMonitors() }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("閉じる") { dismiss() }
                }
                ToolbarItem(placement: .primaryAction) {
                    Button("更新", systemImage: "arrow.clockwise") { session.requestMonitors() }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }

    private func isCurrent(_ monitor: RemoteMonitor) -> Bool {
        switch session.viewMode {
        case .monitor: session.currentMonitor?.id == monitor.id
        case .screen: monitor.primary
        default: false
        }
    }

    private func row(_ monitor: RemoteMonitor) -> some View {
        let color: Color = monitor.virtual ? .purple : (monitor.primary ? .blue : .orange)
        let symbol = monitor.virtual ? "rectangle.on.rectangle" : "display"
        var parts: [String] = []
        if monitor.width > 0, monitor.height > 0 { parts.append("\(monitor.width) × \(monitor.height)") }
        if monitor.primary { parts.append("メイン") }
        if monitor.virtual { parts.append("仮想") }
        let detail = parts.joined(separator: " ・ ")
        return HStack(spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 32, height: 32)
                .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(color.gradient))
            VStack(alignment: .leading, spacing: 2) {
                Text(monitor.name).font(.body).lineLimit(1)
                if !detail.isEmpty {
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            Spacer()
            if isCurrent(monitor) {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.tint)
            }
        }
        .contentShape(Rectangle())
    }
}
