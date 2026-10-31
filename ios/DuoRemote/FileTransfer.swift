import PhotosUI
import SwiftUI
import UIKit
import UniformTypeIdentifiers

// ファイルと画像の受け渡し。通信の取り決めは docs/protocol.md の
// 「ファイルの受け渡し（バイナリフレーム）」を参照。
//
// スレッドの分け方:
// - 受け取り: バイナリフレームの中身は、受信のスレッドが FileReceiver に直接書く（ロックで守る）。
//   file_begin と file_end は、文字のフレームなので、メインスレッドの FileTransfer が受け持つ。
//   受信のループは、文字のフレームの処理が終わってから次のフレームを読むので、
//   file_begin → バイナリ → file_end の順は、そのまま守られる
// - 送り出し: ファイルを読んで送る部分は、メインスレッドの外で動かし、進み具合だけメインスレッドに知らせる

/// バイナリフレームの、ファイルの印（フラグ）
let fileFrameFlag: UInt8 = 0x04

// MARK: - 受け取り（受信のスレッドから使う）

/// PCから届くファイルを、一時フォルダに書き足していく。どのメソッドも、どのスレッドから呼んでもよい。
final class FileReceiver: @unchecked Sendable {
    struct Finished {
        let url: URL
        let directory: URL
        let name: String
        let mime: String
        /// 宣言された大きさのとおりに、最後まで書けた
        let complete: Bool
    }

    private struct Entry {
        let url: URL
        let directory: URL
        let name: String
        let mime: String
        let size: Int64
        let handle: FileHandle
        var received: Int64 = 0
        var failed = false
    }

    private let lock = NSLock()
    private var entries: [UInt32: Entry] = [:]
    private var cleaned = false

    static var rootDirectory: URL {
        FileManager.default.temporaryDirectory.appendingPathComponent("DuoRemoteReceived", isDirectory: true)
    }

    /// 受け取りを始める。書き込む先を作れなければ false
    func begin(id: UInt32, name: String, size: Int64, mime: String) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        removeOldFolders()
        // 同じ番号が残っていたら、閉じて捨てる
        if let old = entries.removeValue(forKey: id) {
            try? old.handle.close()
            try? FileManager.default.removeItem(at: old.directory)
        }
        let directory = Self.rootDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        let safe = Self.safeName(name)
        let url = directory.appendingPathComponent(safe)
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            guard FileManager.default.createFile(atPath: url.path, contents: nil) else { return false }
            let handle = try FileHandle(forWritingTo: url)
            entries[id] = Entry(url: url, directory: directory, name: safe, mime: mime, size: size, handle: handle)
            return true
        } catch {
            try? FileManager.default.removeItem(at: directory)
            return false
        }
    }

    /// 中身を書き足す
    func append(id: UInt32, data: Data) {
        lock.lock()
        defer { lock.unlock() }
        guard var entry = entries[id], !entry.failed else { return }
        do {
            try entry.handle.write(contentsOf: data)
            entry.received += Int64(data.count)
            if entry.size > 0, entry.received > entry.size { entry.failed = true }
        } catch {
            entry.failed = true
        }
        entries[id] = entry
    }

    /// 受け取りを終える。書き込みを閉じて、結果を返す。知らない番号なら nil
    func finish(id: UInt32) -> Finished? {
        lock.lock()
        defer { lock.unlock() }
        guard let entry = entries.removeValue(forKey: id) else { return nil }
        try? entry.handle.close()
        let complete = !entry.failed && (entry.size <= 0 || entry.received == entry.size)
        return Finished(url: entry.url, directory: entry.directory, name: entry.name, mime: entry.mime, complete: complete)
    }

    /// 受け取りかけのものを、すべて消す（切断したとき）
    func cancelAll() {
        lock.lock()
        defer { lock.unlock() }
        for entry in entries.values {
            try? entry.handle.close()
            try? FileManager.default.removeItem(at: entry.directory)
        }
        entries = [:]
    }

    /// 前に受け取った一時ファイルのうち、1日以上たったものを消す（最初の1回だけ）。ロックを持って呼ぶ
    private func removeOldFolders() {
        guard !cleaned else { return }
        cleaned = true
        let fm = FileManager.default
        let folders = (try? fm.contentsOfDirectory(
            at: Self.rootDirectory,
            includingPropertiesForKeys: [.contentModificationDateKey]
        )) ?? []
        let limit = Date().addingTimeInterval(-24 * 60 * 60)
        for folder in folders {
            let date = (try? folder.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
            if let date, date < limit { try? fm.removeItem(at: folder) }
        }
    }

    /// フォルダの区切りや、使えない文字を取り除いた名前にする
    static func safeName(_ name: String) -> String {
        let base = name.split(whereSeparator: { $0 == "/" || $0 == "\\" }).last.map(String.init) ?? ""
        let bad = CharacterSet(charactersIn: ":*?\"<>|").union(.controlCharacters)
        var cleaned = base.unicodeScalars.map { bad.contains($0) ? "_" : String($0) }.joined()
        cleaned = cleaned.trimmingCharacters(in: .whitespaces)
        if cleaned.isEmpty || cleaned == "." || cleaned == ".." { cleaned = "ファイル" }
        return String(cleaned.prefix(120))
    }
}

