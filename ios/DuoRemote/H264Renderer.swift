import AVFoundation
import CoreMedia
import UIKit
import VideoToolbox

/// H.264 か H.265（HEVC）（Annex B形式）の1フレームを受け取り、AVSampleBufferDisplayLayerに表示する。
/// どちらかは、welcome の codec で決まる（setCodec）。
/// デコードは、レイヤー側（VideoToolbox）が行う。
/// enqueue は受信のスレッドから、reset はメインスレッドから呼ばれるので、状態はロックで守る。
final class H264Renderer: @unchecked Sendable {
    let layer = AVSampleBufferDisplayLayer()

    private let lock = NSLock()
    private var formatDescription: CMVideoFormatDescription?
    private var vps: Data? // H.265 だけ
    private var sps: Data?
    private var pps: Data?
    private var hevc = false

    /// この端末が H.265 をハードウェアでデコードできるか（hello の codecs に hevc を入れるか）
    static var supportsHEVC: Bool { VTIsHardwareDecodeSupported(kCMVideoCodecType_HEVC) }

    init() {
        layer.videoGravity = .resizeAspect
        layer.backgroundColor = UIColor.black.cgColor
    }

    /// 映像の形を決める（welcome の codec）。変わったら、パラメーター（SPS など）を忘れて、次のキーフレームを待つ
    func setCodec(_ codec: String) {
        lock.lock()
        let isHEVC = codec == "hevc"
        if isHEVC != hevc {
            hevc = isHEVC
            vps = nil
            sps = nil
            pps = nil
            formatDescription = nil
        }
        lock.unlock()
    }

    func reset() {
        forgetParameterSets()
        DispatchQueue.main.async { [layer = self.layer] in
            layer.flushAndRemoveImage()
        }
    }

    /// SPS と PPS を忘れる。次のキーフレーム（SPS と PPS が付いている）が来るまで、表示しない
    private func forgetParameterSets() {
        lock.lock()
        vps = nil
        sps = nil
        pps = nil
        formatDescription = nil
        lock.unlock()
    }

    func enqueue(accessUnit: Data, timestampMs: UInt32) {
        lock.lock()
        let isHEVC = hevc
        lock.unlock()

        var slices: [Data] = []
        var newVPS: Data?
        var newSPS: Data?
        var newPPS: Data?
        for nal in Self.splitNALs(accessUnit) {
            guard let header = nal.first else { continue }
            if isHEVC {
                // H.265 の NAL ヘッダーは2バイト。タイプは1バイト目の 1〜6 ビット目
                switch (header >> 1) & 0x3F {
                case 32: newVPS = nal // VPS
                case 33: newSPS = nal // SPS
                case 34: newPPS = nal // PPS
                case 0...31: slices.append(nal) // 映像本体
                default: break // AUD、SEIなどは使わない
                }
            } else {
                switch header & 0x1F {
                case 7: newSPS = nal // SPS
                case 8: newPPS = nal // PPS
                case 1, 5: slices.append(nal) // 映像本体（通常、キーフレーム）
                default: break // AUD、SEIなどは使わない
                }
            }
        }
        guard !slices.isEmpty else { return }

        lock.lock()
        if let newVPS, newVPS != vps { vps = newVPS; formatDescription = nil }
        if let newSPS, newSPS != sps { sps = newSPS; formatDescription = nil }
        if let newPPS, newPPS != pps { pps = newPPS; formatDescription = nil }
        if formatDescription == nil, let sps, let pps {
            if isHEVC {
                if let vps { formatDescription = Self.makeHEVCFormatDescription(vps: vps, sps: sps, pps: pps) }
            } else {
                formatDescription = Self.makeFormatDescription(sps: sps, pps: pps)
            }
        }
        let format = formatDescription
        lock.unlock()
        guard let format else { return } // 最初のキーフレームが来るまで待つ

        // Annex B → AVCC（4バイトの長さ + NAL）
        var avcc = Data()
        for nal in slices {
            var length = UInt32(nal.count).bigEndian
            avcc.append(Data(bytes: &length, count: 4))
            avcc.append(nal)
        }
        guard let sample = Self.makeSampleBuffer(avcc, format: format, timestampMs: timestampMs) else {
            return
        }

        DispatchQueue.main.async { [weak self, layer = self.layer] in
            // アプリが裏に回ると、iOS はデコーダーを止める。戻ったときは flush しないと何も映らない。
            // flush したあとは、キーフレームから入れ直す必要があるので、このフレームは捨てて次のキーフレームを待つ
            if layer.requiresFlushToResumeDecoding || layer.status == .failed {
                layer.flush()
                self?.forgetParameterSets()
                return
            }
            layer.enqueue(sample)
        }
    }

    // MARK: - 変換

