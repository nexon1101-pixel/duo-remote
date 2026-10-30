import Foundation

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
        state = .disconnected
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
        case "error":
            errorMessage = (json["message"] as? String) ?? "サーバーからエラーが返りました"
        default:
            break
        }
    }

    private static func send(_ task: URLSessionWebSocketTask, _ object: [String: Any]) async throws {
        let data = try JSONSerialization.data(withJSONObject: object)
        try await task.send(.string(String(decoding: data, as: UTF8.self)))
    }
}
