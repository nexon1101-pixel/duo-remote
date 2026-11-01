import AudioToolbox
import AVFoundation
import SwiftUI

/// PCから届いた音を鳴らす。音の形は2つ。
/// - pcm: 符号つき16ビット、インターリーブ
/// - opus: Opus の1パケット（20ミリ秒、48000Hz）。AVAudioConverter で PCM に戻してから鳴らす
///
/// 音の管理（AVAudioSession）は、音を鳴らしている間だけ使う。
/// オンにすると、他のアプリの音（YouTube など）は止まる（mixWithOthers がオフのとき）。
/// 止めると、他のアプリが続きを再生できる状態に戻す。
final class AudioPlayer: @unchecked Sendable {
    /// 鳴らせなかったとき、理由を知らせる（メインスレッドで呼ばれる）
    var onFailure: (@Sendable (String) -> Void)?

    /// 溜まった音がこれ（秒）を超えたら、新しい音を捨てる。遅れをためないため
    private static let maxQueuedSeconds = 0.15

    private let queue = DispatchQueue(label: "duo.audio")
    private var engine: AVAudioEngine?
    private var node: AVAudioPlayerNode?
    private var format: AVAudioFormat?
    /// Opus のとき、パケットを PCM に戻すもの
    private var opusFormat: AVAudioFormat?
    private var converter: AVAudioConverter?
    private var mixWithOthers = false
    private var queuedFrames = 0
    /// 音量（0〜3）。1 までは、そのまま小さくする。1 を超えた分は、増幅する（3 で、約 +9.5dB）
    private var volume: Float = 1
    /// 増幅の部品（EQ の全体のゲイン）と、割れを抑えるリミッター
    private var boost: AVAudioUnitEQ?
    private var level = (peak: Float(0), at: CFAbsoluteTime(0))
    private var observers: [NSObjectProtocol] = []

    /// 鳴らし始める。すでに鳴らしていれば、やり直す
    func start(rate: Int, channels: Int, codec: String = "pcm", mixWithOthers: Bool) {
        queue.async { [self] in
            teardown()
            self.mixWithOthers = mixWithOthers
            guard let format = AVAudioFormat(
                commonFormat: .pcmFormatFloat32,
                sampleRate: Double(rate),
                channels: AVAudioChannelCount(channels),
                interleaved: false
            ) else {
                fail("この音声形式には対応していません")
                return
            }
            self.format = format
            if codec == "opus" {
                var description = AudioStreamBasicDescription(
                    mSampleRate: Double(rate), mFormatID: kAudioFormatOpus, mFormatFlags: 0,
                    mBytesPerPacket: 0, mFramesPerPacket: 960, mBytesPerFrame: 0,
                    mChannelsPerFrame: UInt32(channels), mBitsPerChannel: 0, mReserved: 0
                )
                guard let opus = AVAudioFormat(streamDescription: &description),
                      let converter = AVAudioConverter(from: opus, to: format) else {
                    fail("Opus 音声を再生できません")
                    return
                }
                opusFormat = opus
                self.converter = converter
            }
            do {
                try activateSession()
                try startEngine()
            } catch {
                teardown()
                fail("音声を再生できません（\(error.localizedDescription)）")
                return
            }
            observe()
        }
    }

