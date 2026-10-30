import Foundation

/// PCのウィンドウ（一覧の1行）。
struct RemoteWindow: Identifiable, Equatable {
    let id: Int
    let title: String
    let app: String
}

/// サーバーとのWebSocket接続。通信の取り決めは docs/protocol.md を参照。
@MainActor
final class RemoteSession: ObservableObject {
    enum State: Equatable {
        case disconnected
        case connecting
        case connected
    }

    @Published private(set) var state: State = .disconnected
    @Published private(set) var errorMessage: String?
    @Published private(set) var videoSize: CGSize = .zero

    /// 接続中に出すお知らせ（サーバーからのエラーなど）。数秒で消す
    @Published private(set) var notice: String?
    /// PCのウィンドウの一覧。requestWindows() で読み直す
    @Published private(set) var windows: [RemoteWindow] = []
    @Published private(set) var isLoadingWindows = false
    /// 表示しているウィンドウ。nilなら画面全体
    @Published private(set) var currentWindow: RemoteWindow?
    /// PCのIMEがオンか。サーバーが知らせてこないときはnil
    @Published private(set) var imeOn: Bool?
    private var noticeTask: Task<Void, Never>?

    let renderer = H264Renderer()

    private var task: URLSessionWebSocketTask?
    private var receiveTask: Task<Void, Never>?

    func connect(host: String, port: Int, token: String) {
        disconnect()
        errorMessage = nil

        guard let url = URL(string: "ws://\(host):\(port)/") else {
            errorMessage = "接続先の形式が正しくありません"
            return
        }
        state = .connecting

        let task = URLSession.shared.webSocketTask(with: url)
        task.maximumMessageSize = 16 * 1024 * 1024
        self.task = task
        task.resume()

        receiveTask = Task { [weak self] in
            do {
                try await Self.send(task, ["t": "hello", "v": 1, "token": token])
                while !Task.isCancelled {
                    let message = try await task.receive()
                    guard let self else { return }
                    self.handle(message)
                }
            } catch {
                guard let self, !Task.isCancelled else { return }
                self.fail(error)
            }
        }
    }

    func disconnect() {
        receiveTask?.cancel()
        receiveTask = nil
        task?.cancel(with: .normalClosure, reason: nil)
        task = nil
        renderer.reset()
        resetView()
        state = .disconnected
    }

    private func resetView() {
        imeOn = nil
        windows = []
        isLoadingWindows = false
        currentWindow = nil
        noticeTask?.cancel()
        notice = nil
    }

    // MARK: - キーと文字

    /// 確定した文字を送る。長いときは、決まりの上限（4096文字）ごとに分ける
    func sendText(_ text: String) {
        var rest = Substring(text)
        while !rest.isEmpty {
            let part = rest.prefix(4096)
            send(["t": "text", "s": String(part)])
            rest = rest.dropFirst(part.count)
        }
    }

    /// 仮想キーコードのキーを押して離す。ctrlなら、Ctrlを押したまま押す
    func tapKey(vk: Int, ctrl: Bool = false) {
        if ctrl { send(["t": "key", "vk": KeyMap.vkControl, "d": "down"]) }
        send(["t": "key", "vk": vk, "d": "down"])
        send(["t": "key", "vk": vk, "d": "up"])
        if ctrl { send(["t": "key", "vk": KeyMap.vkControl, "d": "up"]) }
    }

    /// キーの位置（スキャンコード）で押す・離す。Bluetoothキーボード用
    func sendScan(_ sc: Int, down: Bool) {
        send(["t": "key", "sc": sc, "d": down ? "down" : "up"])
    }

    func setIME(_ on: Bool) {
        send(["t": "ime", "on": on])
    }

    // MARK: - 表示する範囲

    func requestWindows() {
        isLoadingWindows = true
        send(["t": "list_windows"])
    }

    /// ウィンドウ1つだけを表示する。fitがあれば、ウィンドウの大きさをその縦横比に変える（iPhoneの画面のポイント数）
    func showWindow(_ window: RemoteWindow, fit: CGSize?) {
        var message: [String: Any] = ["t": "mode", "mode": "window", "id": window.id]
        if let fit, fit.width > 0, fit.height > 0 {
            message["fit_w"] = Double(fit.width)
            message["fit_h"] = Double(fit.height)
        }
        send(message)
    }

    func showScreen() {
        send(["t": "mode", "mode": "screen"])
    }

    private func show(notice text: String) {
        notice = text
        noticeTask?.cancel()
        noticeTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(4))
            guard !Task.isCancelled else { return }
            self?.notice = nil
        }
    }

    /// 入力などの制御メッセージを送る。呼んだ順にサーバーへ届く。
    /// 送れなかった場合は、受信の側で切断として扱われるので、ここでは何もしない。
    func send(_ object: [String: Any]) {
        guard state == .connected, let task,
              let data = try? JSONSerialization.data(withJSONObject: object) else { return }
        task.send(.string(String(decoding: data, as: UTF8.self))) { _ in }
    }

    private func fail(_ error: Error) {
        let wasConnecting = state == .connecting
        if errorMessage == nil {
            errorMessage = wasConnecting
                ? "接続できませんでした。PCのサーバーが動いているか、アドレスとトークンを確認してください"
                : "接続が切れました"
        }
        receiveTask = nil
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        renderer.reset()
        resetView()
        state = .disconnected
    }

    private func handle(_ message: URLSessionWebSocketTask.Message) {
        switch message {
        case .data(let data):
            // 先頭: フラグ(1) + タイムスタンプ(4) + H.264
            guard data.count > 5 else { return }
            let timestamp = data[1..<5].reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
            renderer.enqueue(accessUnit: data.dropFirst(5), timestampMs: timestamp)
        case .string(let text):
            handleControl(text)
        @unknown default:
            break
        }
    }

    private func handleControl(_ text: String) {
        guard let data = text.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = json["t"] as? String else { return }

        switch type {
        case "welcome":
            if let w = json["w"] as? Int, let h = json["h"] as? Int {
                videoSize = CGSize(width: w, height: h)
            }
            state = .connected
        case "resize":
            if let w = json["w"] as? Int, let h = json["h"] as? Int {
                videoSize = CGSize(width: w, height: h)
            }
        case "ime_state":
            imeOn = json["on"] as? Bool
        case "windows":
            let list = json["list"] as? [[String: Any]] ?? []
            windows = list.compactMap { item in
                guard let id = item["id"] as? Int else { return nil }
                return RemoteWindow(id: id, title: item["title"] as? String ?? "", app: item["app"] as? String ?? "")
            }
            isLoadingWindows = false
        case "mode":
            if json["mode"] as? String == "window", let id = json["id"] as? Int {
                let title = json["title"] as? String ?? ""
                currentWindow = windows.first { $0.id == id } ?? RemoteWindow(id: id, title: title, app: "")
            } else {
                currentWindow = nil
            }
        case "error":
            let message = (json["message"] as? String) ?? "サーバーからエラーが返りました"
            if state == .connected {
                show(notice: message)
            } else {
                errorMessage = message
            }
        default:
            break
        }
    }

    private static func send(_ task: URLSessionWebSocketTask, _ object: [String: Any]) async throws {
        let data = try JSONSerialization.data(withJSONObject: object)
        try await task.send(.string(String(decoding: data, as: UTF8.self)))
    }
}
