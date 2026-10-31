import GameController
import SwiftUI
import UIKit

struct ContentView: View {
    @EnvironmentObject private var session: RemoteSession

    // 接続先は端末に覚えておく。トークンはキーチェーンにしまう
    @AppStorage("host") private var host = ""
    /// 外から（Tailscale）つなぐときのホスト。省略できる
    @AppStorage("hostRemote") private var hostRemote = ""
    /// 前回つながったホスト（0: 家、1: 外）。次は、こちらから試す
    @AppStorage("lastHostIndex") private var lastHostIndex = 0
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
    @AppStorage(RemoteSession.useHEVCKey) private var useHEVC = true
    @AppStorage(RemoteSession.autoQualityKey) private var autoQuality = true
    /// トラックパッドのピンチ（video: 映像の拡大・縮小、pc: PC に Ctrl+ホイールを送る）
    @AppStorage(TrackpadCoordinator.pinchModeKey) private var trackpadPinch = "video"
    @AppStorage(KeyCaptureView.zenkakuKey) private var zenkakuAsIME = false
    /// 右下のボタンで出すキーボード（iPhoneのキーボードか、アプリのキーボード）
    @AppStorage("keyboardKind") private var keyboardKind = InputMode.overlay
    /// 重ねて出すトラックパッド（アプリのキーボードと同じく、動かせて、大きさを変えられる）
    @AppStorage("showFloatingTrackpad") private var showTrackpad = false
    @AppStorage(OverlayKeyboard.layoutKey) private var overlayLayout = OverlayKeyboardLayout.us
    @AppStorage(OverlayKeyboard.opacityKey) private var overlayOpacity = 0.8

    @Environment(\.scenePhase) private var scenePhase
    /// ノッチや Dynamic Island、ホームの線の分の余白。映像は端まで出すので、上に置くボタンはこの分だけ内側に寄せる
    @State private var safeInsets = EdgeInsets()

    @State private var showingWindows = false
    @State private var showingMonitors = false
    @State private var showingSettings = false
    /// 写真・ファイルの選択画面を出すか、と、PC のクリップボードにも入れるか
    @State private var showingPhotoPicker = false
    @State private var showingFileImporter = false
    @State private var sendToPCClipboard = false
    /// 画面全体の大きさ（ポイント。キーボードが出ていても変わらない）。ウィンドウの大きさを合わせるときに使う
    @State private var fullSize: CGSize = .zero
    @State private var refitTask: Task<Void, Never>?
    @State private var inputMode: InputMode = .touch
    /// 変えると、キーを受け取る部品が、もう一度入力を受け取る状態になる
    @State private var refocus = 0
    /// Duo の開き具合と、折り目の位置
    @State private var fold = FoldState()
    @AppStorage("dockWhenHalfOpen") private var dockWhenHalfOpen = true
    @AppStorage(DockedInput.showKeysKey) private var dockShowKeys = true
    @AppStorage(DockedInput.showPadKey) private var dockShowPad = true
    /// アプリのキーボードを、普通の iPhone のキーボードのように下に固定する
    @AppStorage(OverlayKeyboard.colorKeysKey) private var colorKeys = true
    /// キーボードを下部に固定するか（false ならフロート）
    @AppStorage(OverlayKeyboard.atBottomKey) private var keyboardAtBottom = false
    /// トラックパッドを下部に固定するか（false ならフロート）
    @AppStorage(OverlayKeyboard.padAtBottomKey) private var padAtBottom = false
    /// 前の版の、3つから選ぶ置き方の設定。一度だけ、2つの設定に引き継ぐ
    @AppStorage("overlayDockMode") private var legacyDockMode = ""

    /// 下部に固定したキーボードを出すか
    private var bottomKeys: Bool { inputMode == .overlay && keyboardAtBottom }
    /// 下部に固定したトラックパッドを出すか
    private var bottomPad: Bool { showTrackpad && padAtBottom }

    /// 前の版の「置き方」（free / keyboard / both）を、キーボードとトラックパッドの2つの設定に引き継ぐ
    private func migrateDockMode() {
        guard !legacyDockMode.isEmpty else { return }
        keyboardAtBottom = legacyDockMode != "free"
        padAtBottom = legacyDockMode == "both"
        if padAtBottom { showTrackpad = true }
        legacyDockMode = ""
    }