// MARK: - 送るファイル

/// PCに送るファイル1つ
struct OutgoingFile: Sendable {
    let url: URL
    let name: String
    let size: Int64
    let toClipboard: Bool
    /// 送り終えたら、置いてあるフォルダごと消す（アプリが作った一時ファイル）
    let deleteFolderAfter: Bool
    /// ファイルアプリから選んだもの。送り終えるまで、アクセスの許可を持っておく
    let scoped: Bool

    func cleanup() {
        if scoped { url.stopAccessingSecurityScopedResource() }
        if deleteFolderAfter { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
    }
}

/// 写真アプリから選んだ1枚（または1本）。一時ファイルにコピーして持つ
struct PickedFile: Transferable, Sendable {
    let url: URL

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(importedContentType: .image) { received in
            try PickedFile.copy(received.file)
        }
        FileRepresentation(importedContentType: .movie) { received in
            try PickedFile.copy(received.file)
        }
    }

    /// 受け取ったファイルは、読み込みが終わると消えるので、自分の一時フォルダにコピーする
    private static func copy(_ source: URL) throws -> PickedFile {
        let directory = FileTransfer.sendDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let destination = directory.appendingPathComponent(source.lastPathComponent)
        try FileManager.default.copyItem(at: source, to: destination)
        return PickedFile(url: destination)
    }
}

// MARK: - 送る・受け取るの本体

@MainActor
final class FileTransfer {
    /// 取り決めの上限。これを超えるファイルは、送る前に断る
    static let maxFileSize: Int64 = 200 * 1024 * 1024
    /// バイナリフレーム1つの中身の大きさ
    nonisolated static let chunkSize = 256 * 1024

    nonisolated static var sendDirectory: URL {
        FileManager.default.temporaryDirectory.appendingPathComponent("DuoRemoteSend", isDirectory: true)
    }

    /// 受信のスレッドが書き込む先。RemoteSession.handleBinary から使う
    nonisolated static let receiver = FileReceiver()

    private weak var session: RemoteSession?

    // 送る側（メインスレッドだけで触る）
    private var queue: [OutgoingFile] = []
    private var worker: Task<Void, Never>?
    /// 接続ごとの通し番号
    private var nextID: UInt32 = 0
    private var batchTotal = 0
    private var batchDone = 0

    // 受け取る側
    private var received: [FileReceiver.Finished] = []
    private var shareTask: Task<Void, Never>?

    init(session: RemoteSession) {
        self.session = session
    }

    // MARK: 送る

