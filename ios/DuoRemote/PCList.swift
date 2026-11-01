import SwiftUI

/// 登録した PC 1台分の設定。トークンだけは、キーチェーンにしまう
struct PCProfile: Codable, Identifiable, Equatable {
    var id = UUID()
    var name = ""
    /// 自宅のホスト
    var host = ""
    /// 外出先（Tailscale）のホスト
    var hostRemote = ""
    var port = "8765"
    /// 有線 LAN の MAC アドレス（直接起こすときに使う）
    var mac = ""
    /// ブロードキャスト（例: 192.168.0.255）
    var broadcast = ""
    /// UpSnap に登録した IP アドレス。空なら、自宅のホスト
    var deviceIP = ""

    /// UpSnap の機器と突き合わせる IP アドレス
    var upsnapIP: String {
        let own = deviceIP.trimmingCharacters(in: .whitespaces)
        return own.isEmpty ? host.trimmingCharacters(in: .whitespaces) : own
    }

    var displayName: String {
        name.isEmpty ? (host.isEmpty ? hostRemote : host) : name
    }

    /// 起こすための設定がそろっているか
    var canWakeDirectly: Bool { !mac.isEmpty }
}

/// 登録した PC の一覧。端末に覚える
@MainActor
final class PCStore: ObservableObject {
    @Published private(set) var profiles: [PCProfile] = []

    private let key = "pcProfiles"

    init() {
        load()
        if profiles.isEmpty { migrateFromSingleHost() }
    }

    func token(for id: UUID) -> String {
        Keychain.load("token.\(id.uuidString)") ?? ""
    }

    func save(_ profile: PCProfile, token: String) {
        if let index = profiles.firstIndex(where: { $0.id == profile.id }) {
            profiles[index] = profile
        } else {
            profiles.append(profile)
        }
        Keychain.save("token.\(profile.id.uuidString)", token)
        persist()
    }

    func delete(_ id: UUID) {
        profiles.removeAll { $0.id == id }
        Keychain.save("token.\(id.uuidString)", "")
        persist()
    }

    private func load() {
        guard let data = UserDefaults.standard.data(forKey: key),
              let decoded = try? JSONDecoder().decode([PCProfile].self, from: data) else { return }
        profiles = decoded
    }

    private func persist() {
        if let data = try? JSONEncoder().encode(profiles) {
            UserDefaults.standard.set(data, forKey: key)
        }
    }

    /// 0.22 までは、PC を1台だけ覚えていた。その設定を、最初の1台として引き継ぐ
    private func migrateFromSingleHost() {
        let defaults = UserDefaults.standard
        let host = defaults.string(forKey: "host") ?? ""
        let remote = defaults.string(forKey: "hostRemote") ?? ""
        guard !host.isEmpty || !remote.isEmpty else { return }
        var profile = PCProfile()
        profile.name = "自宅の PC"
        profile.host = host
        profile.hostRemote = remote
        profile.port = defaults.string(forKey: "port") ?? "8765"
        profile.mac = defaults.string(forKey: "wakeMAC") ?? ""
        profile.broadcast = defaults.string(forKey: "wakeBroadcast") ?? ""
        profile.deviceIP = defaults.string(forKey: "upsnapDeviceIP") ?? ""
        save(profile, token: Keychain.load("token") ?? "")
    }
}

/// 接続の画面の、PC の一覧。UpSnap の状態を見て、オン・オフを出す
struct PCListSection: View {
    @EnvironmentObject private var session: RemoteSession

    /// つなぐ PC と、そのトークン。つなぐ処理は、呼ぶ側が持つ
    let connect: (PCProfile, String) -> Void

    @StateObject private var store = PCStore()
    @StateObject private var waker = PCWaker()

    @AppStorage("upsnapURL") private var upsnapURL = ""
    @AppStorage("upsnapEmail") private var upsnapEmail = ""
    @State private var upsnapPassword = ""