    var body: some View {
        ZStack {
            if session.isConnectedOrReconnecting {
                Color.black.ignoresSafeArea()
            } else {
                backdrop
            }
            if session.isConnectedOrReconnecting {
                connectedView
            } else {
                connectForm
            }
        }
        .onAppear {
            loadToken()
            updateIdleTimer()
            migrateDockMode()
        }
        // トラックパッドを下部に固定にしたときは、そのまま出す（出し入れは、トラックパッドのボタンで）
        .onChange(of: padAtBottom) { _, atBottom in
            if atBottom { showTrackpad = true }
        }
        // 接続中は、触らなくても画面がロックされないようにする
        .onChange(of: session.state) { _, _ in updateIdleTimer() }
        .onChange(of: session.activeHost) { _, active in
            guard let active else { return }
            if active == host { lastHostIndex = 0 } else if active == hostRemote { lastHostIndex = 1 }
        }
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
            // 半開きのときは、折り目より上だけに映す
            .frame(height: dockedFold.map { $0.minY })
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)

            if let fold = dockedFold {
                DockedInput(menuButton: AnyView(menu(compact: true)), windowsButton: AnyView(windowsButton(compact: true)))
                    .padding(.horizontal, 10 + max(safeInsets.leading, safeInsets.trailing))
                    .padding(.top, 8)
                    .padding(.bottom, 8 + safeInsets.bottom)
                    .frame(height: max(fullSize.height - fold.maxY, 0))
                    .frame(maxHeight: .infinity, alignment: .bottom)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }

            // 遅れは、メニューの中に置くと、数字が変わるたびに、メニューのスクロールが戻る。画面に出し続ける
            if showLatency {
                LatencyBadge()
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    .padding(8)
                    .padding(.top, safeInsets.top)
                    .padding(.leading, safeInsets.leading)
                    .allowsHitTesting(false)
            }

            if showTrackpad && halfOpenFold == nil && !padAtBottom {
                OverlayTrackpad(session: session, onClose: { showTrackpad = false }, onDock: { padAtBottom = true })
                    .padding(.top, safeInsets.top)
                    .padding(.leading, safeInsets.leading)
                    .padding(.trailing, safeInsets.trailing)
                    .padding(.bottom, safeInsets.bottom)
                    .transition(.opacity)
            }

            if inputMode == .overlay && halfOpenFold == nil && !keyboardAtBottom {
                OverlayKeyboard(session: session, onClose: {
                    if inputMode == .overlay { inputMode = .touch }
                }, onDock: {
                    keyboardAtBottom = true
                })
                .padding(.top, safeInsets.top)
                .padding(.leading, safeInsets.leading)
                .padding(.trailing, safeInsets.trailing)
                .padding(.bottom, safeInsets.bottom)
            }

