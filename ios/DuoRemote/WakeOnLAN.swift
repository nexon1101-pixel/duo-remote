import Foundation
import Network
import SwiftUI

/// PC を起こす。2つの方法を、続けて試す。
/// - 自宅のネットワークでは、マジックパケットを直接送る（Wi-Fi でそのまま使える）
/// - 外出先では、自宅の NAS で動く UpSnap に頼む（Tailscale 経由）。UpSnap が自宅の中から信号を送る
enum WakeOnLAN {
    enum WakeError: LocalizedError {
        case invalidMAC
        case socket
        case upsnap(String)

        var errorDescription: String? {
            switch self {
            case .invalidMAC: return "MAC アドレスの形が正しくありません"
            case .socket: return "信号を送れませんでした"
            case .upsnap(let message): return message
            }
        }
    }

    /// "AA-BB-CC-DD-EE-FF" や "aa:bb:cc:dd:ee:ff" を6バイトにする
    static func parseMAC(_ text: String) -> [UInt8]? {
        let hex = text.filter { $0.isHexDigit }
        guard hex.count == 12 else { return nil }
        let chars = Array(hex)
        var bytes: [UInt8] = []
        for i in stride(from: 0, to: 12, by: 2) {
            guard let value = UInt8(String(chars[i...i + 1]), radix: 16) else { return nil }
            bytes.append(value)
        }
        return bytes
    }

    /// マジックパケット（FF を6つ、続けて MAC を16回）
    static func magicPacket(mac: [UInt8]) -> [UInt8] {
        Array(repeating: 0xFF, count: 6) + Array((0..<16).map { _ in mac }.joined())
    }

    /// ブロードキャストの宛先へ、UDP で送る（ポート 9 と 7）
    static func sendMagicPacket(mac macText: String, broadcast: String) throws {
        guard let mac = parseMAC(macText) else { throw WakeError.invalidMAC }
        let packet = magicPacket(mac: mac)
        let fd = socket(AF_INET, SOCK_DGRAM, 0)
        guard fd >= 0 else { throw WakeError.socket }
        defer { close(fd) }
        var yes: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_BROADCAST, &yes, socklen_t(MemoryLayout<Int32>.size))