    /// 開始コード（00 00 01 / 00 00 00 01）でNALに切り分ける。
    /// 配列にコピーせず、Data の中身を直接見る。
    static func splitNALs(_ data: Data) -> [Data] {
        data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) -> [Data] in
            let bytes = raw.bindMemory(to: UInt8.self)
            let n = bytes.count
            var ranges: [Range<Int>] = []
            var start = -1
            var i = 0
            while i + 2 < n {
                if bytes[i] == 0, bytes[i + 1] == 0, bytes[i + 2] == 1 {
                    if start >= 0 {
                        var end = i
                        if end > start, bytes[end - 1] == 0 { end -= 1 } // 4バイトの開始コードの先頭の0
                        ranges.append(start..<end)
                    }
                    start = i + 3
                    i += 3
                } else if bytes[i + 2] > 1 {
                    i += 3 // 3バイト目が0でも1でもなければ、ここから始まる開始コードはない
                } else {
                    i += 1
                }
            }
            if start >= 0, start < n {
                ranges.append(start..<n)
            }
            let base = data.startIndex
            return ranges.map { data.subdata(in: (base + $0.lowerBound)..<(base + $0.upperBound)) }
        }
    }

    private static func makeFormatDescription(sps: Data, pps: Data) -> CMVideoFormatDescription? {
        var description: CMVideoFormatDescription?
        let status = sps.withUnsafeBytes { spsBuffer in
            pps.withUnsafeBytes { ppsBuffer in
                let pointers: [UnsafePointer<UInt8>] = [
                    spsBuffer.bindMemory(to: UInt8.self).baseAddress!,
                    ppsBuffer.bindMemory(to: UInt8.self).baseAddress!,
                ]
                let sizes = [sps.count, pps.count]
                return CMVideoFormatDescriptionCreateFromH264ParameterSets(
                    allocator: kCFAllocatorDefault,
                    parameterSetCount: 2,
                    parameterSetPointers: pointers,
                    parameterSetSizes: sizes,
                    nalUnitHeaderLength: 4,
                    formatDescriptionOut: &description
                )
            }
        }
        return status == noErr ? description : nil
    }

    private static func makeHEVCFormatDescription(vps: Data, sps: Data, pps: Data) -> CMVideoFormatDescription? {
        var description: CMVideoFormatDescription?
        let status = vps.withUnsafeBytes { vpsBuffer in
            sps.withUnsafeBytes { spsBuffer in
                pps.withUnsafeBytes { ppsBuffer in
                    let pointers: [UnsafePointer<UInt8>] = [
                        vpsBuffer.bindMemory(to: UInt8.self).baseAddress!,
                        spsBuffer.bindMemory(to: UInt8.self).baseAddress!,
                        ppsBuffer.bindMemory(to: UInt8.self).baseAddress!,
                    ]
                    let sizes = [vps.count, sps.count, pps.count]
                    return CMVideoFormatDescriptionCreateFromHEVCParameterSets(
                        allocator: kCFAllocatorDefault,
                        parameterSetCount: 3,
                        parameterSetPointers: pointers,
                        parameterSetSizes: sizes,
                        nalUnitHeaderLength: 4,
                        extensions: nil,
                        formatDescriptionOut: &description
                    )
                }
            }
        }
        return status == noErr ? description : nil
    }

    private static func makeSampleBuffer(
        _ avcc: Data,
        format: CMVideoFormatDescription,
        timestampMs: UInt32
    ) -> CMSampleBuffer? {
        let length = avcc.count
        var block: CMBlockBuffer?
        guard CMBlockBufferCreateWithMemoryBlock(
            allocator: kCFAllocatorDefault,
            memoryBlock: nil,
            blockLength: length,
            blockAllocator: kCFAllocatorDefault,
            customBlockSource: nil,
            offsetToData: 0,
            dataLength: length,
            flags: 0,
            blockBufferOut: &block
        ) == kCMBlockBufferNoErr, let block else { return nil }

        let copied = avcc.withUnsafeBytes {
            CMBlockBufferReplaceDataBytes(
                with: $0.baseAddress!,
                blockBuffer: block,
                offsetIntoDestination: 0,
                dataLength: length
            )
        }
        guard copied == kCMBlockBufferNoErr else { return nil }

        var timing = CMSampleTimingInfo(
            duration: .invalid,
            presentationTimeStamp: CMTime(value: CMTimeValue(timestampMs), timescale: 1000),
            decodeTimeStamp: .invalid
        )
        var sampleSize = length
        var sample: CMSampleBuffer?
        guard CMSampleBufferCreateReady(
            allocator: kCFAllocatorDefault,
            dataBuffer: block,
            formatDescription: format,
            sampleCount: 1,
            sampleTimingEntryCount: 1,
            sampleTimingArray: &timing,
            sampleSizeEntryCount: 1,
            sampleSizeArray: &sampleSize,
            sampleBufferOut: &sample
        ) == noErr, let sample else { return nil }

        // 遅れをためないよう、届いた順にすぐ表示する
        if let attachments = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: true),
           CFArrayGetCount(attachments) > 0 {
            let dictionary = unsafeBitCast(CFArrayGetValueAtIndex(attachments, 0), to: CFMutableDictionary.self)
            CFDictionarySetValue(
                dictionary,
                Unmanaged.passUnretained(kCMSampleAttachmentKey_DisplayImmediately).toOpaque(),
                Unmanaged.passUnretained(kCFBooleanTrue).toOpaque()
            )
        }
        return sample
    }
}
