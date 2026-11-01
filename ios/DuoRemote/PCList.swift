import SwiftUI

/// つなぐ PC 1台分の接続先
struct PCProfile: Equatable {
    /// 同じ PC かを見分ける印（UpSnap の機器の ID、または "manual"）
    var id = "manual"
    var name = ""
    /// 自宅のホスト
    var host = ""
    /// 外出先（Tailscale）のホスト
    var hostRemote = ""
    var port = "8765"
}

/// UpSnap の機器ごとに、端末に覚えておく接続の設定。トークンは、キーチェーンにしまう
struct DeviceSettings: Codable, Equatable {
    var hostRemote = ""
    var port = "8765"
}

enum DeviceSettingsStore {
    private static let key = "upsnapDeviceSettings"

    static func load(_ id: String) -> DeviceSettings {
        all()[id] ?? DeviceSettings()
    }

    static func save(_ settings: DeviceSettings, token: String, id: String) {
        var map = all()
        map[id] = settings
        if let data = try? JSONEncoder().encode(map) {
            UserDefaults.standard.set(data, forKey: key)
        }
        Keychain.save("token.\(id)", token)
    }

    static func token(_ id: String) -> String {
        Keychain.load("token.\(id)") ?? ""
    }

    private static func all() -> [String: DeviceSettings] {
        guard let data = UserDefaults.standard.data(forKey: key),
              let map = try? JSONDecoder().decode([String: DeviceSettings].self, from: data) else { return [:] }
        return map
    }
}

/// 接続の画面の上の部分。「UpSnap」と「手入力」のタブで分ける
struct PCListSection: View {
    /// つなぐ PC と、そのトークン。つなぐ処理は、呼ぶ側が持つ
    let connect: (PCProfile, String) -> Void

    private enum Tab: String { case upsnap, manual }
    @AppStorage("connectTab") private var tabRaw = Tab.upsnap.rawValue

    private var tab: Binding<Tab> {
        Binding(get: { Tab(rawValue: tabRaw) ?? .upsnap }, set: { tabRaw = $0.rawValue })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Picker("接続の方法", selection: tab) {
                Text("UpSnap").tag(Tab.upsnap)
                Text("手入力").tag(Tab.manual)
            }
            .pickerStyle(.segmented)

            switch tab.wrappedValue {
            case .upsnap: UpSnapTab(connect: connect)
            case .manual: ManualTab(connect: connect)
            }
        }
    }
}

// MARK: - 手入力

private struct ManualTab: View {
    @EnvironmentObject private var session: RemoteSession
    let connect: (PCProfile, String) -> Void

    @AppStorage("host") private var host = ""
    @AppStorage("hostRemote") private var hostRemote = ""
    @AppStorage("port") private var port = "8765"
    @State private var token = ""
    @State private var showToken = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 12) {
                row("server.rack") {
                    TextField("自宅のホスト（例: 192.168.0.200）", text: $host)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                }
                Divider().overlay(Color.white.opacity(0.1))
                row("globe") {
                    TextField("外出先のホスト（Tailscale、任意）", text: $hostRemote)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                }
                Divider().overlay(Color.white.opacity(0.1))
                row("number") {
                    TextField("ポート", text: $port)
                        .keyboardType(.numberPad)
                }
                Divider().overlay(Color.white.opacity(0.1))
                row("key.fill") {
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
                        Image(systemName: showToken ? "eye.slash" : "eye").foregroundStyle(.secondary)
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel(showToken ? "トークンを非表示" : "トークンを表示")
                }
            }
            .padding(16)
            .glass(in: RoundedRectangle(cornerRadius: 22, style: .continuous))

            Button {
                let cleanedToken = ContentView.cleaned(token)
                Keychain.save("token", cleanedToken)
                var profile = PCProfile()
                profile.host = ContentView.cleaned(host)
                profile.hostRemote = ContentView.cleaned(hostRemote)
                profile.port = ContentView.cleaned(port)
                host = profile.host
                hostRemote = profile.hostRemote
                port = profile.port
                connect(profile, cleanedToken)
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: "bolt.horizontal.fill")
                    Text("接続")
                }
                .font(.headline)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 6)
            }
            .glassButton(prominent: true)
            .controlSize(.large)
            .disabled((host.isEmpty && hostRemote.isEmpty) || token.isEmpty || session.state == .connecting)
        }
        .onAppear { token = Keychain.load("token") ?? "" }
    }

    private func row<Content: View>(_ icon: String, @ViewBuilder content: () -> Content) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon).foregroundStyle(.secondary).frame(width: 22)
            content()
        }
    }
}

// MARK: - UpSnap

private struct UpSnapTab: View {
    @EnvironmentObject private var session: RemoteSession
    let connect: (PCProfile, String) -> Void

