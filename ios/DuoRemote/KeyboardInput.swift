import SwiftUI
import UIKit

/// 入力の方式。
enum InputMode: String, CaseIterable, Identifiable {
    /// タッチでのマウス操作だけ
    case touch
    /// iPhoneのキーボード（DuoFlickなど）。iPhone側で変換まで済ませ、確定した文字を送る
    case software
    /// Bluetoothキーボード。キーをそのまま送り、日本語の変換はPCのIMEで行う
    case hardware

    var id: String { rawValue }

    var label: String {
        switch self {
        case .touch: "タッチだけ"
        case .software: "iPhoneのキーボード"
        case .hardware: "Bluetoothキーボード"
        }
    }

    var systemImage: String {
        switch self {
        case .touch: "hand.tap"
        case .software: "keyboard"
        case .hardware: "keyboard.badge.ellipsis"
        }
    }
}

// MARK: - Bluetoothキーボード

/// Bluetoothキーボードのキーを受け取る、見えない入れ物。
/// 文字入力の部品ではないので、iPhoneのキーボードは出ず、iPhone側のIMEも通らない。
struct HardwareKeyView: UIViewRepresentable {
    let isActive: Bool
    /// 値が変わると、もう一度キーを受け取れる状態にする（シートを閉じたあとなど）
    let refocus: Int
    let session: RemoteSession

    func makeUIView(context: Context) -> KeyCaptureView {
        let view = KeyCaptureView()
        view.session = session
        return view
    }

    func updateUIView(_ uiView: KeyCaptureView, context: Context) {
        uiView.session = session
        uiView.isActive = isActive
        uiView.updateFocus()
    }
}

final class KeyCaptureView: UIView {
    weak var session: RemoteSession?
    var isActive = false

    /// 押しているキー（スキャンコード）と、押し続けたときの繰り返し
    private var held: [Int: Task<Void, Never>] = [:]

    /// 押し続けたとき、繰り返しが始まるまでの時間と、その間隔。
    /// PCは、送ったキーを自分では繰り返さないので、こちらで繰り返す
    private static let repeatDelay: Duration = .milliseconds(450)
    private static let repeatInterval: Duration = .milliseconds(33)

    override var canBecomeFirstResponder: Bool { isActive }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        updateFocus()
    }

    func updateFocus() {
        // 画面の切り替えの途中だと失敗することがあるので、少し後で行う
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            if self.isActive, self.window != nil, !self.isFirstResponder {
                self.becomeFirstResponder()
            } else if !self.isActive, self.isFirstResponder {
                self.resignFirstResponder()
            }
        }
    }

    override func resignFirstResponder() -> Bool {
        releaseAll()
        return super.resignFirstResponder()
    }

    override func pressesBegan(_ presses: Set<UIPress>, with event: UIPressesEvent?) {
        var others = Set<UIPress>()
        for press in presses {
            guard let usage = press.key?.keyCode.rawValue else {
                others.insert(press)
                continue
            }
            switch usage {
            case KeyMap.hidLang1: // かな
                session?.setIME(true)
            case KeyMap.hidLang2: // 英数
                session?.setIME(false)
            default:
                if let sc = KeyMap.scanCode(hid: usage) {
                    keyDown(sc)
                } else {
                    others.insert(press)
                }
            }
        }
        if !others.isEmpty { super.pressesBegan(others, with: event) }
    }

    override func pressesEnded(_ presses: Set<UIPress>, with event: UIPressesEvent?) {
        let others = keysUp(presses)
        if !others.isEmpty { super.pressesEnded(others, with: event) }
    }

    override func pressesCancelled(_ presses: Set<UIPress>, with event: UIPressesEvent?) {
        let others = keysUp(presses)
        if !others.isEmpty { super.pressesCancelled(others, with: event) }
    }

    private func keysUp(_ presses: Set<UIPress>) -> Set<UIPress> {
        var others = Set<UIPress>()
        for press in presses {
            guard let usage = press.key?.keyCode.rawValue else {
                others.insert(press)
                continue
            }
            if usage == KeyMap.hidLang1 || usage == KeyMap.hidLang2 { continue }
            if let sc = KeyMap.scanCode(hid: usage) {
                keyUp(sc)
            } else {
                others.insert(press)
            }
        }
        return others
    }

    private func keyDown(_ sc: Int) {
        guard held[sc] == nil else { return }
        session?.sendScan(sc, down: true)
        if KeyMap.isModifier(scanCode: sc) {
            held[sc] = Task {}
            return
        }
        held[sc] = Task { [weak self] in
            try? await Task.sleep(for: Self.repeatDelay)
            while !Task.isCancelled {
                self?.session?.sendScan(sc, down: true)
                try? await Task.sleep(for: Self.repeatInterval)
            }
        }
    }

    private func keyUp(_ sc: Int) {
        guard let task = held.removeValue(forKey: sc) else { return }
        task.cancel()
        session?.sendScan(sc, down: false)
    }

    /// 押しているキーを全部離す。アプリが裏に回ったときなどに、PCでキーが押されたままにならないようにする
    private func releaseAll() {
        for sc in Array(held.keys) {
            keyUp(sc)
        }
    }
}

// MARK: - iPhoneのキーボード