    /// 写真アプリから選んだものを読み込んで、送る
    func sendPhotos(_ items: [PhotosPickerItem], toClipboard: Bool) {
        guard !items.isEmpty else { return }
        session?.show(notice: "写真を読み込み中…")
        Task { [weak self] in
            var files: [OutgoingFile] = []
            for (index, item) in items.enumerated() {
                do {
                    guard let picked = try await item.loadTransferable(type: PickedFile.self) else { continue }
                    let file = await Task.detached(priority: .userInitiated) {
                        FileTransfer.preparePhoto(picked, index: index, toClipboard: toClipboard)
                    }.value
                    if let file { files.append(file) }
                } catch {
                    self?.session?.show(notice: "写真を読み込めませんでした")
                }
            }
            self?.enqueue(files)
        }
    }

    /// ファイルアプリから選んだものを送る
    func sendFiles(_ urls: [URL], toClipboard: Bool) {
        var files: [OutgoingFile] = []
        for url in urls {
            let scoped = url.startAccessingSecurityScopedResource()
            let values = try? url.resourceValues(forKeys: [.fileSizeKey, .isDirectoryKey])
            if values?.isDirectory == true || values?.fileSize == nil {
                if scoped { url.stopAccessingSecurityScopedResource() }
                session?.show(notice: "「\(url.lastPathComponent)」は送れません")
                continue
            }
            files.append(OutgoingFile(
                url: url, name: url.lastPathComponent, size: Int64(values?.fileSize ?? 0),
                toClipboard: toClipboard, deleteFolderAfter: false, scoped: scoped
            ))
        }
        enqueue(files)
    }

    /// 大きすぎるものを断って、残りを送る順番に並べる
    private func enqueue(_ files: [OutgoingFile]) {
        guard let session, !files.isEmpty else { return }
        var accepted: [OutgoingFile] = []
        for file in files {
            if file.size > Self.maxFileSize {
                session.show(notice: "「\(file.name)」は大きすぎます（200MBまで）")
                file.cleanup()
            } else {
                accepted.append(file)
            }
        }
        guard !accepted.isEmpty else { return }
        guard session.state == .connected else {
            session.show(notice: "未接続です")
            accepted.forEach { $0.cleanup() }
            return
        }
        if worker == nil {
            batchTotal = 0
            batchDone = 0
        }
        batchTotal += accepted.count
        queue.append(contentsOf: accepted)
        startWorker()
    }