    @AppStorage("upsnapURL") private var url = ""
    @AppStorage("upsnapEmail") private var email = ""
    @State private var password = ""
    /// ログインできた設定。これがあるときは、機器の一覧を出す
    @AppStorage("upsnapLoggedIn") private var loggedIn = false

    @State private var devices: [WakeOnLAN.UpSnapDevice] = []
    @State private var loaded = false
    @State private var message: String?
    @State private var loggingIn = false

    @StateObject private var waker = PCWaker()
    @State private var wakingID: String?
    @State private var editing: WakeOnLAN.UpSnapDevice?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if loggedIn {
                deviceList
            } else {
                loginForm
            }
        }
        .onAppear { password = Keychain.load("upsnapPassword") ?? "" }
        .sheet(item: $editing) { device in
            DeviceSettingsSheet(device: device)
        }
        // ログインしている間、画面が出ている6秒おきに、一覧を読み直す
        .task(id: loggedIn) {
            guard loggedIn else { return }
            while !Task.isCancelled {
                await reload()
                try? await Task.sleep(nanoseconds: 6_000_000_000)
            }
        }
    }

    // MARK: ログイン

    private var loginForm: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 12) {
                field("externaldrive.connected.to.line.below") {
                    TextField("UpSnap のアドレス（例: 100.x.x.x:8090）", text: $url)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                }
                Divider().overlay(Color.white.opacity(0.1))
                field("envelope") {
                    TextField("メールアドレス", text: $email)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.emailAddress)
                }
                Divider().overlay(Color.white.opacity(0.1))
                field("lock") {
                    SecureField("パスワード", text: $password)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
            }
            .padding(16)
            .glass(in: RoundedRectangle(cornerRadius: 22, style: .continuous))

            Button {
                Task { await login() }
            } label: {
                HStack(spacing: 10) {
                    if loggingIn { ProgressView().tint(.white) } else { Image(systemName: "person.crop.circle") }
                    Text(loggingIn ? "ログイン中…" : "ログイン")
                }
                .font(.headline)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 6)
            }
            .glassButton(prominent: true)
            .controlSize(.large)
            .disabled(url.isEmpty || email.isEmpty || password.isEmpty || loggingIn)

            if let message {
                Label(message, systemImage: "exclamationmark.triangle.fill")
                    .font(.footnote)
                    .foregroundStyle(.orange)
            }
            Text("自宅の NAS などで動かしている UpSnap にログインすると、登録した PC の一覧と、オン・オフが見えます。外出先では、Tailscale をオンにしてください。パスワードは、この iPhone のキーチェーンにだけ入ります")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
    }

    private func login() async {
        loggingIn = true
        defer { loggingIn = false }
        do {
            let list = try await WakeOnLAN.upsnapDeviceList(
                baseURL: ContentView.cleaned(url), email: ContentView.cleaned(email), password: password)
            Keychain.save("upsnapPassword", password)
            url = ContentView.cleaned(url)
            email = ContentView.cleaned(email)
            devices = list
            loaded = true
            message = nil
            loggedIn = true
        } catch {
            message = error.localizedDescription
        }
    }

    private func logout() {
        waker.cancel()
        wakingID = nil
        Keychain.save("upsnapPassword", "")
        password = ""
        devices = []
        loaded = false
        loggedIn = false
    }

    // MARK: 機器の一覧

    private var deviceList: some View {
        VStack(alignment: .leading, spacing: 10) {
            VStack(spacing: 0) {
                if devices.isEmpty {
                    Text(loaded ? "UpSnap に PC が登録されていません" : "読み込み中…")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(16)
                } else {
                    ForEach(Array(devices.enumerated()), id: \.element.id) { index, device in
                        if index > 0 { Divider().overlay(Color.white.opacity(0.1)) }
                        deviceRow(device)
                    }
                }
                Divider().overlay(Color.white.opacity(0.1))
                HStack {
                    Text(email)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                    Spacer()
                    Button("ログアウト", role: .destructive) { logout() }
                        .font(.subheadline)
                        .buttonStyle(.borderless)
                }
                .padding(16)
            }
            .glass(in: RoundedRectangle(cornerRadius: 22, style: .continuous))

            if let message {
                Label(message, systemImage: "exclamationmark.triangle.fill")
                    .font(.footnote)
                    .foregroundStyle(.orange)
            }
            if !waker.detail.isEmpty {
                Text(waker.detail).font(.footnote).foregroundStyle(.secondary)
            }
            if case .failed(let failure) = waker.phase {
                Label(failure, systemImage: "exclamationmark.triangle.fill")
                    .font(.footnote)
                    .foregroundStyle(.orange)
            }
        }
    }

    private func deviceRow(_ device: WakeOnLAN.UpSnapDevice) -> some View {
        let isWaking = wakingID == device.id && waker.isBusy
        let isOnline = device.status == "online"
        return HStack(spacing: 12) {
            Button {
                tap(device)
            } label: {
                HStack(spacing: 12) {
                    Circle()
                        .fill(dotColor(device.status))
                        .frame(width: 12, height: 12)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(device.name)
                            .font(.headline)
                            .foregroundStyle(.primary)
                        Text(isWaking ? "起動を待っています" : statusText(device.status))
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 8)
                    if isWaking {
                        ProgressView()
                    } else {
                        Text(isOnline ? "接続" : "起こして接続")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(isOnline ? Color.accentColor : Color.orange)
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
                    editing = device
                } label: {
                    Image(systemName: "slider.horizontal.3").foregroundStyle(.secondary)
                }
                .buttonStyle(.borderless)
                .accessibilityLabel("\(device.name)の接続の設定")
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
    }

    private func dotColor(_ status: String) -> Color {
        switch status {
        case "online": return .green
        case "offline": return .gray
        case "pending": return .orange
        default: return Color.gray.opacity(0.4)
        }
    }

    private func statusText(_ status: String) -> String {
        switch status {
        case "online": return "オン"
        case "offline": return "オフ"
        case "pending": return "起動中"
        default: return "状態は不明"
        }
    }

    // MARK: 動き

    private func profile(for device: WakeOnLAN.UpSnapDevice) -> PCProfile {
        let settings = DeviceSettingsStore.load(device.id)
        return PCProfile(id: device.id, name: device.name, host: device.ip,
                         hostRemote: settings.hostRemote, port: settings.port)
    }

    private func tap(_ device: WakeOnLAN.UpSnapDevice) {
        let token = DeviceSettingsStore.token(device.id)
        if token.isEmpty {
            // 初めてのときは、トークンを聞く
            editing = device
            return
        }
        let pc = profile(for: device)
        if device.status == "online" {
            connect(pc, token)
        } else {
            wake(device, pc: pc, token: token)
        }
    }

    private func wake(_ device: WakeOnLAN.UpSnapDevice, pc: PCProfile, token: String) {
        wakingID = device.id
        let config = PCWaker.Config(
            mac: "", broadcast: "",
            upsnapURL: ContentView.cleaned(url),
            upsnapEmail: ContentView.cleaned(email),
            upsnapPassword: password,
            deviceIP: device.ip,
            hosts: [pc.host, pc.hostRemote].filter { !$0.isEmpty },
            port: Int(pc.port) ?? 8765)
        waker.wake(config) {
            wakingID = nil
            connect(pc, token)
        }
    }

    private func reload() async {
        do {
            let list = try await WakeOnLAN.upsnapDeviceList(
                baseURL: ContentView.cleaned(url), email: ContentView.cleaned(email), password: password)
            if Task.isCancelled { return }
            devices = list
            message = nil
        } catch {
            if Task.isCancelled { return }
            message = "UpSnap から読めません（\(error.localizedDescription)）"
        }
        loaded = true
    }

    private func field<Content: View>(_ icon: String, @ViewBuilder content: () -> Content) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon).foregroundStyle(.secondary).frame(width: 22)
            content()
        }
    }
}

