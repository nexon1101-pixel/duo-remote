import Foundation
import Network

/// 映像と音を、UDP で受け取る。通信の取り決めは、docs/protocol.md の「UDP での映像と音」。
///
/// - つなぎ始め: BIND を 0.2 秒おきに送り、ACK が来たら、つながったことにする（3 秒たっても来なければ、失敗）
/// - つながったあと: 2 秒おきに KEEPALIVE を送る
/// - 映像: 分けて届いたデータグラムを集めて、1 つのフレームにする。欠けたら、次のキーフレームまで、デコーダーに渡さない
/// - 音: 欠けた分は、捨てる
final class UDPVideoReceiver: @unchecked Sendable {
    /// データグラムの種類
    private enum Kind {
        static let bind: UInt8 = 0x01
        static let ack: UInt8 = 0x02
        static let keepalive: UInt8 = 0x03
        static let video: UInt8 = 0x10
        static let audio: UInt8 = 0x11
    }

    /// 1 つのフレームを、集めている途中の状態
    private struct Assembly {
        var count: Int
        var parts: [Int: Data] = [:]
        var timestamp: UInt32
        var isKey: Bool
        var startedAt: Date
    }

    private static let headerSize = 14
    private static let bindInterval: TimeInterval = 0.2
    private static let bindTimeout: TimeInterval = 3
    private static let keepaliveInterval: TimeInterval = 2
    /// そろわないまま、これだけたったフレームは、捨てる
    private static let assemblyTimeout: TimeInterval = 0.1

    private let connection: NWConnection
    private let queue = DispatchQueue(label: "duo.udp")
    private let key: Data
    private let video: H264Renderer
    private let audio: AudioPlayer
    private let onBound: @Sendable () -> Void
    private let onFailed: @Sendable () -> Void

    // 以下は、queue の上でだけ、読み書きする
    private var stopped = false
    private var bound = false
    private var bindTimer: DispatchSourceTimer?
    private var keepaliveTimer: DispatchSourceTimer?
    private var purgeTimer: DispatchSourceTimer?
    private var assemblies: [UInt32: Assembly] = [:]
    private var lastVideoID: UInt32?
    /// 欠けたあと、キーフレームが来るまで、映像を渡さない
    private var needKeyframe = true

    /// `udpKey` は、`welcome` の `udp_key`（16 文字の 16 進）。読めなければ nil
    init?(host: String, port: Int, udpKey: String, video: H264Renderer, audio: AudioPlayer,
          onBound: @escaping @Sendable () -> Void, onFailed: @escaping @Sendable () -> Void) {
        guard let keyData = Self.bytes(fromHex: udpKey), keyData.count == 8,
              let nwPort = NWEndpoint.Port(rawValue: UInt16(clamping: port)) else { return nil }
        self.key = keyData
        self.video = video
        self.audio = audio
        self.onBound = onBound
        self.onFailed = onFailed
        self.connection = NWConnection(host: NWEndpoint.Host(host), port: nwPort, using: .udp)
    }

    func start() {
        queue.async { [self] in
            connection.stateUpdateHandler = { [weak self] state in
                guard let self else { return }
                switch state {
                case .ready:
                    self.queue.async { self.beginBinding() }
                case .failed, .cancelled:
                    self.queue.async { self.failIfNotBound() }
                default:
                    break
                }
            }
            connection.start(queue: queue)
            receiveNext()
        }
    }

    func stop() {
        queue.async { [self] in
            stopped = true
            bindTimer?.cancel()
            keepaliveTimer?.cancel()
            purgeTimer?.cancel()
            bindTimer = nil
            keepaliveTimer = nil
            purgeTimer = nil
            connection.cancel()
        }
    }

    // MARK: つなぐ