    private func startWorker() {
        guard worker == nil else { return }
        worker = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, !self.queue.isEmpty else { break }
                let file = self.queue.removeFirst()
                self.batchDone += 1
                await self.transmit(file)
            }
            // 切断で止められたときは、cancelAll が片づける
            if !Task.isCancelled { self?.worker = nil }
        }
    }

    /// 1つ送る。file_put → バイナリフレーム → file_end の順
    private func transmit(_ file: OutgoingFile) async {
        defer { file.cleanup() }
        guard let session, session.state == .connected, let socket = session.task else { return }
        nextID &+= 1
        let id = nextID
        let put = Self.jsonString([
            "t": "file_put", "id": Int(id), "name": file.name,
            "size": file.size, "to_clipboard": file.toClipboard,
        ])
        let end = Self.jsonString(["t": "file_end", "id": Int(id)])
        let label = batchTotal > 1 ? "(\(batchDone)/\(batchTotal)) " : ""
        session.show(notice: "送信中 \(label)\(file.name)")
        let size = file.size
        let name = file.name
        do {
            try await socket.send(.string(put))
            try await Self.stream(url: file.url, id: id, socket: socket) { [weak self] sent in
                guard size > 0 else { return }
                let percent = min(Int(sent * 100 / size), 99)
                self?.session?.show(notice: "送信中 \(label)\(name) \(percent)%")
            }
            try await socket.send(.string(end))
        } catch {
            // つながりが切れたときは、受信の側が切断として扱う
            if !(error is CancellationError), !Task.isCancelled {
                session.show(notice: "「\(name)」を送れませんでした")
            }
        }
    }

    /// ファイルを少しずつ読んで、送る。メインスレッドの外で動く
    private nonisolated static func stream(
        url: URL, id: UInt32, socket: URLSessionWebSocketTask,
        progress: @MainActor @Sendable (Int64) -> Void
    ) async throws {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        let header = Data([
            fileFrameFlag,
            UInt8(truncatingIfNeeded: id >> 24), UInt8(truncatingIfNeeded: id >> 16),
            UInt8(truncatingIfNeeded: id >> 8), UInt8(truncatingIfNeeded: id),
        ])
        var sent: Int64 = 0
        var reported: Int64 = 0
        while true {
            try Task.checkCancellation()
            guard let chunk = try handle.read(upToCount: chunkSize), !chunk.isEmpty else { break }
            var frame = header
            frame.append(chunk)
            try await socket.send(.data(frame))
            sent += Int64(chunk.count)
            if sent - reported >= 1024 * 1024 {
                reported = sent
                await progress(sent)
            }
        }
    }

    private nonisolated static func jsonString(_ object: [String: Any]) -> String {
        let data = (try? JSONSerialization.data(withJSONObject: object)) ?? Data()
        return String(decoding: data, as: UTF8.self)
    }

    /// 写真を送れる形にする。HEIC は PC で開けないことが多いので、JPEG にする
    private nonisolated static func preparePhoto(_ picked: PickedFile, index: Int, toClipboard: Bool) -> OutgoingFile? {
        let source = picked.url
        let ext = source.pathExtension.lowercased()
        let isImage = UTType(filenameExtension: ext)?.conforms(to: .image) ?? false
        var name = source.lastPathComponent
        var url = source

        let base = (name as NSString).deletingPathExtension
        let unknownName = ext.isEmpty || base.isEmpty || UUID(uuidString: base) != nil
            || (base.count >= 32 && base.allSatisfy { $0.isHexDigit || $0 == "-" })

        if isImage, ["heic", "heif"].contains(ext) {
            // 向きは jpegData が反映する
            if let image = UIImage(contentsOfFile: source.path), let jpeg = image.jpegData(compressionQuality: 0.9) {
                let directory = sendDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
                let converted = directory.appendingPathComponent("photo.jpg")
                do {
                    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
                    try jpeg.write(to: converted)
                    try? FileManager.default.removeItem(at: source.deletingLastPathComponent())
                    url = converted
                    name = unknownName ? "" : base + ".jpg"
                } catch {
                    // 変えられなければ、そのまま送る
                }
            }
        }
        if name.isEmpty || (url == source && unknownName) {
            let stamp = DateFormatter()
            stamp.locale = Locale(identifier: "en_US_POSIX")
            stamp.dateFormat = "yyyyMMdd-HHmmss"
            let suffix = index > 0 ? "-\(index + 1)" : ""
            let fileExt = url.pathExtension.isEmpty ? "jpg" : url.pathExtension.lowercased()
            name = "\(isImage ? "写真" : "動画")-\(stamp.string(from: Date()))\(suffix).\(fileExt)"
        }
        let size = (try? url.resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? 0
        return OutgoingFile(
            url: url, name: name, size: Int64(size),
            toClipboard: toClipboard, deleteFolderAfter: true, scoped: false
        )
    }

    // MARK: PCから届く制御メッセージ

    /// file_begin、file_end、file_saved を受け持つ。メインスレッドで呼ぶ
    func handleControl(_ type: String, _ json: [String: Any]) {
        switch type {
        case "file_begin":
            guard let rawID = json["id"] as? Int, let id = UInt32(exactly: rawID) else { return }
            let name = json["name"] as? String ?? "ファイル"
            let size = (json["size"] as? NSNumber)?.int64Value ?? 0
            let mime = json["mime"] as? String ?? ""
            // 続けて次のファイルが来るので、まとめて見せる待ちは延ばす
            shareTask?.cancel()
            shareTask = nil
            if Self.receiver.begin(id: id, name: name, size: size, mime: mime) {
                session?.show(notice: "PC から受信中: \(name)")
            } else {
                session?.show(notice: "「\(name)」を受信できません")
            }
        case "file_end":
            guard let rawID = json["id"] as? Int, let id = UInt32(exactly: rawID),
                  let done = Self.receiver.finish(id: id) else { return }
            guard done.complete else {
                try? FileManager.default.removeItem(at: done.directory)
                session?.show(notice: "「\(done.name)」を受信できませんでした")
                scheduleShare()
                return
            }
            if done.mime.hasPrefix("image/"), let image = UIImage(contentsOfFile: done.url.path) {
                UIPasteboard.general.image = image
            }
            received.append(done)
            session?.show(notice: "「\(done.name)」を受信しました")
            scheduleShare()
        case "file_saved":
            session?.show(notice: "PC の ダウンロード\\DuoRemote に保存しました")
        default:
            break
        }
    }

    /// 最後の file_end から少し待って、受け取ったものをまとめて1回、共有の画面に出す
    private func scheduleShare() {
        shareTask?.cancel()
        guard !received.isEmpty else { return }
        shareTask = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(700))
            guard let self, !Task.isCancelled else { return }
            let urls = self.received.map(\.url)
            self.received = []
            self.shareTask = nil
            Self.presentShare(urls)
        }
    }

    /// 切断したとき。送りかけのものを止め、受け取りかけのものを消す
    func cancelAll() {
        worker?.cancel()
        worker = nil
        queue.forEach { $0.cleanup() }
        queue = []
        nextID = 0
        batchTotal = 0
        batchDone = 0
        Self.receiver.cancelAll()
        // 受け取り終えていたものは、そのまま見せる
        scheduleShare()
    }

    private static func presentShare(_ urls: [URL]) {
        guard !urls.isEmpty else { return }
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let scene = scenes.first { $0.activationState == .foregroundActive } ?? scenes.first
        guard var top = scene?.keyWindow?.rootViewController ?? scene?.windows.first?.rootViewController else { return }
        while let next = top.presentedViewController, !next.isBeingDismissed { top = next }
        let controller = UIActivityViewController(activityItems: urls, applicationActivities: nil)
        // iPad では、基準になる場所が要る
        if let popover = controller.popoverPresentationController {
            popover.sourceView = top.view
            popover.sourceRect = CGRect(x: top.view.bounds.midX, y: top.view.bounds.midY, width: 0, height: 0)
            popover.permittedArrowDirections = []
        }
        top.present(controller, animated: true)
    }
}