/// UpSnap の機器 1台の、接続の設定（トークン、外出先のホスト、ポート）
private struct DeviceSettingsSheet: View {
    @Environment(\.dismiss) private var dismiss
    let device: WakeOnLAN.UpSnapDevice

    @State private var settings: DeviceSettings
    @State private var token: String
    @State private var showToken = false

    init(device: WakeOnLAN.UpSnapDevice) {
        self.device = device
        _settings = State(initialValue: DeviceSettingsStore.load(device.id))
        _token = State(initialValue: DeviceSettingsStore.token(device.id))
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack {
                        Text("自宅のホスト")
                        Spacer()
                        Text(device.ip).foregroundStyle(.secondary)
                    }
                    TextField("外出先のホスト（Tailscale、任意）", text: $settings.hostRemote)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    TextField("ポート", text: $settings.port)
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
                            Image(systemName: showToken ? "eye.slash" : "eye").foregroundStyle(.secondary)
                        }
                        .buttonStyle(.borderless)
                    }
                } footer: {
                    Text("自宅のホストは、UpSnap に登録した IP アドレスです。外では、Tailscale のアドレスでつなぎます。トークンは、この iPhone のキーチェーンにだけ入ります")
                }
            }
            .navigationTitle(device.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("キャンセル") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") {
                        var saved = settings
                        saved.hostRemote = ContentView.cleaned(saved.hostRemote)
                        let port = ContentView.cleaned(saved.port)
                        saved.port = port.isEmpty ? "8765" : port
                        DeviceSettingsStore.save(saved, token: ContentView.cleaned(token), id: device.id)
                        dismiss()
                    }
                }
            }
        }
    }
}
