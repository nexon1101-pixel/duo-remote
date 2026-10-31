import SwiftUI

/// PCのウィンドウの一覧。上のボタンで、小さい絵（プレビュー）の並びと、文字だけの一覧を切り替える。
/// 絵が取れないウィンドウ（最小化しているものなど）は、場所の色と、アプリの頭文字で、見分けられるようにする。
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
    /// 並べ方: grid（小さい絵）か list（文字だけ）
    @AppStorage("windowPickerStyle") private var style = "grid"

    private var isGrid: Bool { style == "grid" }

    private var sectionTitle: String { bringing ? "仮想画面に移すウィンドウ" : "ウィンドウ" }

    /// 今映しているウィンドウを先頭にした一覧
    private var ordered: [RemoteWindow] {
        let currentID = session.currentWindow?.id
        return session.windows.filter { $0.id == currentID } + session.windows.filter { $0.id != currentID }
    }

    /// 探す文字で絞った一覧
    private var shownWindows: [RemoteWindow] {
        let words = query.split(separator: " ").map(String.init)
        return ordered.filter { window in
            words.allSatisfy { window.title.localizedCaseInsensitiveContains($0) || window.app.localizedCaseInsensitiveContains($0) }
        }
    }

    private func requestThumbs() {
        guard isGrid else { return }
        session.requestWindowThumbs(ordered.map(\.id))
    }

    private var bringing: Bool { session.viewMode == .desktop }

    var body: some View {
        NavigationStack {
            List {
                // 小さい絵の並びのときは、ウィンドウがあれば、見出しも絵の方に付ける
                if !isGrid || session.windows.isEmpty {
                Section(sectionTitle) {
                    if session.windows.isEmpty {
                        if session.isLoadingWindows {
                            ProgressView()
                                .frame(maxWidth: .infinity)
                        } else {
                            ContentUnavailableView(
                                "ウィンドウがありません",
                                systemImage: "macwindow",
                                description: Text("下に引くか、右上のボタンで更新します")
                            )
                        }
                    }
                    if !isGrid {
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
                }
                }

                if isGrid && shownWindows.isEmpty && !session.windows.isEmpty {
                    Section {
                        Text("「\(query)」に一致するウィンドウはありません")
                            .foregroundStyle(.secondary)
                    }
                }

                if isGrid && !shownWindows.isEmpty {
                    Section(sectionTitle) {
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 12)], spacing: 14) {
                            ForEach(shownWindows) { window in
                                Button {
                                    choose(window)
                                    dismiss()
                                } label: {
                                    card(window)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                    .listRowInsets(EdgeInsets(top: 6, leading: 12, bottom: 10, trailing: 12))
                    .listRowBackground(Color.clear)
                }

                if session.viewMode != .screen {
                    Section {
                        Button {
                            session.showScreen()
                            dismiss()
                        } label: {
                            Label("PC の画面を表示", systemImage: "display")
                        }
                    }
                }

                if !bringing {
                    Section {
                        if session.virtualAvailable {
                            Toggle("仮想画面に移して映す", isOn: $toVirtual)
                        }
                        Toggle("この画面の大きさに合わせる", isOn: $fitWindow)
                            .disabled(session.virtualAvailable && toVirtual)
                    } footer: {
                        Text(footerText)
                    }
                }
            }
            .navigationTitle(bringing ? "ウィンドウを仮想画面に移す" : "ウィンドウを選ぶ")
            .navigationBarTitleDisplayMode(.inline)
            .searchable(text: $query, placement: .navigationBarDrawer(displayMode: .automatic), prompt: "ウィンドウを探す")
            .refreshable { session.requestWindows() }
            .onAppear { requestThumbs() }
            .onChange(of: session.isLoadingWindows) { _, loading in
                if !loading { requestThumbs() }
            }
            .onChange(of: style) { _, _ in requestThumbs() }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("閉じる") { dismiss() }
                }
                ToolbarItem(placement: .principal) {
                    Picker("表示", selection: $style) {
                        Image(systemName: "square.grid.2x2").tag("grid")
                            .accessibilityLabel("プレビューで選ぶ")
                        Image(systemName: "list.bullet").tag("list")
                            .accessibilityLabel("一覧で選ぶ")
                    }
                    .pickerStyle(.segmented)
                    .frame(width: 110)
                }
                ToolbarItem(placement: .primaryAction) {
                    Button("更新", systemImage: "arrow.clockwise") { session.requestWindows() }
                }
            }
        }
        .presentationDetents([.fraction(0.6), .large])
        .presentationDragIndicator(.visible)
    }

    private var footerText: String {
        if session.virtualAvailable && toVirtual {
            return "選んだウィンドウを仮想画面に移し、この画面と同じ縦横比で映します。切断すると元の場所に戻ります。"
        }
        return "選んだウィンドウを、この画面と同じ縦横比にします。文字の大きさは、PC で見るときとほぼ同じです。"
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

    // MARK: 小さい絵の並び

    private func detailText(_ window: RemoteWindow) -> String {
        [window.app, placeName(window.place)]
            .compactMap { $0 }
            .filter { !$0.isEmpty }
            .joined(separator: " ・ ")
    }

    private func card(_ window: RemoteWindow) -> some View {
        let isCurrent = window.id == session.currentWindow?.id
        let shape = RoundedRectangle(cornerRadius: 12, style: .continuous)
        let detail = detailText(window)
        return VStack(alignment: .leading, spacing: 6) {
            Color.clear
                .aspectRatio(16 / 10, contentMode: .fit)
                .overlay { thumbnail(window) }
                .clipShape(shape)
                .overlay {
                    shape.strokeBorder(isCurrent ? Color.accentColor : Color.primary.opacity(0.12), lineWidth: isCurrent ? 3 : 1)
                }
                .overlay(alignment: .topTrailing) {
                    if isCurrent {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.title3)
                            .foregroundStyle(.white, Color.accentColor)
                            .padding(6)
                    }
                }
            Text(window.title.isEmpty ? "（名前なし）" : window.title)
                .font(.footnote.weight(.medium))
                .lineLimit(1)
            if !detail.isEmpty {
                Text(detail)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    /// 絵が届いていれば、絵。なければ、場所の色と、アプリの頭文字（届くのを待っているときは、くるくる。取れなかったときは、その旨）
    @ViewBuilder
    private func thumbnail(_ window: RemoteWindow) -> some View {
        if let image = session.windowThumbs[window.id] {
            ZStack {
                Color.black.opacity(0.08)
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            }
        } else {
            placeholder(window)
        }
    }

    private func placeholder(_ window: RemoteWindow) -> some View {
        let icon = iconStyle(window.place)
        return ZStack {
            LinearGradient(
                colors: [icon.color.opacity(0.38), icon.color.opacity(0.12)],
                startPoint: .topLeading, endPoint: .bottomTrailing
            )
            Text(initial(of: window))
                .font(.system(size: 26, weight: .bold, design: .rounded))
                .foregroundStyle(.white)
                .frame(width: 52, height: 52)
                .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(icon.color.gradient))
                .shadow(color: icon.color.opacity(0.35), radius: 6, y: 3)
            VStack {
                Spacer()
                HStack(spacing: 4) {
                    if session.windowThumbPending.contains(window.id) {
                        ProgressView().controlSize(.mini)
                    } else if session.windowThumbMissing.contains(window.id) {
                        Image(systemName: "eye.slash")
                        Text("プレビューなし")
                    }
                    Spacer()
                    Image(systemName: icon.symbol)
                }
                .font(.caption2)
                .foregroundStyle(.secondary)
                .padding(8)
            }
        }
    }

    /// アプリの名前（なければ、ウィンドウの名前）の、最初の1文字
    private func initial(of window: RemoteWindow) -> String {
        var name = window.app
        if name.lowercased().hasSuffix(".exe") { name = String(name.dropLast(4)) }
        if name.isEmpty { name = window.title }
        return name.first.map { String($0).uppercased() } ?? "?"
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
