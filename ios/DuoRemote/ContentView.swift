import GameController
import SwiftUI
import UIKit

struct ContentView: View {
    @EnvironmentObject private var session: RemoteSession

    // 接続先は端末に覚えておく。トークンはキーチェーンにしまう
    @AppStorage("host") private var host = ""
    @AppStorage("port") private var port = "8765"
    @State private var token = ""
    @State private var showToken = false

    @AppStorage("pointerMode") private var pointerMode = PointerMode.direct
    @AppStorage(KeyCaptureView.commandAsControlKey) private var commandAsControl = false
    @AppStorage("quality") private var quality = VideoQuality.high
    @AppStorage("audioOn") private var audioOn = false
    @AppStorage("audioMix") private var audioMix = false
    /// 画面の端の余白（ポイント）。Duo の角が丸く、映像の角が削れるときに、広げる
    @AppStorage("edgeMargin") private var edgeMargin = 0
    @AppStorage("showLatency") private var showLatency = true
    @AppStorage(KeyCaptureView.zenkakuKey) private var zenkakuAsIME = false

    @Environment(\.scenePhase) private var scenePhase
    /// ノッチや Dynamic Island、ホームの線の分の余白。映像は端まで出すので、上に置くボタンはこの分だけ内側に寄せる
    @State private var safeInsets = EdgeInsets()