    /// IP アドレスごとの状態（"online"、"offline"、"pending"）。UpSnap から読めなかったときは空
    @State private var statuses: [String: String] = [:]
    @State private var statusError: String?
    @State private var statusLoaded = false

    @State private var editing: EditTarget?
    @State private var showingUpSnap = false
    /// いま起こしている PC
    @State private var wakingID: UUID?

    struct EditTarget: Identifiable {
        var profile: PCProfile
        var token: String
        var isNew: Bool
        var id: UUID { profile.id }
    }

    private var upsnapConfigured: Bool {
        !upsnapURL.isEmpty && !upsnapEmail.isEmpty
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("PC", systemImage: "desktopcomputer")
                .font(.footnote.weight(.semibold))
                .foregroundStyle(.secondary)
                .padding(.leading, 4)

            VStack(spacing: 0) {
                if store.profiles.isEmpty {
                    Text("PC を追加すると、ここから選んでつなげます")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(16)
                } else {
                    ForEach(Array(store.profiles.enumerated()), id: \.element.id) { index, profile in
                        if index > 0 { Divider().overlay(Color.white.opacity(0.1)) }
                        row(profile)
                    }
                }
                Divider().overlay(Color.white.opacity(0.1))
                HStack {
                    Button {
                        editing = EditTarget(profile: PCProfile(), token: "", isNew: true)
                    } label: {
                        Label("PC を追加", systemImage: "plus")
                    }
                    Spacer()
                    Button {
                        showingUpSnap = true
                    } label: {
                        Label("UpSnap", systemImage: "gearshape")
                    }
                }
                .font(.subheadline)
                .buttonStyle(.borderless)
                .padding(16)
            }
            .glass(in: RoundedRectangle(cornerRadius: 22, style: .continuous))

            if let message = statusError, upsnapConfigured {
                Label(message, systemImage: "exclamationmark.triangle.fill")
                    .font(.footnote)
                    .foregroundStyle(.orange)
                    .padding(.leading, 4)
            } else if !upsnapConfigured {
                Text("UpSnap を設定すると、PC のオン・オフが見えて、オフのときは起こしてつなげます")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(.leading, 4)
            }
            if !waker.detail.isEmpty {
                Text(waker.detail)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(.leading, 4)
            }
            if case .failed(let message) = waker.phase {
                Label(message, systemImage: "exclamationmark.triangle.fill")
                    .font(.footnote)
                    .foregroundStyle(.orange)
                    .padding(.leading, 4)
            }
        }
        .sheet(item: $editing) { target in
            PCEditSheet(target: target) { profile, token in
                store.save(profile, token: token)
            } onDelete: { id in
                store.delete(id)
            }
        }
        .sheet(isPresented: $showingUpSnap) {
            UpSnapSettingsSheet(url: $upsnapURL, email: $upsnapEmail, password: $upsnapPassword) {
                Keychain.save("upsnapPassword", upsnapPassword)
            }
        }
        .onAppear { upsnapPassword = Keychain.load("upsnapPassword") ?? "" }
        // 画面が出ている間、6秒おきに状態を読む
        .task(id: "\(upsnapURL)|\(upsnapEmail)|\(upsnapPassword)") {
            await pollStatuses()
        }
    }

    // MARK: 1行

    private enum PCState { case online, offline, pending, unknown }

    private func state(of profile: PCProfile) -> PCState {
        switch statuses[profile.upsnapIP] {
        case "online": return .online
        case "offline": return .offline
        case "pending": return .pending
        default: return .unknown
        }
    }

    private func canWake(_ profile: PCProfile) -> Bool {
        profile.canWakeDirectly || upsnapConfigured
    }

    private func row(_ profile: PCProfile) -> some View {
        let pcState = state(of: profile)
        let isWaking = wakingID == profile.id && waker.isBusy
        return HStack(spacing: 12) {
            Button {
                tap(profile)
            } label: {
                HStack(spacing: 12) {
                    Circle()
                        .fill(color(of: pcState))
                        .frame(width: 12, height: 12)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(profile.displayName)
                            .font(.headline)
                            .foregroundStyle(.primary)
                        Text(subtitle(profile, pcState: pcState, isWaking: isWaking))
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 8)
                    if isWaking {
                        ProgressView()
                    } else {
                        Text(actionLabel(profile, pcState: pcState))
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(pcState == .offline ? Color.orange : Color.accentColor)
                    }
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(session.state == .connecting || (waker.isBusy && !isWaking))

            if isWaking {
                Button("やめる") { waker.cancel(); wakingID = nil }
                    .font(.subheadline)
                    .buttonStyle(.borderless)
            } else {
                Button {
                    editing = EditTarget(profile: profile, token: store.token(for: profile.id), isNew: false)
                } label: {
                    Image(systemName: "slider.horizontal.3")
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.borderless)
                .accessibilityLabel("\(profile.displayName)の設定")
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
    }

    private func color(of pcState: PCState) -> Color {
        switch pcState {
        case .online: return .green
        case .offline: return Color.gray
        case .pending: return .orange
        case .unknown: return Color.gray.opacity(0.4)
        }
    }

    private func subtitle(_ profile: PCProfile, pcState: PCState, isWaking: Bool) -> String {
        if isWaking { return "起動を待っています" }
        switch pcState {
        case .online: return "オン"
        case .offline: return "オフ"
        case .pending: return "起動中"
        case .unknown: return upsnapConfigured && !statusLoaded ? "状態を確認中" : "状態は不明"
        }
    }

    private func actionLabel(_ profile: PCProfile, pcState: PCState) -> String {
        switch pcState {
        case .offline, .pending: return canWake(profile) ? "起こして接続" : "接続"
        default: return "接続"
        }
    }

    // MARK: 動き

    private func tap(_ profile: PCProfile) {
        let token = store.token(for: profile.id)
        if token.isEmpty || (profile.host.isEmpty && profile.hostRemote.isEmpty) {
            // 接続に必要なものが足りない。設定の画面を開く
            editing = EditTarget(profile: profile, token: token, isNew: false)
            return
        }
        let pcState = state(of: profile)
        if (pcState == .offline || pcState == .pending), canWake(profile) {
            wake(profile, token: token)
        } else {
            connect(profile, token)
        }
    }

    private func wake(_ profile: PCProfile, token: String) {
        Keychain.save("upsnapPassword", upsnapPassword)
        wakingID = profile.id
        let config = PCWaker.Config(
            mac: ContentView.cleaned(profile.mac),
            broadcast: ContentView.cleaned(profile.broadcast),
            upsnapURL: ContentView.cleaned(upsnapURL),
            upsnapEmail: ContentView.cleaned(upsnapEmail),
            upsnapPassword: upsnapPassword,
            deviceIP: profile.upsnapIP,
            hosts: [profile.host, profile.hostRemote].filter { !$0.isEmpty },
            port: Int(profile.port) ?? 8765)
        waker.wake(config) {
            wakingID = nil
            connect(profile, token)
        }
    }

    private func pollStatuses() async {
        guard upsnapConfigured else {
            statuses = [:]
            statusError = nil
            statusLoaded = false
            return
        }
        while !Task.isCancelled {
            do {
                let result = try await WakeOnLAN.upsnapStatuses(
                    baseURL: ContentView.cleaned(upsnapURL),
                    email: ContentView.cleaned(upsnapEmail),
                    password: upsnapPassword)
                if Task.isCancelled { return }
                statuses = result
                statusError = nil
            } catch {
                if Task.isCancelled { return }
                statuses = [:]
                statusError = "UpSnap から状態を読めません（\(error.localizedDescription)）"
            }
            statusLoaded = true
            try? await Task.sleep(nanoseconds: 6_000_000_000)
        }
    }
}

/// PC 1台の設定
private struct PCEditSheet: View {
    @Environment(\.dismiss) private var dismiss

    @State private var profile: PCProfile
    @State private var token: String
    @State private var showToken = false
    private let isNew: Bool
    private let onSave: (PCProfile, String) -> Void
    private let onDelete: (UUID) -> Void
    @State private var confirmDelete = false

    init(target: PCListSection.EditTarget,
         onSave: @escaping (PCProfile, String) -> Void,
         onDelete: @escaping (UUID) -> Void) {
        _profile = State(initialValue: target.profile)
        _token = State(initialValue: target.token)
        isNew = target.isNew
        self.onSave = onSave
        self.onDelete = onDelete
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("名前") {
                    TextField("例: 自宅の PC", text: $profile.name)
                }
                Section {
                    TextField("自宅のホスト（例: 192.168.0.200）", text: $profile.host)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    TextField("外出先のホスト（Tailscale、任意）", text: $profile.hostRemote)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    TextField("ポート", text: $profile.port)
                        .keyboardType(.numberPad)
                    HStack {
                        Group {
                            if showToken {
                                TextField("トークン", text: $token)
                            } else {
                                SecureField("トークン", text: $token)
                            }
                        }
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.asciiCapable)
                        .font(.body.monospaced())
                        Button {
                            showToken.toggle()
                        } label: {
                            Image(systemName: showToken ? "eye.slash" : "eye")
                                .foregroundStyle(.secondary)
                        }
                        .buttonStyle(.borderless)
                    }
                } header: {
                    Text("接続先")
                }
                Section {
                    TextField("MAC アドレス（有線）", text: $profile.mac)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.asciiCapable)
                        .font(.body.monospaced())
                    TextField("ブロードキャスト（例: 192.168.0.255）", text: $profile.broadcast)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.numbersAndPunctuation)
                    TextField("UpSnap に登録した IP（空なら自宅のホスト）", text: $profile.deviceIP)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.numbersAndPunctuation)
                } header: {
                    Text("起こすための設定")
                } footer: {
                    Text("自宅の Wi-Fi では、MAC アドレスとブロードキャストで直接起こせます。外出先では、UpSnap を通して起こします")
                }
                if !isNew {
                    Section {
                        Button("この PC を削除", role: .destructive) { confirmDelete = true }
                    }
                }
            }
            .navigationTitle(isNew ? "PC を追加" : "PC の設定")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("キャンセル") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") {
                        var saved = profile
                        saved.name = ContentView.cleaned(saved.name)
                        saved.host = ContentView.cleaned(saved.host)
                        saved.hostRemote = ContentView.cleaned(saved.hostRemote)
                        saved.port = ContentView.cleaned(saved.port).isEmpty ? "8765" : ContentView.cleaned(saved.port)
                        saved.mac = ContentView.cleaned(saved.mac)
                        saved.broadcast = ContentView.cleaned(saved.broadcast)
                        saved.deviceIP = ContentView.cleaned(saved.deviceIP)
                        onSave(saved, ContentView.cleaned(token))
                        dismiss()
                    }
                    .disabled(profile.host.trimmingCharacters(in: .whitespaces).isEmpty
                              && profile.hostRemote.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .confirmationDialog("この PC を削除しますか？", isPresented: $confirmDelete, titleVisibility: .visible) {
                Button("削除", role: .destructive) {
                    onDelete(profile.id)
                    dismiss()
                }
            }
        }
    }
}

/// UpSnap の接続の設定（全部の PC で共通）
private struct UpSnapSettingsSheet: View {
    @Environment(\.dismiss) private var dismiss
    @Binding var url: String
    @Binding var email: String
    @Binding var password: String
    let onDone: () -> Void

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("アドレス（例: 100.x.x.x:8090）", text: $url)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    TextField("メールアドレス", text: $email)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.emailAddress)
                    SecureField("パスワード", text: $password)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                } footer: {
                    Text("自宅の NAS などで動かしている UpSnap です。外出先では、Tailscale をオンにしておきます。パスワードは、この iPhone のキーチェーンにだけ入ります")
                }
            }
            .navigationTitle("UpSnap")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完了") {
                        onDone()
                        dismiss()
                    }
                }
            }
        }
    }
}