        var sent = false
        for port: UInt16 in [9, 7] {
            var addr = sockaddr_in()
            addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
            addr.sin_family = sa_family_t(AF_INET)
            addr.sin_port = port.bigEndian
            guard inet_pton(AF_INET, broadcast, &addr.sin_addr) == 1 else { throw WakeError.socket }
            let result = withUnsafePointer(to: &addr) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    sendto(fd, packet, packet.count, 0, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
                }
            }
            if result == packet.count { sent = true }
        }
        if !sent { throw WakeError.socket }
    }

    // MARK: UpSnap

    private static func upsnapRoot(_ baseURL: String) throws -> URL {
        var base = baseURL.trimmingCharacters(in: .whitespaces)
        if !base.contains("://") { base = "http://" + base }
        while base.hasSuffix("/") { base.removeLast() }
        guard let root = URL(string: base) else { throw WakeError.upsnap("UpSnap のアドレスが正しくありません") }
        return root
    }

    /// ログインして、UpSnap の機器の一覧（IP アドレス、名前、状態）を読む
    private static func upsnapDevices(root: URL, token: String) async throws -> [[String: Any]] {
        var listComponents = URLComponents(url: root.appendingPathComponent("api/collections/devices/records"), resolvingAgainstBaseURL: false)!
        listComponents.queryItems = [URLQueryItem(name: "perPage", value: "200")]
        var listRequest = URLRequest(url: listComponents.url!, timeoutInterval: 8)
        listRequest.setValue(token, forHTTPHeaderField: "Authorization")
        let (listData, listResponse) = try await session.data(for: listRequest)
        try check(listResponse)
        guard let list = try JSONSerialization.jsonObject(with: listData) as? [String: Any],
              let items = list["items"] as? [[String: Any]] else {
            throw WakeError.upsnap("PC の一覧を取得できませんでした")
        }
        return items
    }

    /// UpSnap のアドレスにつながるか（2.5秒で打ち切る）
    static func upsnapReachable(_ baseURL: String) async -> Bool {
        guard let root = try? upsnapRoot(baseURL) else { return false }
        var request = URLRequest(url: root.appendingPathComponent("api/health"), timeoutInterval: 2.5)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        guard let (_, response) = try? await session.data(for: request),
              let http = response as? HTTPURLResponse else { return false }
        return (200..<300).contains(http.statusCode)
    }

    struct UpSnapDevice: Identifiable, Equatable {
        let id: String
        let name: String
        let ip: String
        /// "online"、"offline"、"pending"
        let status: String
    }

    /// UpSnap に登録された機器の一覧（名前、IP アドレス、状態）
    static func upsnapDeviceList(baseURL: String, email: String, password: String) async throws -> [UpSnapDevice] {
        let root = try upsnapRoot(baseURL)
        let token = try await login(root: root, email: email, password: password)
        return try await upsnapDevices(root: root, token: token).compactMap { item in
            guard let id = item["id"] as? String, let ip = item["ip"] as? String else { return nil }
            return UpSnapDevice(id: id, name: (item["name"] as? String) ?? ip, ip: ip,
                                status: (item["status"] as? String) ?? "")
        }
        .sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    /// UpSnap に登録された機器の状態を、IP アドレスごとに返す（"online"、"offline"、"pending"）
    static func upsnapStatuses(baseURL: String, email: String, password: String) async throws -> [String: String] {
        let root = try upsnapRoot(baseURL)
        let token = try await login(root: root, email: email, password: password)
        var result: [String: String] = [:]
        for item in try await upsnapDevices(root: root, token: token) {
            if let ip = item["ip"] as? String, let status = item["status"] as? String {
                result[ip] = status
            }
        }
        return result
    }

    /// UpSnap にログインし、IP アドレスが合う機器を起こす
    static func wakeWithUpSnap(baseURL: String, email: String, password: String, deviceIP: String) async throws {
        let root = try upsnapRoot(baseURL)
        let token = try await login(root: root, email: email, password: password)
        let items = try await upsnapDevices(root: root, token: token)
        let wanted = deviceIP.trimmingCharacters(in: .whitespaces)
        guard let device = items.first(where: { ($0["ip"] as? String) == wanted }),
              let id = device["id"] as? String else {
            throw WakeError.upsnap("UpSnap に \(wanted) の PC が登録されていません")
        }

        // UpSnap は、PC が起動するまで返事を返さないことがある。数秒で返事がなくても、
        // 信号はすでに送られているので、失敗にはしない（起動したかは、あとで PC への接続で確認する）
        var wakeRequest = URLRequest(url: root.appendingPathComponent("api/upsnap/wake/\(id)"), timeoutInterval: 5)
        wakeRequest.setValue(token, forHTTPHeaderField: "Authorization")
        do {
            let (_, wakeResponse) = try await session.data(for: wakeRequest)
            try check(wakeResponse)
        } catch let error as URLError where error.code == .timedOut {
            return
        }
    }

    /// 画面に出す、エラーの説明（日本語にする）
    static func message(for error: Error) -> String {
        if let urlError = error as? URLError {
            switch urlError.code {
            case .timedOut: return "応答がありません（タイムアウト）"
            case .notConnectedToInternet, .dataNotAllowed: return "ネットワークに接続されていません"
            case .cannotConnectToHost, .cannotFindHost, .networkConnectionLost, .dnsLookupFailed:
                return "UpSnap に接続できません"
            default: break
            }
        }
        return error.localizedDescription
    }

    private static let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 8
        config.waitsForConnectivity = false
        return URLSession(configuration: config)
    }()

    private static func check(_ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw WakeError.upsnap("UpSnap に拒否されました")
        }
    }

    /// 管理者（_superusers）で試し、だめなら一般のユーザー（users）で試す
    private static func login(root: URL, email: String, password: String) async throws -> String {
        for collection in ["_superusers", "users"] {
            var request = URLRequest(url: root.appendingPathComponent("api/collections/\(collection)/auth-with-password"), timeoutInterval: 8)
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: ["identity": email, "password": password])
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else { continue }
            if (200..<300).contains(http.statusCode),
               let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
               let token = json["token"] as? String {
                return token
            }
        }
        throw WakeError.upsnap("UpSnap にログインできませんでした。メールアドレスとパスワードを確認してください")
    }

    // MARK: 起動の待ち受け

    /// ホストの TCP ポートにつながるか（3秒で打ち切る）
    static func isReachable(host: String, port: Int) async -> Bool {
        guard let nwPort = NWEndpoint.Port(rawValue: UInt16(clamping: port)) else { return false }
        let connection = NWConnection(host: NWEndpoint.Host(host), port: nwPort, using: .tcp)
        return await withCheckedContinuation { continuation in
            let lock = NSLock()
            var finished = false
            func finish(_ value: Bool) {
                lock.lock()
                defer { lock.unlock() }
                guard !finished else { return }
                finished = true
                connection.cancel()
                continuation.resume(returning: value)
            }
            connection.stateUpdateHandler = { state in
                switch state {
                case .ready: finish(true)
                case .failed, .cancelled: finish(false)
                default: break
                }
            }
            connection.start(queue: .global())
            DispatchQueue.global().asyncAfter(deadline: .now() + 3) { finish(false) }
        }
    }
}