    @State private var showingWindows = false
    /// 画面全体の大きさ（ポイント。キーボードが出ていても変わらない）。ウィンドウの大きさを合わせるときに使う
    @State private var fullSize: CGSize = .zero
    @State private var refitTask: Task<Void, Never>?
    @State private var inputMode: InputMode = .touch
    /// 変えると、キーを受け取る部品が、もう一度入力を受け取る状態になる
    @State private var refocus = 0

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            if session.isConnectedOrReconnecting {
                connectedView
            } else {
                connectForm
            }
        }
        .onAppear {
            loadToken()
            updateIdleTimer()
        }
        // 接続中は、触らなくても画面がロックされないようにする
        .onChange(of: session.state) { _, _ in updateIdleTimer() }
        // 裏に回ったら接続を閉じ、戻ったらつなぎ直す
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .background: session.enterBackground()
            case .active: session.becomeActive()
            default: break
            }
        }
        .onChange(of: quality) { _, newValue in
            session.setQuality(newValue)
            // 画質で、音の形も変わる（低画質は、24000Hz のモノラル）。つなぎ直しのあと、あらためて頼まれる
        }
        .onChange(of: edgeMargin) { _, _ in refitWindow() }
        .onChange(of: audioOn) { _, _ in session.setAudio(audioOn, mixWithOthers: audioMix) }
        .onChange(of: audioMix) { _, _ in session.setAudio(audioOn, mixWithOthers: audioMix) }
        // 音を始められなかったら、スイッチをオフに戻す
        .onChange(of: session.audioFailures) { _, _ in audioOn = false }
    }

    private func updateIdleTimer() {
        UIApplication.shared.isIdleTimerDisabled = session.isConnectedOrReconnecting
    }

    private var connectedView: some View {
        // 映像は画面いっぱいに置いたままにして、iPhoneのキーボードは、その上に重ねる（映像を縮めない）。
        // 入力の欄とボタンだけが、キーボードの上に出る
        ZStack(alignment: .bottom) {
            VideoView(
                renderer: session.renderer,
                videoSize: session.videoSize,
                pointerMode: pointerMode,
                send: session.send
            )
            .padding(CGFloat(edgeMargin))
            .ignoresSafeArea() // キーボードの分も含めて、動かさない

            // 遅れは、メニューの中に置くと、数字が変わるたびに、メニューのスクロールが戻る。画面に出し続ける
            if showLatency {
                LatencyBadge()
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    .padding(8)
                    .padding(.top, safeInsets.top)
                    .padding(.leading, safeInsets.leading)
                    .allowsHitTesting(false)
            }

            VStack(spacing: 0) {
                HStack {
                    Spacer()
                    inputButton
                }
                if inputMode == .software {
                    SoftwareInputBar(refocus: refocus) {
                        // キーボードが閉じられたら、タッチだけに戻す
                        if inputMode == .software { inputMode = .touch }
                    }
                }
            }
        }
        // 画面の端までは使う。キーボードが出たときだけ、入力の欄がその上に上がる
        .ignoresSafeArea(.container)
        .background {
            HardwareKeyView(
                isActive: inputMode == .hardware && !showingWindows,
                refocus: refocus,
                session: session
            )
            .frame(width: 0, height: 0)
        }
        .background {
            GeometryReader { proxy in
                Color.clear
                    .onAppear {
                        fullSize = proxy.size
                        safeInsets = proxy.safeAreaInsets
                    }
                    .onChange(of: proxy.size) { _, size in
                        fullSize = size
                        refitWindow()
                    }
                    .onChange(of: proxy.safeAreaInsets) { _, insets in safeInsets = insets }
            }
            .ignoresSafeArea()
        }
        .overlay(alignment: .topTrailing) { menu }
        .overlay(alignment: .top) {
            if let notice = session.notice {
                Text(notice)
                    .font(.callout)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 8)
                    .background(.regularMaterial, in: Capsule())
                    .padding(.top, 8 + safeInsets.top)
                    .padding(.horizontal, 60)
                    .transition(.opacity)
            }
        }
        .overlay {
            if session.state == .reconnecting {
                VStack(spacing: 12) {
                    ProgressView()
                    Text("つなぎ直しています…")
                    Button("やめる") { session.disconnect() }
                        .buttonStyle(.bordered)
                }
                .padding(24)
                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
            }
        }
        .animation(.default, value: session.notice)
        .sheet(isPresented: $showingWindows, onDismiss: { refocus += 1 }) {
            WindowPicker(fitSize: fitSize)
        }
        .onAppear {
            inputMode = GCKeyboard.coalesced != nil ? .hardware : .touch
        }
        .onReceive(NotificationCenter.default.publisher(for: .GCKeyboardDidConnect)) { _ in
            inputMode = .hardware
        }
        .onReceive(NotificationCenter.default.publisher(for: .GCKeyboardDidDisconnect)) { _ in
            if inputMode == .hardware { inputMode = .touch }
        }
    }

    /// 映像を出している範囲の大きさ（余白を除く）。ウィンドウや仮想画面の大きさを、これに合わせる
    private var fitSize: CGSize {
        let margin = CGFloat(edgeMargin) * 2
        return CGSize(width: max(fullSize.width - margin, 1), height: max(fullSize.height - margin, 1))
    }

    /// この端末の、いまの画面の大きさ（実機が報告する値）。折りたたみの開閉や回転で変わる
    private var screenDescription: String {
        _ = fullSize // 大きさが変わったら、作り直す
        let screen = UIApplication.shared.connectedScenes
            .compactMap { ($0 as? UIWindowScene)?.screen }.first
        guard let screen else { return "画面: 不明" }
        let pt = screen.bounds.size
        let px = screen.nativeBounds.size
        return "画面: \(Int(pt.width))×\(Int(pt.height))pt（\(Int(px.width))×\(Int(px.height))px、\(String(format: "%.2f", screen.nativeScale))倍）"
    }

    /// 回したり、Duoを閉じたり開いたりして画面の大きさが変わったら、ウィンドウの大きさを合わせ直す。
    /// 変わっている途中で何度も送らないよう、少し待ってから送る
    private func refitWindow() {
        refitTask?.cancel()
        refitTask = Task {
            try? await Task.sleep(for: .milliseconds(600))
            guard !Task.isCancelled else { return }
            session.refreshDisplay(fit: fitSize)
        }
    }

    /// 右下のボタン。iPhoneのキーボードを出す・しまう。PCのIMEの状態も見せる
    private var inputButton: some View {
        Button {
            inputMode = inputMode == .software ? .touch : .software
        } label: {
            HStack(spacing: 6) {
                Image(systemName: inputMode == .software ? "keyboard.chevron.compact.down" : inputMode.systemImage)
                if inputMode != .touch, let on = session.imeOn {
                    Text(on ? "あ" : "A").font(.callout.bold())
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .foregroundStyle(.white)
            .background(.black.opacity(0.5), in: Capsule())
        }
        .padding(12)
        .padding(.trailing, safeInsets.trailing)
        // iPhoneのキーボードが出ているときは、映像がキーボードの上で終わるので、ホームの線の分は要らない
        .padding(.bottom, inputMode == .software ? 0 : safeInsets.bottom)
    }

    private var menu: some View {
        Menu {
            if let window = session.currentWindow {
                Text("表示中: \(window.title)")
            } else if session.viewMode == .desktop {
                Text("表示中: 仮想画面")
            }
            Text(screenDescription)
            Toggle("PCの音を鳴らす", systemImage: session.audioOn ? "speaker.wave.2.fill" : "speaker.slash", isOn: $audioOn)
            Toggle("他のアプリの音と混ぜる", systemImage: "speaker.badge.exclamationmark", isOn: $audioMix)
            Picker("入力", selection: $inputMode) {
                ForEach(InputMode.allCases) { mode in
                    Label(mode.label, systemImage: mode.systemImage).tag(mode)
                }
            }
            .pickerStyle(.inline)
            Menu("操作の設定", systemImage: "slider.horizontal.3") {
                Picker("画面の触り方", selection: $pointerMode) {
                    ForEach(PointerMode.allCases) { mode in
                        Text(mode.label).tag(mode)
                    }
                }
                .pickerStyle(.inline)
                Toggle("command を Ctrl にする", isOn: $commandAsControl)
                Toggle("半角/全角キーで IME を切り替える", isOn: $zenkakuAsIME)
                Toggle("遅れを画面に出す", isOn: $showLatency)
                Picker("画面の端の余白", selection: $edgeMargin) {
                    Text("なし").tag(0)
                    Text("小（10）").tag(10)
                    Text("中（20）").tag(20)
                    Text("大（30）").tag(30)
                }
                Picker("画質", selection: $quality) {
                    ForEach(VideoQuality.allCases) { q in
                        Text(q.label).tag(q)
                    }
                }
            }
            Button("ウィンドウを選ぶ", systemImage: "macwindow") {
                session.requestWindows()
                showingWindows = true
            }
            if session.virtualAvailable {
                Button("仮想画面を表示", systemImage: "rectangle.on.rectangle") {
                    session.showDesktop(fit: fitSize)
                }
                Button("持ってきたウィンドウを元に戻す", systemImage: "arrow.uturn.backward") {
                    session.restoreWindows()
                }
            }
            if session.viewMode != .screen {
                Button("本物の画面を表示", systemImage: "display") { session.showScreen() }
            }
            Button("切断", systemImage: "xmark.circle", role: .destructive) { session.disconnect() }
        } label: {
            Image(systemName: "ellipsis.circle.fill")
                .font(.title)
                .symbolRenderingMode(.palette)
                .foregroundStyle(.white, .black.opacity(0.5))
                .padding(12)
        }
        .padding(.top, safeInsets.top)
        .padding(.trailing, safeInsets.trailing)
    }

    private var connectForm: some View {
        Form {
            Section("接続先") {
                TextField("ホスト（例: 100.x.x.x）", text: $host)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                TextField("ポート", text: $port)
                    .keyboardType(.numberPad)
                HStack {
                    // 伏せ字のままだと、打ち間違いに気づけないので、目のボタンで表示を切り替える
                    Group {
                        if showToken {
                            TextField("トークン", text: $token)
                        } else {
                            SecureField("トークン", text: $token)
                        }
                    }
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.asciiCapable) // 日本語のキーボードで、全角の文字が入らないようにする
                    .font(.body.monospaced())
                    Button {
                        showToken.toggle()
                    } label: {
                        Image(systemName: showToken ? "eye.slash" : "eye")
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel(showToken ? "トークンを隠す" : "トークンを表示する")
                }
            }
            Section {
                Picker("画質", selection: $quality) {
                    ForEach(VideoQuality.allCases) { q in
                        Text(q.label).tag(q)
                    }
                }
            } footer: {
                Text("外（4G/5G）からつなぐときは、低くすると遅れがたまりにくくなります")
            }
            Section {
                Button(session.state == .connecting ? "接続しています…" : "接続") {
                    // 貼り付けたときに付いてくる空白や、全角の英数字を直してから使う
                    host = Self.cleaned(host)
                    port = Self.cleaned(port)
                    token = Self.cleaned(token)
                    Keychain.save("token", token)
                    session.setAudio(audioOn, mixWithOthers: audioMix)
                    session.connect(host: host, port: Int(port) ?? 8765, token: token, quality: quality)
                }
                .disabled(host.isEmpty || token.isEmpty || session.state == .connecting)
                if session.state == .connecting {
                    Button("やめる", role: .cancel) { session.disconnect() }
                }
                if let message = session.errorMessage {
                    Text(message).foregroundStyle(.red)
                }
            }
        }
        .scrollContentBackground(.hidden)
    }

    /// 全角の英数字と記号を半角にし、前後の空白と改行を取り除く
    static func cleaned(_ text: String) -> String {
        let halfWidth = text.applyingTransform(.fullwidthToHalfwidth, reverse: false) ?? text
        return halfWidth.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func loadToken() {
        if let saved = Keychain.load("token") {
            token = saved
        } else if let old = UserDefaults.standard.string(forKey: "token") {
            // 0.4 までは、トークンを普通の設定にしまっていた。キーチェーンに移す
            token = old
            Keychain.save("token", old)
            UserDefaults.standard.removeObject(forKey: "token")
        }
    }
}


/// 画面の隅に出し続ける、PCとの往復の時間。この部品だけが、数字の変化を見る
struct LatencyBadge: View {
    @EnvironmentObject private var session: RemoteSession

    var body: some View {
        if let ms = session.latencyMs {
            Text("\(ms)ms")
                .font(.caption2.monospacedDigit())
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .foregroundStyle(.white.opacity(0.85))
                .background(.black.opacity(0.35), in: Capsule())
        }
    }
}
