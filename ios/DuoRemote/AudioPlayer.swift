import AVFoundation

/// PCから届いた音（PCM、符号つき16ビット、インターリーブ）を鳴らす。
///
/// 音の管理（AVAudioSession）は、音を鳴らしている間だけ使う。
/// オンにすると、他のアプリの音（YouTube など）は止まる（mixWithOthers がオフのとき）。
/// 止めると、他のアプリが続きを再生できる状態に戻す。
final class AudioPlayer: @unchecked Sendable {
    /// 鳴らせなかったとき、理由を知らせる（メインスレッドで呼ばれる）
    var onFailure: (@Sendable (String) -> Void)?

    /// 溜まった音がこれ（秒）を超えたら、新しい音を捨てる。遅れをためないため
    private static let maxQueuedSeconds = 0.3

    private let queue = DispatchQueue(label: "duo.audio")
    private var engine: AVAudioEngine?
    private var node: AVAudioPlayerNode?
    private var format: AVAudioFormat?
    private var mixWithOthers = false
    private var queuedFrames = 0
    private var observers: [NSObjectProtocol] = []

    /// 鳴らし始める。すでに鳴らしていれば、やり直す
    func start(rate: Int, channels: Int, mixWithOthers: Bool) {
        queue.async { [self] in
            teardown()
            self.mixWithOthers = mixWithOthers
            guard let format = AVAudioFormat(
                commonFormat: .pcmFormatFloat32,
                sampleRate: Double(rate),
                channels: AVAudioChannelCount(channels),
                interleaved: false
            ) else {
                fail("音の形に対応していません")
                return
            }
            self.format = format
            do {
                try activateSession()
                try startEngine()
            } catch {
                teardown()
                fail("音を鳴らせませんでした（\(error.localizedDescription)）")
                return
            }
            observe()
        }
    }

    /// 届いた音（PCM）を、鳴らす列に足す。受信のスレッドから呼ばれる
    func enqueue(_ data: Data) {
        queue.async { [self] in
            guard let format, let node, let engine, engine.isRunning else { return }
            let channels = Int(format.channelCount)
            let frames = data.count / (2 * channels)
            guard frames > 0 else { return }
            // 追いつけないときは、新しい音を捨てる
            if Double(queuedFrames) > format.sampleRate * Self.maxQueuedSeconds { return }
            guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames)),
                  let out = buffer.floatChannelData else { return }
            buffer.frameLength = AVAudioFrameCount(frames)
            data.withUnsafeBytes { raw in
                for ch in 0..<channels {
                    for i in 0..<frames {
                        let sample = raw.loadUnaligned(fromByteOffset: (i * channels + ch) * 2, as: Int16.self)
                        out[ch][i] = Float(Int16(littleEndian: sample)) / 32768
                    }
                }
            }
            queuedFrames += frames
            node.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
                self?.queue.async { self?.queuedFrames -= frames }
            }
        }
    }

    /// 止める。他のアプリが、続きを再生できる状態に戻す
    func stop() {
        queue.async { [self] in
            let wasActive = engine != nil
            teardown()
            if wasActive {
                try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            }
        }
    }

    // MARK: - 内部（queue の上で呼ぶ）

    private func activateSession() throws {
        let session = AVAudioSession.sharedInstance()
        // .playback は、消音スイッチがオンでも鳴る
        try session.setCategory(.playback, mode: .default, options: mixWithOthers ? [.mixWithOthers] : [])
        try session.setActive(true)
    }

    private func startEngine() throws {
        guard let format else { return }
        let engine = AVAudioEngine()
        let node = AVAudioPlayerNode()
        engine.attach(node)
        engine.connect(node, to: engine.mainMixerNode, format: format)
        try engine.start()
        node.play()
        self.engine = engine
        self.node = node
        queuedFrames = 0
    }

    /// 電話などで止められたあとや、音の出口（イヤホンなど）が変わったあとに、鳴らし直す
    private func observe() {
        let center = NotificationCenter.default
        let restart: @Sendable (Notification) -> Void = { [weak self] _ in
            self?.queue.async {
                guard let self, self.format != nil else { return }
                self.engine?.stop()
                self.engine = nil
                self.node = nil
                try? self.activateSession()
                try? self.startEngine()
            }
        }
        observers = [
            center.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: nil) { note in
                let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
                if raw == AVAudioSession.InterruptionType.ended.rawValue { restart(note) }
            },
            center.addObserver(forName: .AVAudioEngineConfigurationChange, object: nil, queue: nil, using: restart),
        ]
    }

    private func teardown() {
        for o in observers { NotificationCenter.default.removeObserver(o) }
        observers = []
        node?.stop()
        engine?.stop()
        node = nil
        engine = nil
        format = nil
        queuedFrames = 0
    }

    private func fail(_ message: String) {
        let handler = onFailure
        DispatchQueue.main.async { handler?(message) }
    }
}
