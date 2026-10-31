import Foundation
import UIKit

/// PCのウィンドウ（一覧の1行）。
struct RemoteWindow: Identifiable, Equatable {
    let id: Int
    let title: String
    let app: String
    /// 出ている場所: main（本物の主モニター）、virtual（仮想画面）、other、none
    var place: String = "main"
}

/// 今、何を映しているか
enum ViewMode: Equatable {
    /// 本物の主モニター全体
    case screen
    /// 仮想画面（仮想ディスプレイ）全体
    case desktop
    /// ウィンドウ1つだけ
    case window
}

/// つなぎ直したときに、同じ表示に戻すため、最後に頼んだ表示を覚えておく
private enum DisplayRequest {
    case desktop(fit: CGSize?)
    case window(RemoteWindow, fit: CGSize?, toVirtual: Bool)
}

/// 映像の画質。外（4G/5G）では低くすると、遅れがたまりにくい。
enum VideoQuality: String, CaseIterable, Identifiable {
    case high, medium, low

    var id: String { rawValue }

    var label: String {
        switch self {
        case .high: "高（元の大きさ、60fps）"
        case .medium: "中（1080、60fps）"
        case .low: "低（720、30fps）"
        }
    }

    /// hello で送る値
    var helloFields: [String: Any] {
        switch self {
        case .high: ["max_height": 0, "bitrate": 8000, "fps": 60]
        case .medium: ["max_height": 1080, "bitrate": 4000, "fps": 60]
        case .low: ["max_height": 720, "bitrate": 1500, "fps": 30]
        }
    }
}

/// サーバーとのWebSocket接続。通信の取り決めは docs/protocol.md を参照。
@MainActor
final class RemoteSession: ObservableObject {
    enum State: Equatable {
        case disconnected
        case connecting
        case connected
        /// 切れたので、つなぎ直している
        case reconnecting
    }

    /// 切れたときに、つなぎ直す回数の上限
    static let maxReconnectAttempts = 6
    /// 接続を始めてから、welcome を待つ時間
    static let connectTimeout: Duration = .seconds(10)
    /// 候補が2つ以上あるとき、最初の候補だけは短く待つ（だめなら、すぐ次の候補を試す）
    static let firstCandidateTimeout: Duration = .seconds(4)
    /// 接続中に ping を送る間隔と、pong が来なければ切れたとみなす時間
    static let pingInterval: Duration = .seconds(5)
    static let pingTimeout: TimeInterval = 10

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
    @Published private(set) var viewMode: ViewMode = .screen
    /// PCに、仮想ディスプレイのドライバーが入っている
    @Published private(set) var virtualAvailable = false
    /// PCのIMEがオンか。サーバーが知らせてこないときはnil
    @Published private(set) var imeOn: Bool?
    /// 往復の時間（ミリ秒）。ping を送ってから pong が来るまで
    @Published private(set) var latencyMs: Int?
    /// 今つながっているホスト（候補の1つ）。つながっていないときはnil
    @Published private(set) var activeHost: String?
    private var noticeTask: Task<Void, Never>?

    let renderer = H264Renderer()
    let audio = AudioPlayer()
    /// サーバーが、PCの音を送っている
    @Published private(set) var audioOn = false
    /// 音を始められなかった回数。画面側が、スイッチを戻すために見る
    @Published private(set) var audioFailures = 0
    /// 音を鳴らしたいか（画面のスイッチ）と、他のアプリの音と混ぜるか
    private var audioWanted = false
    private var audioMix = false

    private var task: URLSessionWebSocketTask?
    private var receiveTask: Task<Void, Never>?

    /// つなぎ直すときに使う、接続先
    private var target: (hosts: [String], port: Int, token: String)?
    /// 今試している候補の番号（target.hosts の中）
    private var hostIndex = 0
    /// 最後にwelcomeまで届いた候補の番号。つなぎ直すときは、ここから試す
    private var lastGoodIndex = 0
    /// 今回の順番で、すでに試した候補の数
    private var triedInRound = 0
    /// 今の接続で、welcome が届いた
    private var welcomed = false
    private var reconnectAttempts = 0
    private var reconnectTask: Task<Void, Never>?
    /// サーバーに断られた（トークンが違うなど）。このときは、つなぎ直さない
    private var rejected = false
    /// 最後に選んだウィンドウの大きさの合わせ先。つなぎ直したときに、同じ表示に戻すため
    private var lastDisplay: DisplayRequest?
    private var quality = VideoQuality.high
    /// アプリが裏に回ったので、接続を閉じている。前に戻ったら、つなぎ直す
    private var suspended = false

