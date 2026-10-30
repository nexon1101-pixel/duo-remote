import SwiftUI
import UIKit

/// 入力の方式。
enum InputMode: String, CaseIterable, Identifiable {
    /// タッチでのマウス操作だけ
    case touch
    /// iPhoneのキーボード。iPhone側で変換まで済ませ、確定した文字を送る
    case software
    /// Bluetoothキーボード。キーをそのまま送り、日本語の変換はPCのIMEで行う
    case hardware
    /// アプリのキーボード（映像の上に重ねて出す、PCのキー）。日本語の変換はPCのIMEで行う
    case overlay

    var id: String { rawValue }

    var label: String {
        switch self {
        case .touch: "タッチだけ"
        case .software: "iPhoneのキーボード"
        case .hardware: "Bluetoothキーボード"
        case .overlay: "アプリのキーボード"
        }
    }

    var systemImage: String {
        switch self {
        case .touch: "hand.tap"
        case .software: "keyboard"
        case .hardware: "keyboard.badge.ellipsis"
        case .overlay: "keyboard.fill"
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
    /// 押したときに決めたスキャンコード（キーの番号ごと）。離すときも同じものを使う
    private var pressed: [Int: Int] = [:]
    /// 半角/全角として扱ったキー（離したときに、PCへ何も送らないため）
    private var zenkakuDown = Set<Int>()

    private var zenkakuAsIME: Bool {
        UserDefaults.standard.object(forKey: Self.zenkakuKey) as? Bool ?? false
    }

    /// Shiftを押しているか（Shift+` は ~ なので、IMEの切り替えにしない）
    private var shiftIsDown: Bool { pressed[0xE1] != nil || pressed[0xE5] != nil }

    /// Macのキーボードの command キーを、Ctrl として送るか（設定の「command を Ctrl にする」）
    static let commandAsControlKey = "commandAsControl"
    /// 日本語キーボードの「半角/全角」キー（HID 0x35。USキーボードでは ` キー）を、IMEのオン・オフの切り替えとして送るか
    static let zenkakuKey = "zenkakuAsIME"

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
            case KeyMap.hidGrave where zenkakuAsIME && !shiftIsDown:
                // 半角/全角。PCのキー配列がJISでないと、スキャンコードでは、IMEが切り替わらないので、IMEの切り替えとして送る
                session?.toggleIME()
                zenkakuDown.insert(usage)
            default:
                if let sc = scanCode(hid: usage) {
                    pressed[usage] = sc
                    keyDown(sc)
                } else {
                    others.insert(press)
                }
            }
        }
        if !others.isEmpty { super.pressesBegan(others, with: event) }
    }

    private func scanCode(hid usage: Int) -> Int? {
        if UserDefaults.standard.bool(forKey: Self.commandAsControlKey) {
            if usage == 0xE3 { return 0x1D } // 左command → 左Ctrl
            if usage == 0xE7 { return 0xE01D } // 右command → 右Ctrl
        }
        return KeyMap.scanCode(hid: usage)
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
            if zenkakuDown.remove(usage) != nil { continue }
            if let sc = pressed.removeValue(forKey: usage) {
                keyUp(sc)
            } else if KeyMap.scanCode(hid: usage) == nil {
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
        pressed.removeAll()
    }
}

// MARK: - iPhoneのキーボード

/// iPhoneのキーボードで打つ欄と、PCのキー（Esc、Tab、矢印など）の帯。
struct SoftwareInputBar: View {
    @EnvironmentObject private var session: RemoteSession
    let refocus: Int
    /// キーボードが閉じられたとき
    let onClose: () -> Void

    /// 押してある修飾キー（仮想キーコード）。次に押したキーか、次に打った英字・数字と一緒に押す（Ctrl+C、Shift+→ など）
    @State private var modifiers: [Int] = []
    /// オンのとき、英字のキーボード（QWERTY）を出す。日本語の変換はできない。
    /// オフのときは、iPhoneで最後に使ったキーボードが出る（フリックなどは、iPhoneの設定で決まる）
    @AppStorage("asciiKeyboard") private var asciiKeyboard = false