    private func beginBinding() {
        guard !stopped, bindTimer == nil, !bound else { return }
        let startedAt = Date()
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now(), repeating: Self.bindInterval)
        timer.setEventHandler { [weak self] in
            guard let self, !self.stopped, !self.bound else { return }
            if Date().timeIntervalSince(startedAt) > Self.bindTimeout {
                self.failIfNotBound()
                return
            }
            self.send(Data([Kind.bind]) + self.key)
        }
        bindTimer = timer
        timer.resume()
    }

    private func failIfNotBound() {
        guard !stopped, !bound else { return }
        stopped = true
        bindTimer?.cancel()
        bindTimer = nil
        connection.cancel()
        onFailed()
    }

    private func didBind() {
        guard !bound else { return }
        bound = true
        bindTimer?.cancel()
        bindTimer = nil

        let keepalive = DispatchSource.makeTimerSource(queue: queue)
        keepalive.schedule(deadline: .now() + Self.keepaliveInterval, repeating: Self.keepaliveInterval)
        keepalive.setEventHandler { [weak self] in
            guard let self, !self.stopped else { return }
            self.send(Data([Kind.keepalive]))
        }
        keepaliveTimer = keepalive
        keepalive.resume()

        let purge = DispatchSource.makeTimerSource(queue: queue)
        purge.schedule(deadline: .now() + 0.05, repeating: 0.05)
        purge.setEventHandler { [weak self] in
            self?.purgeOldAssemblies()
        }
        purgeTimer = purge
        purge.resume()

        onBound()
    }

    private func send(_ data: Data) {
        connection.send(content: data, completion: .contentProcessed { _ in })
    }

    private func receiveNext() {
        connection.receiveMessage { [weak self] data, _, _, error in
            guard let self else { return }
            self.queue.async {
                guard !self.stopped else { return }
                if let data, !data.isEmpty { self.handle(data) }
                if error == nil { self.receiveNext() }
            }
        }
    }

    // MARK: 受け取る

    private func handle(_ data: Data) {
        guard let kind = data.first else { return }
        switch kind {
        case Kind.ack:
            if data.count == 9, data.dropFirst() == key { didBind() }
        case Kind.video:
            handleVideo(data)
        case Kind.audio:
            handleAudio(data)
        default:
            break
        }
    }

    /// 先頭 14 バイトの決まり（docs/protocol.md）を読む。大きさが足りなければ nil
    private func parseHeader(_ data: Data) -> (id: UInt32, timestamp: UInt32, flags: UInt8, index: Int, count: Int)? {
        guard data.count >= Self.headerSize else { return nil }
        let b = [UInt8](data.prefix(Self.headerSize))
        let id = UInt32(b[1]) << 24 | UInt32(b[2]) << 16 | UInt32(b[3]) << 8 | UInt32(b[4])
        let ts = UInt32(b[5]) << 24 | UInt32(b[6]) << 16 | UInt32(b[7]) << 8 | UInt32(b[8])
        let index = Int(b[10]) << 8 | Int(b[11])
        let count = Int(b[12]) << 8 | Int(b[13])
        return (id, ts, b[9], index, count)
    }

    private func handleAudio(_ data: Data) {
        guard parseHeader(data) != nil, data.count > Self.headerSize else { return }
        audio.enqueue(Data(data.dropFirst(Self.headerSize)))
    }

    private func handleVideo(_ data: Data) {
        guard let h = parseHeader(data), h.count >= 1, h.index < h.count else { return }
        let body = Data(data.dropFirst(Self.headerSize))
        // もう渡した（または、捨てた）より古いフレームの、遅れて届いた分は、使わない
        if let last = lastVideoID, Int32(bitPattern: h.id &- last) <= 0 { return }

        if h.count == 1 {
            assemblies[h.id] = nil
            deliver(id: h.id, timestamp: h.timestamp, isKey: h.flags & 0x01 != 0, body: body)
            return
        }
        var assembly = assemblies[h.id] ?? Assembly(
            count: h.count, timestamp: h.timestamp, isKey: h.flags & 0x01 != 0, startedAt: Date()
        )
        guard assembly.count == h.count else { return }
        assembly.parts[h.index] = body
        if assembly.parts.count == assembly.count {
            assemblies[h.id] = nil
            var frame = Data()
            for i in 0..<assembly.count {
                guard let part = assembly.parts[i] else { return }
                frame.append(part)
            }
            deliver(id: h.id, timestamp: assembly.timestamp, isKey: assembly.isKey, body: frame)
        } else {
            assemblies[h.id] = assembly
        }
    }

    /// そろったフレームを、デコーダーに渡す。順番は、フレーム番号の順
    private func deliver(id: UInt32, timestamp: UInt32, isKey: Bool, body: Data) {
        // 番号が飛んだら、その間のフレームが欠けたことになる
        if let last = lastVideoID, id != last &+ 1 { needKeyframe = true }
        lastVideoID = id
        // 手前の、そろっていないフレームは、もう使えない
        for (oldID, _) in assemblies where Int32(bitPattern: id &- oldID) > 0 {
            assemblies[oldID] = nil
        }
        if isKey { needKeyframe = false }
        guard !needKeyframe else { return }
        video.enqueue(accessUnit: body, timestampMs: timestamp)
    }

    /// そろわないまま 100 ミリ秒たったフレームを捨てる。捨てたら、欠けたことにする
    private func purgeOldAssemblies() {
        guard !stopped else { return }
        let now = Date()
        for (id, assembly) in assemblies where now.timeIntervalSince(assembly.startedAt) > Self.assemblyTimeout {
            assemblies[id] = nil
            needKeyframe = true
            // この番号まで、使えないことにする（遅れて届いても、渡さない）
            if let last = lastVideoID {
                if Int32(bitPattern: id &- last) > 0 { lastVideoID = id }
            } else {
                lastVideoID = id
            }
        }
    }

    // MARK: 文字から、バイトへ

    private static func bytes(fromHex text: String) -> Data? {
        let chars = Array(text.utf8)
        guard chars.count % 2 == 0 else { return nil }
        var result = Data()
        var index = 0
        while index < chars.count {
            guard let high = nibble(chars[index]), let low = nibble(chars[index + 1]) else { return nil }
            result.append(high << 4 | low)
            index += 2
        }
        return result
    }

    private static func nibble(_ c: UInt8) -> UInt8? {
        switch c {
        case 0x30...0x39: return c - 0x30
        case 0x61...0x66: return c - 0x61 + 10
        case 0x41...0x46: return c - 0x41 + 10
        default: return nil
        }
    }
}