    /// 接続を始めてから welcome が来るまでの見張り
    private var connectWatchdog: Task<Void, Never>?
    /// 接続中の ping の送信と、pong の見張り
    private var heartbeat: Task<Void, Never>?
    private var pingCounter = 0
    /// 送った ping の番号と、送った時刻
    private var pendingPings: [Int: Date] = [:]

    var isConnectedOrReconnecting: Bool { state == .connected || state == .reconnecting }

    init() {
        audio.onFailure = { [weak self] message in
            Task { @MainActor in
                guard let self else { return }
                self.audioOn = false
                self.audioFailures += 1
                self.show(notice: "音: \(message)")
            }
        }
    }

    /// hosts は、試す順のホストの並び。空のものと重なりは除く。
    /// 最初の候補でつながらなければ、すぐ次の候補を試す
    func connect(hosts: [String], port: Int, token: String, quality: VideoQuality) {
        disconnect()
        errorMessage = nil
        self.quality = quality

        var list: [String] = []
        for host in hosts where !host.isEmpty && !list.contains(host) {
            list.append(host)
        }
        guard !list.isEmpty, list.allSatisfy({ Self.url(host: $0, port: port) != nil }) else {
            errorMessage = "接続先の形式が正しくありません"
            return
        }
        target = (list, port, token)
        lastGoodIndex = 0
        beginRound()
        state = .connecting
        open()
    }

    private static func url(host: String, port: Int) -> URL? {
        URL(string: "ws://\(host):\(port)/")
    }

    /// 候補を試す順番を、最後につながった候補から始め直す
    private func beginRound() {
        hostIndex = lastGoodIndex
        triedInRound = 0
    }

    /// 次の候補に移れるなら、移って true を返す
    private func advanceCandidate() -> Bool {
        guard let target, triedInRound + 1 < target.hosts.count else { return false }
        triedInRound += 1
        hostIndex = (hostIndex + 1) % target.hosts.count
        return true
    }

    private func open() {
        guard let target, target.hosts.indices.contains(hostIndex),
              let url = Self.url(host: target.hosts[hostIndex], port: target.port) else { return }
        rejected = false
        welcomed = false
        let task = URLSession.shared.webSocketTask(with: url)
        task.maximumMessageSize = 16 * 1024 * 1024
        self.task = task
        task.resume()

        var hello: [String: Any] = ["t": "hello", "v": 1, "token": target.token]
        hello.merge(quality.helloFields) { _, new in new }
        let helloData = (try? JSONSerialization.data(withJSONObject: hello)) ?? Data()
        let renderer = self.renderer

        // 受け取りは、画面を描くスレッド（メインスレッド）とは別で動かす。
        // 映像はそのままデコーダーに渡し、制御メッセージだけをメインスレッドに渡す
        let player = self.audio
        receiveTask = Task.detached { [weak self] in
            do {
                try await task.send(.string(String(decoding: helloData, as: UTF8.self)))
                while !Task.isCancelled {
                    switch try await task.receive() {
                    case .data(let data):
                        Self.handleBinary(data, video: renderer, audio: player)
                    case .string(let text):
                        await self?.handleControl(text, from: task)
                    @unknown default:
                        break
                    }
                }
            } catch {
                guard !Task.isCancelled else { return }
                await self?.fail(task)
            }
        }

        // welcome が来なければ、接続に失敗したことにする。
        // 候補が複数あるときは、各回の最初の候補だけ、短く待つ
        let timeout = (triedInRound == 0 && target.hosts.count > 1)
            ? Self.firstCandidateTimeout : Self.connectTimeout
        connectWatchdog?.cancel()
        connectWatchdog = Task { [weak self] in
            try? await Task.sleep(for: timeout)
            guard let self, !Task.isCancelled, self.task === task, self.state != .connected else { return }
            self.fail(task)
        }
    }

    /// バイナリのフレーム（先頭: フラグ(1) + タイムスタンプ(4) + 中身）を、映像か音に振り分ける。
    /// フラグの bit1 が立っていれば音（PCM）、立っていなければ映像（H.264）
    private nonisolated static func handleBinary(_ data: Data, video: H264Renderer, audio: AudioPlayer) {
        guard data.count > 5, let flags = data.first else { return }
        let body = data.dropFirst(5)
        if flags & 0x02 != 0 {
            audio.enqueue(Data(body))
            return
        }
        let start = data.startIndex
        let timestamp = data[(start + 1)..<(start + 5)].reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
        video.enqueue(accessUnit: body, timestampMs: timestamp)
    }