    /// 届いた音（PCM か、Opus の1パケット）を、鳴らす列に足す。受信のスレッドから呼ばれる
    func enqueue(_ data: Data) {
        queue.async { [self] in
            guard let format, let node, let engine, engine.isRunning else { return }
            // 追いつけないときは、新しい音を捨てる
            if Double(queuedFrames) > format.sampleRate * Self.maxQueuedSeconds { return }
            let decoded = converter != nil ? decodeOpus(data, to: format) : decodePCM(data, to: format)
            guard let buffer = decoded, buffer.frameLength > 0 else { return }
            measure(buffer)
            let frames = Int(buffer.frameLength)
            queuedFrames += frames
            node.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
                self?.queue.async { self?.queuedFrames -= frames }
            }
        }
    }

    /// 音量を変える（0〜3）。鳴らしている最中でも、鳴らす前でも使える
    func setVolume(_ value: Float) {
        queue.async { [self] in
            volume = min(max(value, 0), Self.maxVolume)
            applyVolume()
        }
    }

    static let maxVolume: Float = 3

    /// 1 までは、ミキサーの音量で小さくする。1 を超えた分は、EQ のゲイン（dB）で増幅する
    private func applyVolume() {
        engine?.mainMixerNode.outputVolume = min(volume, 1)
        boost?.globalGain = volume > 1 ? 20 * log10(volume) : 0
    }

    /// 鳴らす音の大きさ（0〜1。増幅したあとの大きさ）を、メーターに伝える。0.06 秒ごとに、その間の最大を出す
    private func measure(_ buffer: AVAudioPCMBuffer) {
        guard let channels = buffer.floatChannelData else { return }
        var peak: Float = 0
        for ch in 0..<Int(buffer.format.channelCount) {
            let samples = channels[ch]
            for i in 0..<Int(buffer.frameLength) { peak = max(peak, abs(samples[i])) }
        }
        level.peak = max(level.peak, peak * max(volume, 0))
        let now = CFAbsoluteTimeGetCurrent()
        guard now - level.at >= 0.06 else { return }
        let value = min(level.peak, 1.2)
        level = (0, now)
        DispatchQueue.main.async { AudioLevel.shared.value = value }
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

    private func decodePCM(_ data: Data, to format: AVAudioFormat) -> AVAudioPCMBuffer? {
        let channels = Int(format.channelCount)
        let frames = data.count / (2 * channels)
        guard frames > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames)),
              let out = buffer.floatChannelData else { return nil }
        buffer.frameLength = AVAudioFrameCount(frames)
        data.withUnsafeBytes { raw in
            for ch in 0..<channels {
                for i in 0..<frames {
                    let sample = raw.loadUnaligned(fromByteOffset: (i * channels + ch) * 2, as: Int16.self)
                    out[ch][i] = Float(Int16(littleEndian: sample)) / 32768
                }
            }
        }
        return buffer
    }

    /// Opus の1パケットを、PCM に戻す
    private func decodeOpus(_ data: Data, to format: AVAudioFormat) -> AVAudioPCMBuffer? {
        guard let converter, let opusFormat, !data.isEmpty else { return nil }
        let packet = AVAudioCompressedBuffer(format: opusFormat, packetCapacity: 1, maximumPacketSize: data.count)
        data.withUnsafeBytes { raw in
            packet.data.copyMemory(from: raw.baseAddress!, byteCount: data.count)
        }
        packet.byteLength = UInt32(data.count)
        packet.packetCount = 1
        packet.packetDescriptions?[0] = AudioStreamPacketDescription(
            mStartOffset: 0, mVariableFramesInPacket: 0, mDataByteSize: UInt32(data.count)
        )
        // 1パケットは 20 ミリ秒（48000Hz で 960 フレーム）。余裕をもって、その倍を用意する
        guard let out = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 1920) else { return nil }
        var given = false
        var error: NSError?
        let status = converter.convert(to: out, error: &error) { _, inputStatus in
            if given {
                inputStatus.pointee = .noDataNow
                return nil
            }
            given = true
            inputStatus.pointee = .haveData
            return packet
        }
        return status == .error ? nil : out
    }

    private func activateSession() throws {
        let session = AVAudioSession.sharedInstance()
        // .playback は、消音スイッチがオンでも鳴る
        try session.setCategory(.playback, mode: .default, options: mixWithOthers ? [.mixWithOthers] : [])
        // 出力の小さな溜めを短くして、音の遅れを減らす（5ミリ秒。端末が受け付ける範囲に丸められる）
        try? session.setPreferredIOBufferDuration(0.005)
        try session.setActive(true)
    }

    private func startEngine() throws {
        guard let format else { return }
        let engine = AVAudioEngine()
        let node = AVAudioPlayerNode()
        let boost = AVAudioUnitEQ(numberOfBands: 1)
        let limiter = AVAudioUnitEffect(audioComponentDescription: AudioComponentDescription(
            componentType: kAudioUnitType_Effect, componentSubType: kAudioUnitSubType_PeakLimiter,
            componentManufacturer: kAudioUnitManufacturer_Apple, componentFlags: 0, componentFlagsMask: 0))
        engine.attach(node)
        engine.attach(boost)
        engine.attach(limiter)
        engine.connect(node, to: boost, format: format)
        engine.connect(boost, to: limiter, format: format)
        engine.connect(limiter, to: engine.mainMixerNode, format: format)
        self.engine = engine
        self.boost = boost
        applyVolume()
        try engine.start()
        node.play()
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
        boost = nil
        engine = nil
        format = nil
        converter = nil
        opusFormat = nil
        queuedFrames = 0
        publishSilence()
    }

    private func publishSilence() {
        DispatchQueue.main.async { AudioLevel.shared.value = 0 }
    }

    private func fail(_ message: String) {
        let handler = onFailure
        DispatchQueue.main.async { handler?(message) }
    }
}


/// いま鳴らしている音の大きさ（0〜1。1 を超えると、割れる）。メーターが見る。
/// 画面全体を、細かく描き直さないように、ここだけが変わる
final class AudioLevel: ObservableObject {
    static let shared = AudioLevel()
    @Published var value: Float = 0
}

/// 音の大きさの棒（メーター）。大きすぎて割れそうなときは、赤くなる
struct AudioMeterBar: View {
    @ObservedObject private var level = AudioLevel.shared
    var width: CGFloat = 80

    var body: some View {
        let value = CGFloat(min(max(level.value, 0), 1))
        let color: Color = level.value > 1 ? .red : (level.value > 0.8 ? .orange : .green)
        ZStack(alignment: .leading) {
            Capsule().fill(Color.white.opacity(0.2))
            Capsule().fill(color).frame(width: width * value)
        }
        .frame(width: width, height: 6)
        .animation(.linear(duration: 0.06), value: level.value)
        .accessibilityLabel("音の大きさ")
    }
}