    var body: some View {
        VStack(spacing: 6) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    imeButton
                    key("ABC", isOn: asciiKeyboard) { asciiKeyboard.toggle() }
                    modifier("Ctrl", KeyMap.vkControl)
                    modifier("Shift", KeyMap.vkShift)
                    modifier("Alt", KeyMap.vkMenu)
                    key("Win") { tap(KeyMap.vkLWin) }
                    key("Esc") { tap(KeyMap.vkEscape) }
                    key("Tab") { tap(KeyMap.vkTab) }
                    key(systemImage: "arrow.left") { tap(KeyMap.vkLeft) }
                    key(systemImage: "arrow.down") { tap(KeyMap.vkDown) }
                    key(systemImage: "arrow.up") { tap(KeyMap.vkUp) }
                    key(systemImage: "arrow.right") { tap(KeyMap.vkRight) }
                    key("Home") { tap(KeyMap.vkHome) }
                    key("End") { tap(KeyMap.vkEnd) }
                    key("BS") { tap(KeyMap.vkBack) }
                    key("Del") { tap(KeyMap.vkDelete) }
                    key("Enter") { tap(KeyMap.vkReturn) }
                }
                .padding(.horizontal, 8)
            }
            ComposeField(refocus: refocus, asciiOnly: asciiKeyboard, onText: send, onKey: { tap($0) }, onEnd: onClose)
                .frame(height: 36)
                .background(RoundedRectangle(cornerRadius: 10).fill(Color.white.opacity(0.08)))
                .padding(.horizontal, 8)
        }
        .padding(.vertical, 8)
        .glass(in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        // 画面の端から少し浮かせる
        .padding(.horizontal, 8)
        .padding(.bottom, 4)
    }

    private var imeButton: some View {
        let label = switch session.imeOn {
        case true?: "あ"
        case false?: "A"
        case nil: "あ/A"
        }
        return key(label, isOn: session.imeOn == true) { session.setIME(!(session.imeOn ?? false)) }
    }

    private func send(_ text: String) {
        if !modifiers.isEmpty {
            // 英字と数字は、仮想キーコードが大文字のASCIIと同じ
            if text.count == 1, let c = text.uppercased().unicodeScalars.first,
               c.isASCII, CharacterSet.alphanumerics.contains(c) {
                tap(Int(c.value))
                return
            }
            modifiers = []
        }
        session.sendText(text)
    }

    /// キーを押して離す。修飾キーが押してあれば、一緒に押してから外す
    private func tap(_ vk: Int) {
        session.tapKey(vk: vk, modifiers: modifiers)
        modifiers = []
    }

    private func modifier(_ title: String, _ vk: Int) -> some View {
        key(title, isOn: modifiers.contains(vk)) {
            if let i = modifiers.firstIndex(of: vk) {
                modifiers.remove(at: i)
            } else {
                modifiers.append(vk)
            }
        }
    }

    private func key(_ title: String, isOn: Bool = false, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.callout.weight(.semibold))
                .foregroundStyle(.white)
                .frame(minWidth: 28, minHeight: 34)
                .padding(.horizontal, 10)
                .background(
                    RoundedRectangle(cornerRadius: 9, style: .continuous)
                        .fill(isOn ? Color.accentColor : Color.white.opacity(0.12))
                )
        }
        .buttonStyle(KeyPressStyle())
    }

    private func key(systemImage: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: systemImage)
                .font(.callout.weight(.semibold))
                .foregroundStyle(.white)
                .frame(minWidth: 28, minHeight: 34)
                .padding(.horizontal, 10)
                .background(
                    RoundedRectangle(cornerRadius: 9, style: .continuous)
                        .fill(Color.white.opacity(0.12))
                )
        }
        .buttonStyle(KeyPressStyle())
    }
}

/// 押したとき、少し縮むボタン
private struct KeyPressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.94 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

/// 打っている途中の文字（変換前）を見せる欄。確定した文字は送って、欄を空にする。
struct ComposeField: UIViewRepresentable {
    let refocus: Int
    /// trueなら、英字のキーボード（QWERTY）にする
    let asciiOnly: Bool
    let onText: (String) -> Void
    /// 欄が空のときの、改行と削除（PCのEnterとBackspaceにする）
    let onKey: (Int) -> Void
    let onEnd: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(parent: self) }

    func makeUIView(context: Context) -> ComposeTextView {
        let view = ComposeTextView()
        view.delegate = context.coordinator
        view.font = .preferredFont(forTextStyle: .body)
        view.backgroundColor = .clear // 後ろの SwiftUI 側で、薄い角丸を敷く
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
        let type: UIKeyboardType = asciiOnly ? .asciiCapable : .default
        if uiView.keyboardType != type {
            uiView.keyboardType = type
            uiView.reloadInputViews()
        }
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