/// 「PC を起こす」の動きと、画面に出す状態
@MainActor
final class PCWaker: ObservableObject {
    enum Phase: Equatable {
        case idle
        case sending
        case waiting      // 信号を送った。PC が起きるのを待っている
        case failed(String)
    }

    @Published private(set) var phase: Phase = .idle
    /// 結果の説明（「自宅のネットワークに直接送りました」など）
    @Published private(set) var detail = ""

    private var task: Task<Void, Never>?

    var isBusy: Bool { phase == .sending || phase == .waiting }

    struct Config {
        var mac: String
        var broadcast: String
        var upsnapURL: String
        var upsnapEmail: String
        var upsnapPassword: String
        var deviceIP: String
        /// 起きたかを確かめるための、接続先（家と外）とポート
        var hosts: [String]
        var port: Int
    }

    static func canWake(_ config: Config) -> Bool {
        !config.mac.isEmpty || (!config.upsnapURL.isEmpty && !config.upsnapEmail.isEmpty)
    }

    /// 起こして、つながるようになったら onReady を呼ぶ
    func wake(_ config: Config, onReady: @escaping @MainActor () -> Void) {
        guard !isBusy else { return }
        task?.cancel()
        phase = .sending
        detail = ""
        task = Task { [weak self] in
            guard let self else { return }
            var notes: [String] = []
            var errors: [String] = []

            if !config.mac.isEmpty {
                let broadcast = config.broadcast.isEmpty ? "255.255.255.255" : config.broadcast
                do {
                    try WakeOnLAN.sendMagicPacket(mac: config.mac, broadcast: broadcast)
                    notes.append("電源を入れる信号を送信しました")
                } catch {
                    errors.append(WakeOnLAN.message(for: error))
                }
            }
            if !config.upsnapURL.isEmpty, !config.upsnapEmail.isEmpty {
                do {
                    try await WakeOnLAN.wakeWithUpSnap(
                        baseURL: config.upsnapURL, email: config.upsnapEmail,
                        password: config.upsnapPassword, deviceIP: config.deviceIP)
                    notes.append("電源を入れています。起動したら自動で接続します")
                } catch {
                    errors.append(WakeOnLAN.message(for: error))
                }
            }
            if Task.isCancelled { return }
            if notes.isEmpty {
                self.phase = .failed(errors.first ?? "電源を入れる方法が設定されていません")
                return
            }
            self.detail = notes.joined(separator: "。")
            self.phase = .waiting

            // 最長3分、5秒おきに、PC につながるかを見る
            for _ in 0..<36 {
                try? await Task.sleep(nanoseconds: 5_000_000_000)
                if Task.isCancelled { return }
                for host in config.hosts where !host.isEmpty {
                    if await WakeOnLAN.isReachable(host: host, port: config.port) {
                        self.phase = .idle
                        self.detail = ""
                        onReady()
                        return
                    }
                }
            }
            self.phase = .failed("3分待っても PC に接続できません。電源と設定を確認してください")
        }
    }

    func cancel() {
        task?.cancel()
        phase = .idle
        detail = ""
    }
}