// MARK: - 選ぶ画面

/// 写真の選択と、ファイルの選択を出す。ContentView の最後に付ける
struct FileSendPickers: ViewModifier {
    @EnvironmentObject private var session: RemoteSession
    @Binding var photosShown: Bool
    @Binding var filesShown: Bool
    /// PC のクリップボードにも入れるか
    let toClipboard: Bool
    @State private var photoItems: [PhotosPickerItem] = []

    func body(content: Content) -> some View {
        content
            .photosPicker(
                isPresented: $photosShown,
                selection: $photoItems,
                matching: .any(of: [.images, .videos])
            )
            .onChange(of: photoItems) { _, items in
                guard !items.isEmpty else { return }
                photoItems = []
                session.fileTransfer.sendPhotos(items, toClipboard: toClipboard)
            }
            .fileImporter(isPresented: $filesShown, allowedContentTypes: [.item], allowsMultipleSelection: true) { result in
                switch result {
                case .success(let urls):
                    session.fileTransfer.sendFiles(urls, toClipboard: toClipboard)
                case .failure:
                    session.show(notice: "ファイルを選べませんでした")
                }
            }
    }
}

extension View {
    func fileSendPickers(photosShown: Binding<Bool>, filesShown: Binding<Bool>, toClipboard: Bool) -> some View {
        modifier(FileSendPickers(photosShown: photosShown, filesShown: filesShown, toClipboard: toClipboard))
    }
}
