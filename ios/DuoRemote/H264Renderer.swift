import AVFoundation
import CoreMedia
import UIKit

/// H.264（Annex B形式）の1フレームを受け取り、AVSampleBufferDisplayLayerに表示する。
/// デコードは、レイヤー側（VideoToolbox）が行う。
final class H264Renderer {
    let layer = AVSampleBufferDisplayLayer()

    private var formatDescription: CMVideoFormatDescription?
    private var sps: Data?
    private var pps: Data?

    init() {
        layer.videoGravity = .resizeAspect
        layer.backgroundColor = UIColor.black.cgColor
    }

    func reset() {
        sps = nil
        pps = nil
        formatDescription = nil
        DispatchQueue.main.async { [layer = self.layer] in
            layer.flushAndRemoveImage()
        }
    }

    func enqueue(accessUnit: Data, timestampMs: UInt32) {
        var slices: [Data] = []
        for nal in Self.splitNALs(accessUnit) {
            guard let header = nal.first else { continue }
            switch header & 0x1F {
            case 7: // SPS
                if sps != nal { sps = nal; formatDescription = nil }
            case 8: // PPS
                if pps != nal { pps = nal; formatDescription = nil }
            case 1, 5: // 映像本体（通常、キーフレーム）
                slices.append(nal)
            default:
                break // AUD、SEIなどは使わない
            }
        }
        guard !slices.isEmpty else { return }

        if formatDescription == nil, let sps, let pps {
            formatDescription = Self.makeFormatDescription(sps: sps, pps: pps)
        }
        guard let formatDescription else { return } // 最初のキーフレームが来るまで待つ

        // Annex B → AVCC（4バイトの長さ + NAL）
        var avcc = Data()
        for nal in slices {
            var length = UInt32(nal.count).bigEndian
            avcc.append(Data(bytes: &length, count: 4))
            avcc.append(nal)
        }
        guard let sample = Self.makeSampleBuffer(avcc, format: formatDescription, timestampMs: timestampMs) else {
            return
        }

        DispatchQueue.main.async { [layer = self.layer] in
            if layer.status == .failed {
                layer.flush()
            }
            layer.enqueue(sample)
        }
    }

    // MARK: - 変換

    /// 開始コード（00 00 01 / 00 00 00 01）でNALに切り分ける。
    static func splitNALs(_ data: Data) -> [Data] {
        let bytes = [UInt8](data)
        let n = bytes.count
        var nals: [Data] = []
        var start = -1
        var i = 0
        while i + 2 < n {
            if bytes[i] == 0, bytes[i + 1] == 0, bytes[i + 2] == 1 {
                if start >= 0 {
                    var end = i
                    if end > start, bytes[end - 1] == 0 { end -= 1 } // 4バイトの開始コードの先頭の0
                    nals.append(Data(bytes[start..<end]))
                }
                start = i + 3
                i += 3
            } else {
                i += 1
            }
        }
        if start >= 0, start < n {
            nals.append(Data(bytes[start..<n]))
        }
        return nals
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