/// iPhoneのキーボードで打つ欄と、PCのキー（Esc、Tab、矢印など）の帯。
struct SoftwareInputBar: View {
    @EnvironmentObject private var session: RemoteSession
    let refocus: Int
    /// キーボードが閉じられたとき
    let onClose: () -> Void

    /// オンのとき、次に打った英字か数字を、Ctrlと一緒に押す（Ctrl+C など）
    @State private var ctrl = false

    var body: some View {
        VStack(spacing: 6) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    imeButton
                    key("Ctrl", isOn: ctrl) { ctrl.toggle() }
                    key("Esc") { session.tapKey(vk: KeyMap.vkEscape) }
                    key("Tab") { session.tapKey(vk: KeyMap.vkTab) }
                    key(systemImage: "arrow.left") { session.tapKey(vk: KeyMap.vkLeft) }
                    key(systemImage: "arrow.down") { session.tapKey(vk: KeyMap.vkDown) }
                    key(systemImage: "arrow.up") { session.tapKey(vk: KeyMap.vkUp) }
                    key(systemImage: "arrow.right") { session.tapKey(vk: KeyMap.vkRight) }
                    key("BS") { session.tapKey(vk: KeyMap.vkBack) }
                    key("Del") { session.tapKey(vk: KeyMap.vkDelete) }
                    key("Enter") { session.tapKey(vk: KeyMap.vkReturn) }
                }
                .padding(.horizontal, 8)
            }
            ComposeField(refocus: refocus, onText: send, onKey: { session.tapKey(vk: $0) }, onEnd: onClose)
                .frame(height: 36)
                .padding(.horizontal, 8)
        }
        .padding(.vertical, 6)
        .background(.bar)
    }

    private var imeButton: some View {
        let label = switch session.imeOn {
        case true?: "あ"
        case false?: "A"
        case nil: "あ/A"
        }
        return key(label) { session.setIME(!(session.imeOn ?? false)) }
    }

    private func send(_ text: String) {
        if ctrl {
            ctrl = false
            // 英字と数字は、仮想キーコードが大文字のASCIIと同じ
            if text.count == 1, let c = text.uppercased().unicodeScalars.first,
               c.isASCII, CharacterSet.alphanumerics.contains(c) {
                session.tapKey(vk: Int(c.value), ctrl: true)
                return
            }
        }
        session.sendText(text)
    }

    private func key(_ title: String, isOn: Bool = false, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title).font(.callout.monospaced()).frame(minWidth: 28)
        }
        .buttonStyle(.bordered)
        .tint(isOn ? Color.accentColor : Color.secondary)
    }

    private func key(systemImage: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemImage).frame(minWidth: 28)
        }
        .buttonStyle(.bordered)
        .tint(Color.secondary)
    }
}

/// 打っている途中の文字（変換前）を見せる欄。確定した文字は送って、欄を空にする。
struct ComposeField: UIViewRepresentable {
    let refocus: Int
    let onText: (String) -> Void
    /// 欄が空のときの、改行と削除（PCのEnterとBackspaceにする）
    let onKey: (Int) -> Void
    let onEnd: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(parent: self) }

    func makeUIView(context: Context) -> ComposeTextView {
        let view = ComposeTextView()
        view.delegate = context.coordinator
        view.font = .preferredFont(forTextStyle: .body)
        view.backgroundColor = .secondarySystemBackground
        view.layer.cornerRadius = 8
        view.textContainer.maximumNumberOfLines = 1
        view.isScrollEnabled = false
        // iPhone側で文字を書き換えないようにする
        view.autocorrectionType = .no
        view.autocapitalizationType = .none
        view.spellCheckingType = .no
        view.smartQuotesType = .no
        view.smartDashesType = .no
        view.smartInsertDeleteType = .no
        view.onDeleteEmpty = { [weak coordinator = context.coordinator] in
            coordinator?.parent.onKey(KeyMap.vkBack)
        }
        return view
    }

    func updateUIView(_ uiView: ComposeTextView, context: Context) {
        context.coordinator.parent = self
        if context.coordinator.lastRefocus != refocus {
            context.coordinator.lastRefocus = refocus
            DispatchQueue.main.async {
                if uiView.window != nil, !uiView.isFirstResponder { uiView.becomeFirstResponder() }
            }
        }
    }

    final class Coordinator: NSObject, UITextViewDelegate {
        var parent: ComposeField
        var lastRefocus = -1

        init(parent: ComposeField) {
            self.parent = parent
        }

        func textViewDidChange(_ textView: UITextView) {
            // 変換中（下線の付いた文字）は待つ。確定したら送る
            guard textView.markedTextRange == nil, let text = textView.text, !text.isEmpty else { return }
            textView.text = ""
            parent.onText(text)
        }

        func textView(_ textView: UITextView, shouldChangeTextIn range: NSRange, replacementText text: String) -> Bool {
            guard text == "\n", textView.markedTextRange == nil else { return true }
            parent.onKey(KeyMap.vkReturn)
            return false
        }

        func textViewDidEndEditing(_ textView: UITextView) {
            parent.onEnd()
        }
    }
}

final class ComposeTextView: UITextView {
    var onDeleteEmpty: () -> Void = {}

    override func deleteBackward() {
        if text.isEmpty, markedTextRange == nil {
            onDeleteEmpty()
        } else {
            super.deleteBackward()
        }
    }
}