            VStack(spacing: 0) {
                if halfOpenFold != nil {
                    // 半開きで、キーボードもトラックパッドもしまったときは、出し直すボタンだけを置く
                    if dockedFold == nil {
                        DockedInputRestoreButton()
                            .padding(.bottom, 12 + safeInsets.bottom)
                            .transition(.move(edge: .bottom).combined(with: .opacity))
                    }
                } else {
                    HStack(spacing: UIMetrics.spacing) {
                        Spacer()
                        windowsButton(compact: false)
                        trackpadButton
                        inputButton
                    }
                    .padding(UIMetrics.edgePadding)
                    .padding(.trailing, safeInsets.trailing)
                    // iPhoneのキーボードが出ているときは、映像がキーボードの上で終わるので、ホームの線の分は要らない
                    .padding(.bottom, inputMode == .software || bottomKeys || bottomPad ? 0 : safeInsets.bottom)
                    if inputMode == .software {
                        SoftwareInputBar(refocus: refocus) {
                            // キーボードが閉じられたら、タッチだけに戻す
                            if inputMode == .software { inputMode = .touch }
                        }
                    }
                    if bottomKeys || bottomPad {
                        BottomKeyboard(
                            session: session,
                            showKeys: bottomKeys,
                            showPad: bottomPad,
                            padHeight: min(max(fullSize.height * 0.18, 110), 170),
                            maxKeysHeight: fullSize.height * (bottomPad ? 0.35 : 0.45),
                            bottomInset: safeInsets.bottom,
                            onUndock: { keyboardAtBottom = false },
                            onUndockPad: { padAtBottom = false }
                        )
                        .transition(.move(edge: .bottom))
                    }
                }
            }
            .animation(.spring(duration: 0.35), value: inputMode)
            .animation(.spring(duration: 0.35), value: keyboardAtBottom)
            .animation(.spring(duration: 0.35), value: bottomPad)
        }
        // 画面の端までは使う。キーボードが出たときだけ、入力の欄がその上に上がる
        .ignoresSafeArea(.container)
        .background {
            FoldWatcher(state: $fold)
                .ignoresSafeArea()
        }
        .animation(.spring(duration: 0.4), value: dockedFold)
        .onChange(of: dockedFold) { _, _ in refitWindow() }
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
        .overlay(alignment: .topTrailing) {
            // 半開きで、下の画面に帯が出ているときは、メニューは、その帯にある
            if dockedFold == nil { menu() }
        }
        .overlay(alignment: .top) {
            if let notice = session.notice {
                Label(notice, systemImage: "info.circle.fill")
                    .font(.callout.weight(.medium))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
                    .glass(in: Capsule())
                    .padding(.top, 8 + safeInsets.top)
                    .padding(.horizontal, 64)
                    .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .overlay {
            if session.state == .reconnecting {
                VStack(spacing: 14) {
                    ProgressView()
                        .controlSize(.large)
                        .tint(.white)
                    Text("再接続中…")
                        .font(.headline)
                        .foregroundStyle(.white)
                    Button("キャンセル") { session.disconnect() }
                        .glassButton()
                }
                .padding(.horizontal, 32)
                .padding(.vertical, 24)
                .glass(in: RoundedRectangle(cornerRadius: 28, style: .continuous))
                .transition(.scale(scale: 0.9).combined(with: .opacity))
            }
        }
        .animation(.spring(duration: 0.35), value: session.notice)
        .animation(.spring(duration: 0.35), value: session.state)
        .sheet(isPresented: $showingWindows, onDismiss: { refocus += 1 }) {
            WindowPicker(fitSize: fitSize)
        }
        .sheet(isPresented: $showingMonitors, onDismiss: { refocus += 1 }) {
            MonitorPicker()
        }
        .sheet(isPresented: $showingSettings, onDismiss: { refocus += 1 }) {
            SettingsView(qualityChoice: qualityChoice, autoChoice: Self.autoChoice, info: settingsInfo)
        }
        .fileSendPickers(
            photosShown: $showingPhotoPicker,
            filesShown: $showingFileImporter,
            toClipboard: sendToPCClipboard
        )
        .onAppear {
            inputMode = GCKeyboard.coalesced != nil ? .hardware : .touch
        }
        .onChange(of: inputMode) { _, mode in
            // メニューで選んだキーボードを、右下のボタンで出すものにする
            if mode == .software || mode == .overlay { keyboardKind = mode }
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
        // 半開きのときは、折り目より上の大きさに合わせる
        let height = dockedFold.map { $0.minY } ?? fullSize.height
        return CGSize(width: max(fullSize.width - margin, 1), height: max(height - margin, 1))
    }

    /// 半開きで、下の画面を入力に使うときの、折り目の位置。そうでなければ nil
    private var halfOpenFold: CGRect? {
        guard dockWhenHalfOpen, fold.isTabletop, let rect = fold.fold, rect.minY > 0 else { return nil }
        return rect
    }

    /// 半開きで、下の画面にキーボードかトラックパッドを出しているときの、折り目の位置。
    /// 両方しまっていれば nil（映像を画面いっぱいに出す）
    private var dockedFold: CGRect? {
        guard dockShowKeys || dockShowPad else { return nil }
        return halfOpenFold
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

    /// ウィンドウの切り替えのボタン。一覧を開くボタンと、1つ前のウィンドウに戻るボタン（戻れるときだけ出る）。
    /// 切り替えは何度もするので、メニューの奥ではなく、すぐ押せる所に置く。
    /// compact: 半開きの下の画面の帯に置く、背の低い形
    @ViewBuilder
    private func windowsButton(compact: Bool) -> some View {
        let tint: Color = session.viewMode == .window || session.viewMode == .desktop ? .accentColor : .white
        HStack(spacing: UIMetrics.spacing) {
            if let previous = session.previousWindow, previous.id != session.currentWindow?.id {
                Button {
                    WindowPicker.open(previous, session: session, fitSize: fitSize)
                } label: {
                    Image(systemName: "arrow.triangle.swap")
                        .modifier(BarButtonLabel(tint: .white, compact: compact))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("前のウィンドウに切り替え: \(previous.title)")
                .transition(.scale.combined(with: .opacity))
            }
            Button {
                session.requestWindows()
                showingWindows = true
            } label: {
                Image(systemName: "macwindow.on.rectangle")
                    .modifier(BarButtonLabel(tint: tint, compact: compact))
            }
            .buttonStyle(.plain)
            .accessibilityLabel("ウィンドウを選択")
        }
        .animation(.spring(duration: 0.3), value: session.previousWindow?.id)
    }

    /// トラックパッドを出す・しまうボタン（重ねて出す形）
    private var trackpadButton: some View {
        Button {
            showTrackpad.toggle()
        } label: {
            Image(systemName: "rectangle.and.hand.point.up.left")
                .modifier(BarButtonLabel(tint: showTrackpad ? Color.accentColor : .white))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(showTrackpad ? "トラックパッドを隠す" : "トラックパッドを表示")
    }

    /// 右下のボタン。iPhoneのキーボードを出す・しまう。PCのIMEの状態も見せる
    private var inputButton: some View {
        Button {
            switch inputMode {
            case .software, .overlay: inputMode = .touch
            default: inputMode = keyboardKind == .software ? .software : .overlay
            }
        } label: {
            HStack(spacing: 8) {
                Image(systemName: inputMode == .software || inputMode == .overlay
                      ? "keyboard.chevron.compact.down" : inputMode.systemImage)
                    .contentTransition(.symbolEffect(.replace))
                if inputMode != .touch, let on = session.imeOn {
                    Text(on ? "あ" : "A")
                        .font(.callout.bold())
                        .frame(width: 22, height: 22)
                        .background(on ? Color.accentColor : Color.white.opacity(0.2), in: Circle())
                }
            }
            .modifier(BarButtonLabel(tint: .white))
        }
        .buttonStyle(.plain)
    }

    private static let autoChoice = "auto"

    /// 画質の選び方。自動か、高・中・低の固定か。選んだ画質は、自動のときの上限にもなる
    private var qualityChoice: Binding<String> {
        Binding(
            get: { autoQuality ? Self.autoChoice : quality.rawValue },
            set: { choice in
                let auto = choice == Self.autoChoice
                let changedAuto = auto != autoQuality
                autoQuality = auto
                if let q = VideoQuality(rawValue: choice), q != quality {
                    quality = q // onChange が、つなぎ直して反映する
                } else if changedAuto {
                    session.setQuality(quality, force: true)
                }
            }
        )
    }

    /// 設定の画面に出す、今の状態
    private var settingsInfo: [(label: String, value: String)] {
        var rows: [(label: String, value: String)] = []
        if let window = session.currentWindow {
            rows.append(("表示中", window.title))
        } else if session.viewMode == .desktop {
            rows.append(("表示中", "仮想画面"))
        } else if session.viewMode == .monitor, let monitor = session.currentMonitor {
            rows.append(("表示中", monitor.name))
        }
        if let active = session.activeHost {
            rows.append(("接続先", hostDescription(active)))
        }
        rows.append(("画面", screenDescription.replacingOccurrences(of: "画面: ", with: "")))
        rows.append(("映像", (session.codec == "hevc" ? "H.265" : "H.264")
                     + (session.autoLevel.map { "（自動: \($0.shortLabel)）" } ?? "")))
        return rows
    }

    /// 右上の「…」のメニュー。よく使うものだけを置く。設定は、専用の画面（SettingsView）に分けた。
    /// compact のときは、半開きの下の画面の帯に置く、小さな丸いボタンにする
    private func menu(compact: Bool = false) -> some View {
        Menu {
            Button("ウィンドウを選択", systemImage: "macwindow") {
                session.requestWindows()
                showingWindows = true
            }
            Button("モニターを選択", systemImage: "display.2") {
                session.requestMonitors()
                showingMonitors = true
            }
            if session.virtualAvailable {
                Button("仮想画面を表示", systemImage: "rectangle.on.rectangle") {
                    session.showDesktop(fit: fitSize)
                }
                Button("移動したウィンドウを元に戻す", systemImage: "arrow.uturn.backward") {
                    session.restoreWindows()
                }
            }
            if session.viewMode != .screen {
                Button("メイン画面を表示", systemImage: "display") { session.showScreen() }
            }
            Divider()
            Toggle("PC の音声を再生", systemImage: session.audioOn ? "speaker.wave.2.fill" : "speaker.slash", isOn: $audioOn)
            Menu("入力方法", systemImage: "keyboard") {
                Picker("入力方法", selection: $inputMode) {
                    ForEach(InputMode.allCases) { mode in
                        Label(mode.label, systemImage: mode.systemImage).tag(mode)
                    }
                }
                .pickerStyle(.inline)
            }
            Menu("ショートカット", systemImage: "command") {
                ShortcutMenuItems(session: session)
            }
            Menu("クリップボードとファイル", systemImage: "doc.on.clipboard") {
                Button("iPhone → PC", systemImage: "arrow.up.doc") { session.sendClipboard() }
                Button("PC → iPhone", systemImage: "arrow.down.doc") { session.requestClipboard() }
                Divider()
                Button("写真を PC に送信", systemImage: "photo") {
                    sendToPCClipboard = false
                    showingPhotoPicker = true
                }
                Button("写真を PC のクリップボードにコピー", systemImage: "photo.on.rectangle") {
                    sendToPCClipboard = true
                    showingPhotoPicker = true
                }
                Button("ファイルを PC に送信", systemImage: "doc") {
                    sendToPCClipboard = false
                    showingFileImporter = true
                }
                Button("ファイルを PC のクリップボードにコピー", systemImage: "doc.on.clipboard") {
                    sendToPCClipboard = true
                    showingFileImporter = true
                }
            }
            Divider()
            Button("設定", systemImage: "slider.horizontal.3") { showingSettings = true }
            Button("切断", systemImage: "xmark.circle", role: .destructive) { session.disconnect() }
        } label: {
            if compact {
                Image(systemName: "ellipsis").glassCircle(size: UIMetrics.compactButtonHeight)
            } else {
                Image(systemName: "ellipsis")
                    .glassCircle(size: UIMetrics.buttonHeight)
                    .padding(UIMetrics.edgePadding)
            }
        }
        .padding(.top, compact ? 0 : safeInsets.top)
        .padding(.trailing, compact ? 0 : safeInsets.trailing)
    }

    /// 接続の画面の背景。暗い色に、ぼかした光を2つ置く
    private var backdrop: some View {
        ZStack {
            Color(red: 0.03, green: 0.04, blue: 0.09)
            Circle()
                .fill(Color.blue.opacity(0.45))
                .frame(width: 420, height: 420)
                .blur(radius: 120)
                .offset(x: -140, y: -260)
            Circle()
                .fill(Color.purple.opacity(0.35))
                .frame(width: 380, height: 380)
                .blur(radius: 120)
                .offset(x: 160, y: 280)
        }
        .ignoresSafeArea()
    }

    private var connectForm: some View {
        ScrollView {
            VStack(spacing: 24) {
                header
                    .padding(.top, 40)

                card(title: "接続先", systemImage: "network") {
                    field(systemImage: "server.rack") {
                        TextField("自宅のホスト（例: 192.168.1.10）", text: $host)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .keyboardType(.URL)
                    }
                    Divider().overlay(Color.white.opacity(0.1))
                    field(systemImage: "globe") {
                        TextField("外出先のホスト（Tailscale、任意）", text: $hostRemote)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .keyboardType(.URL)
                    }
                    Divider().overlay(Color.white.opacity(0.1))
                    field(systemImage: "number") {
                        TextField("ポート", text: $port)
                            .keyboardType(.numberPad)
                    }
                    Divider().overlay(Color.white.opacity(0.1))
                    field(systemImage: "key.fill") {
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
                                .foregroundStyle(.secondary)
                        }
                        .buttonStyle(.borderless)
                        .accessibilityLabel(showToken ? "トークンを非表示" : "トークンを表示")
                    }
                }

                card(title: "画質", systemImage: "sparkles.tv") {
                    Picker("画質", selection: qualityChoice) {
                        Text("自動").tag(Self.autoChoice)
                        ForEach(VideoQuality.allCases) { q in
                            Text(q.shortLabel).tag(q.rawValue)
                        }
                    }
                    .pickerStyle(.segmented)
                    Text("モバイル通信では、画質を下げると遅延が少なくなります")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                if let message = session.errorMessage {
                    Label(message, systemImage: "exclamationmark.triangle.fill")
                        .font(.callout)
                        .foregroundStyle(.white)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(14)
                        .glass(in: RoundedRectangle(cornerRadius: 16, style: .continuous), tint: .red)
                        .transition(.opacity)
                }

                VStack(spacing: 10) {
                    Button {
                        // 貼り付けたときに付いてくる空白や、全角の英数字を直してから使う
                        host = Self.cleaned(host)
                        hostRemote = Self.cleaned(hostRemote)
                        port = Self.cleaned(port)
                        token = Self.cleaned(token)
                        Keychain.save("token", token)
                        session.setAudio(audioOn, mixWithOthers: audioMix)
                        session.connect(hosts: hostCandidates(), port: Int(port) ?? 8765, token: token, quality: quality)
                    } label: {
                        HStack(spacing: 10) {
                            if session.state == .connecting {
                                ProgressView().tint(.white)
                                Text("接続中…")
                            } else {
                                Image(systemName: "bolt.horizontal.fill")
                                Text("接続")
                            }
                        }
                        .font(.headline)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 6)
                    }
                    .glassButton(prominent: true)
                    .controlSize(.large)
                    .disabled((host.isEmpty && hostRemote.isEmpty) || token.isEmpty || session.state == .connecting)

                    if session.state == .connecting {
                        Button("キャンセル", role: .cancel) { session.disconnect() }
                            .glassButton()
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 32)
            .frame(maxWidth: 520)
            .frame(maxWidth: .infinity)
            .animation(.spring(duration: 0.35), value: session.errorMessage)
            .animation(.spring(duration: 0.35), value: session.state)
        }
        .scrollDismissesKeyboard(.interactively)
    }

    private var header: some View {
        VStack(spacing: 12) {
            Image(systemName: "display")
                .font(.system(size: 34, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 76, height: 76)
                .background(
                    LinearGradient(colors: [.blue, .indigo], startPoint: .topLeading, endPoint: .bottomTrailing),
                    in: RoundedRectangle(cornerRadius: 20, style: .continuous)
                )
                .shadow(color: .blue.opacity(0.5), radius: 20, y: 8)
            Text("DuoRemote")
                .font(.largeTitle.bold())
                .foregroundStyle(.white)
            Text("iPhone から PC をリモート操作")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }

    /// 見出しと、ガラスの板
    private func card<Content: View>(title: String, systemImage: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: systemImage)
                .font(.footnote.weight(.semibold))
                .foregroundStyle(.secondary)
                .padding(.leading, 4)
            VStack(alignment: .leading, spacing: 12) {
                content()
            }
            .padding(16)
            .glass(in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        }
    }

    /// 左にアイコンを置いた入力の欄
    private func field<Content: View>(systemImage: String, @ViewBuilder content: () -> Content) -> some View {
        HStack(spacing: 12) {
            Image(systemName: systemImage)
                .foregroundStyle(.secondary)
                .frame(width: 22)
            content()
        }
    }

    /// 全角の英数字と記号を半角にし、前後の空白と改行を取り除く
    static func cleaned(_ text: String) -> String {
        let halfWidth = text.applyingTransform(.fullwidthToHalfwidth, reverse: false) ?? text
        return halfWidth.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// 試すホストの並び。前回つながった方（家か外か）を先にする。空のものは除く
    private func hostCandidates() -> [String] {
        let all = [host, hostRemote]
        let first = lastHostIndex == 1 ? 1 : 0
        return [all[first], all[1 - first]].filter { !$0.isEmpty }
    }

    /// メニューに出す、今の接続先（「家（192.168.x.x）」など）
    private func hostDescription(_ active: String) -> String {
        if active == host { return "自宅（\(active)）" }
        if active == hostRemote { return "外出先（\(active)）" }
        return active
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
        LatencyBadgeContent(latency: session.latency)
    }
}

private struct LatencyBadgeContent: View {
    @ObservedObject var latency: LatencyStore

    var body: some View {
        if let ms = latency.ms {
            HStack(spacing: 5) {
                Circle()
                    .fill(LatencyColor.color(ms: ms))
                    .frame(width: 6, height: 6)
                Text("\(ms)ms")
                    .font(.caption2.weight(.semibold).monospacedDigit())
                    .contentTransition(.numericText())
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .foregroundStyle(.white.opacity(0.9))
            .glass(in: Capsule())
        }
    }
}

/// 右下の丸いボタン（ウィンドウ、トラックパッド、キーボード）の見た目をそろえる。高さ 44、最低の幅 52
struct BarButtonLabel: ViewModifier {
    var tint: Color
    /// 半開きの下の画面の帯に置く、背の低い形（高さ 34）
    var compact = false

    func body(content: Content) -> some View {
        content
            .font(.body.weight(.semibold))
            .padding(.horizontal, compact ? 14 : 16)
            .frame(minWidth: compact ? 0 : 52, minHeight: compact ? UIMetrics.compactButtonHeight : UIMetrics.buttonHeight)
            .foregroundStyle(tint)
            .glass(in: Capsule(), interactive: true)
            .contentShape(Capsule())
    }
}