    /// 接続中は ping を送り、pong が来なくなったら、切れたとみなしてつなぎ直す
    private func startHeartbeat(for task: URLSessionWebSocketTask) {
        heartbeat?.cancel()
        pendingPings = [:]
        heartbeat = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: Self.pingInterval)
                guard let self, !Task.isCancelled, self.task === task else { return }
                if let oldest = self.pendingPings.values.min(),
                   Date().timeIntervalSince(oldest) > Self.pingTimeout {
                    self.fail(task)
                    return
                }
                self.pingCounter += 1
                self.pendingPings[self.pingCounter] = Date()
                self.send(["t": "ping", "n": self.pingCounter])
            }
        }
    }

    // MARK: - アプリが裏に回ったとき

    /// アプリが裏に回ったら、接続を閉じる（映像を受け取り続けても、表示できないため）。接続先は覚えておく
    func enterBackground() {
        guard isConnectedOrReconnecting, target != nil else { return }
        suspended = true
        reconnectTask?.cancel()
        reconnectTask = nil
        close(code: .goingAway)
        state = .reconnecting
    }

    /// 前に戻ったら、つなぎ直す。見ていたウィンドウの表示にも戻す
    func becomeActive() {
        guard suspended else { return }
        suspended = false
        guard target != nil else { return }
        reconnectAttempts = 0
        state = .reconnecting
        beginRound()
        open()
    }

    /// 画質を変える。接続中なら、つなぎ直して反映する
    func setQuality(_ quality: VideoQuality) {
        guard quality != self.quality else { return }
        self.quality = quality
        guard state == .connected, target != nil else { return }
        close(code: .goingAway)
        reconnectAttempts = 0
        state = .reconnecting
        beginRound()
        open()
    }

    /// 自分から切る。つなぎ直しもやめる
    func disconnect() {
        reconnectTask?.cancel()
        reconnectTask = nil
        target = nil
        activeHost = nil
        reconnectAttempts = 0
        suspended = false
        currentWindow = nil
        viewMode = .screen
        lastDisplay = nil
        close(code: .normalClosure)
        state = .disconnected
    }

    private func close(code: URLSessionWebSocketTask.CloseCode) {
        receiveTask?.cancel()
        receiveTask = nil
        connectWatchdog?.cancel()
        connectWatchdog = nil
        heartbeat?.cancel()
        heartbeat = nil
        pendingPings = [:]
        latencyMs = nil
        task?.cancel(with: code, reason: nil)
        task = nil
        renderer.reset()
        audio.stop()
        audioOn = false
        resetView()
    }

    private func fail(_ failedTask: URLSessionWebSocketTask) {
        // 前の接続の知らせが遅れて届いたときは、何もしない
        guard failedTask === task else { return }
        let wasConnecting = state == .connecting
        // サーバーが閉じた理由。1000（正常）は、別の端末がつながったときなど。
        // このときにつなぎ直すと、別の端末を追い出してしまうので、つなぎ直さない
        let closedNormally = failedTask.closeCode == .normalClosure
        let window = currentWindow
        receiveTask = nil
        close(code: .goingAway)

        // welcome が来る前に失敗したら、まだ試していない候補を、すぐ試す
        // （断られたとき、サーバーが正常に閉じたときは、別の候補でも同じなので試さない）
        if !welcomed, !rejected, !closedNormally, advanceCandidate() {
            currentWindow = window // つなぎ直しの途中なら、同じウィンドウの表示に戻す
            open()
            return
        }

        if !wasConnecting, !rejected, !closedNormally, target != nil,
           reconnectAttempts < Self.maxReconnectAttempts {
            currentWindow = window // つなぎ直したら、同じウィンドウの表示に戻す
            scheduleReconnect()
            return
        }

        if errorMessage == nil {
            if wasConnecting {
                errorMessage = "接続できませんでした。PCのサーバーが動いているか、アドレスとトークンを確認してください"
            } else if closedNormally {
                errorMessage = "PCとの接続が切れました（別の端末がつながったか、サーバーが止まりました）"
            } else {
                errorMessage = "接続が切れました。つなぎ直せませんでした"
            }
        }
        target = nil
        activeHost = nil
        reconnectAttempts = 0
        currentWindow = nil
        viewMode = .screen
        lastDisplay = nil
        state = .disconnected
    }

    private func scheduleReconnect() {
        state = .reconnecting
        reconnectAttempts += 1
        // 1, 2, 4, 8秒…と間をあける（10秒まで）
        let delay = min(pow(2, Double(reconnectAttempts - 1)), 10)
        reconnectTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(delay))
            guard let self, !Task.isCancelled, self.state == .reconnecting else { return }
            self.beginRound()
            self.open()
        }
    }

    private func resetView() {
        imeOn = nil
        windows = []
        isLoadingWindows = false
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

    /// 仮想キーコードのキーを押して離す。modifiers（Ctrl、Shiftなど）があれば、それを押したまま押す
    func tapKey(vk: Int, modifiers: [Int] = []) {
        for m in modifiers { send(["t": "key", "vk": m, "d": "down"]) }
        send(["t": "key", "vk": vk, "d": "down"])
        send(["t": "key", "vk": vk, "d": "up"])
        for m in modifiers.reversed() { send(["t": "key", "vk": m, "d": "up"]) }
    }

    /// キーの位置（スキャンコード）で押す・離す。Bluetoothキーボード用
    func sendScan(_ sc: Int, down: Bool) {
        send(["t": "key", "sc": sc, "d": down ? "down" : "up"])
    }

    // MARK: - クリップボード

    /// 決まりの上限（docs/protocol.md の clip_set）
    static let maxClipLength = 100_000

    /// iPhoneのクリップボードの文字を、PCのクリップボードに入れる
    func sendClipboard() {
        guard let text = UIPasteboard.general.string, !text.isEmpty else {
            show(notice: "iPhoneのクリップボードに、文字がありません")
            return
        }
        guard text.unicodeScalars.count <= Self.maxClipLength else {
            show(notice: "文字が長すぎます（\(Self.maxClipLength)文字まで）")
            return
        }
        send(["t": "clip_set", "s": text])
    }

    /// PCのクリップボードの文字を、もらう（届いたら、iPhoneのクリップボードに入れる）
    func requestClipboard() {
        send(["t": "clip_get"])
    }

    func setIME(_ on: Bool) {
        send(["t": "ime", "on": on])
    }

    /// IMEのオンとオフを、入れ替える（PCが知らせてきた今の状態の、反対にする）
    func toggleIME() {
        setIME(!(imeOn ?? false))
    }

    // MARK: - 音

    /// 音を鳴らすか決める。つながっていなければ覚えておき、つながったときに始める。
    /// mixWithOthers がオフのとき、音を鳴らす間は、他のアプリの音（YouTube など）が止まる
    func setAudio(_ on: Bool, mixWithOthers: Bool) {
        audioWanted = on
        audioMix = mixWithOthers
        if !on {
            audio.stop()
            audioOn = false
        }
        guard state == .connected else { return }
        requestAudio()
    }

    private func requestAudio() {
        if audioWanted {
            // 低画質のときは、音も軽くする（24000Hz、モノラル）
            let low = quality == .low
            send(["t": "audio", "on": true, "rate": low ? 24000 : 48000, "channels": low ? 1 : 2])
        } else {
            send(["t": "audio", "on": false])
        }
    }

    // MARK: - 表示する範囲

    func requestWindows() {
        isLoadingWindows = true
        send(["t": "list_windows"])
    }

    /// ウィンドウ1つだけを表示する。fitがあれば、ウィンドウの大きさをその縦横比に変える（iPhoneの画面のポイント数）。
    /// toVirtual が true なら、ウィンドウを仮想画面に移して、そこで映す（fit は、仮想画面の縦横比を決めるのに使う）
    func showWindow(_ window: RemoteWindow, fit: CGSize?, toVirtual: Bool = false) {
        lastDisplay = .window(window, fit: fit, toVirtual: toVirtual)
        var message: [String: Any] = ["t": "mode", "mode": "window", "id": window.id]
        addFit(fit, to: &message)
        if toVirtual { message["to_virtual"] = true }
        send(message)
    }

    /// 仮想画面（仮想ディスプレイ）全体を表示する。fit の縦横比に近い解像度にする
    func showDesktop(fit: CGSize?) {
        lastDisplay = .desktop(fit: fit)
        var message: [String: Any] = ["t": "mode", "mode": "desktop"]
        addFit(fit, to: &message)
        send(message)
    }

    func showScreen() {
        lastDisplay = nil
        send(["t": "mode", "mode": "screen"])
    }

    /// ウィンドウを、仮想画面に持ってくる（表示は、そのまま）。切断すると、元の位置に戻る
    func bringWindow(_ window: RemoteWindow) {
        send(["t": "bring", "id": window.id])
    }

    /// 持ってきたウィンドウを、すべて元の位置に戻す
    func restoreWindows() {
        send(["t": "restore"])
    }

    /// 画面の大きさが変わったとき（回した、Duoを開閉した）、今の表示を、新しい大きさに合わせ直す
    func refreshDisplay(fit: CGSize) {
        switch lastDisplay {
        case .desktop?:
            showDesktop(fit: fit)
        case .window(let window, let oldFit, let toVirtual)?:
            if oldFit != nil { showWindow(window, fit: fit, toVirtual: toVirtual) }
        case nil:
            break
        }
    }

    private func addFit(_ fit: CGSize?, to message: inout [String: Any]) {
        if let fit, fit.width > 0, fit.height > 0 {
            message["fit_w"] = Double(fit.width)
            message["fit_h"] = Double(fit.height)
        }
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

    private func handleControl(_ text: String, from source: URLSessionWebSocketTask) {
        // 閉じた接続から遅れて届いたものは捨てる
        guard source === task else { return }
        guard let data = text.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = json["t"] as? String else { return }

        switch type {
        case "welcome":
            if let w = json["w"] as? Int, let h = json["h"] as? Int {
                videoSize = CGSize(width: w, height: h)
            }
            virtualAvailable = json["virtual"] as? Bool ?? false
            let reconnected = state == .reconnecting
            welcomed = true
            lastGoodIndex = hostIndex
            if let hosts = target?.hosts, hosts.indices.contains(hostIndex) {
                activeHost = hosts[hostIndex]
            }
            state = .connected
            errorMessage = nil
            reconnectAttempts = 0
            connectWatchdog?.cancel()
            connectWatchdog = nil
            startHeartbeat(for: source)
            if audioWanted { requestAudio() }
            if reconnected {
                show(notice: "つなぎ直しました")
                // サーバーは本物の画面全体から始めるので、見ていた表示（仮想画面、ウィンドウ）に戻す
                switch lastDisplay {
                case .desktop(let fit)?:
                    showDesktop(fit: fit)
                case .window(let window, let fit, let toVirtual)?:
                    showWindow(window, fit: fit, toVirtual: toVirtual)
                case nil:
                    break
                }
            }
        case "resize":
            if let w = json["w"] as? Int, let h = json["h"] as? Int {
                videoSize = CGSize(width: w, height: h)
            }
        case "pong":
            if let n = json["n"] as? Int, let sent = pendingPings.removeValue(forKey: n) {
                latencyMs = Int(Date().timeIntervalSince(sent) * 1000)
                // これより前の ping は、返事が来なかったものとして忘れる
                pendingPings = pendingPings.filter { $0.key > n }
            }
        case "audio_state":
            if json["on"] as? Bool == true, audioWanted,
               let rate = json["rate"] as? Int, let channels = json["channels"] as? Int {
                audio.start(rate: rate, channels: channels, mixWithOthers: audioMix)
                audioOn = true
            } else {
                audio.stop()
                audioOn = false
                if audioWanted, let reason = json["reason"] as? String {
                    audioFailures += 1
                    show(notice: "音: \(reason)")
                }
            }
        case "clip":
            let text = json["s"] as? String ?? ""
            if text.isEmpty {
                show(notice: "PCのクリップボードに、文字がありません")
            } else {
                UIPasteboard.general.string = text
                show(notice: "iPhoneのクリップボードに入れました")
            }
        case "ime_state":
            imeOn = json["on"] as? Bool
        case "windows":
            let list = json["list"] as? [[String: Any]] ?? []
            windows = list.compactMap { item in
                guard let id = item["id"] as? Int else { return nil }
                return RemoteWindow(
                    id: id,
                    title: item["title"] as? String ?? "",
                    app: item["app"] as? String ?? "",
                    place: item["where"] as? String ?? "main"
                )
            }
            isLoadingWindows = false
        case "mode":
            switch json["mode"] as? String {
            case "window":
                if let id = json["id"] as? Int {
                    let title = json["title"] as? String ?? ""
                    currentWindow = windows.first { $0.id == id } ?? RemoteWindow(id: id, title: title, app: "")
                    viewMode = .window
                }
            case "desktop":
                currentWindow = nil
                viewMode = .desktop
            default:
                currentWindow = nil
                viewMode = .screen
            }
        case "notice":
            if let message = json["message"] as? String { show(notice: message) }
        case "brought":
            let title = json["title"] as? String ?? "ウィンドウ"
            show(notice: "「\(title)」を仮想画面に持ってきました")
            requestWindows() // 一覧の「出ている場所」を、読み直す
        case "error":
            let message = (json["message"] as? String) ?? "サーバーからエラーが返りました"
            if state == .connected {
                show(notice: message)
            } else {
                // つながる前のエラーは、断られたということ（トークンが違うなど）
                rejected = true
                errorMessage = message
            }
        default:
            break
        }
    }
}
