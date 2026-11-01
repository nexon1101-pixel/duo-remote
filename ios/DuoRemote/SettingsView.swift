import SwiftUI

/// 設定の画面。「…」のメニューにあった設定を、グループに分けて、ここに集める。
/// 値は、メニューや各画面と同じ @AppStorage のキーを使うので、ここで変えると、すぐ反映される
struct SettingsView: View {
    @Environment(\.dismiss) private var dismiss

    /// 画質（自動か、高・中・低）。変えたときの、つなぎ直しは、呼ぶ側の Binding が行う
    let qualityChoice: Binding<String>
    let autoChoice: String
    /// 今の状態（表示中のもの、接続先、画面、映像）。読むだけ
    let info: [(label: String, value: String)]
    /// 画面の縦横比（幅 ÷ 高さ）。仮想画面の大きさの選択肢に、おおよその解像度を出すために使う
    var virtualAspect: Double = 0

    // 入力
    @AppStorage("pointerMode") private var pointerMode = PointerMode.direct
    @AppStorage(KeyCaptureView.commandAsControlKey) private var commandAsControl = false
    @AppStorage(KeyCaptureView.zenkakuKey) private var zenkakuAsIME = false
    // キーボードとトラックパッド
    @AppStorage(OverlayKeyboard.layoutKey) private var overlayLayout = OverlayKeyboardLayout.us
    @AppStorage(OverlayKeyboard.atBottomKey) private var keyboardAtBottom = false
    @AppStorage(OverlayKeyboard.padAtBottomKey) private var padAtBottom = false
    @AppStorage("videoAboveDock") private var videoAboveDock = false
    @AppStorage("showFloatingTrackpad") private var showTrackpad = false
    @AppStorage(TrackpadCoordinator.pinchModeKey) private var trackpadPinch = "video"
    @AppStorage(OverlayKeyboard.opacityKey) private var overlayOpacity = 0.8
    @AppStorage(OverlayKeyboard.colorKeysKey) private var colorKeys = true
    @AppStorage(OverlayKeyboard.hapticsKey) private var keyHaptics = true
    @AppStorage("dockWhenHalfOpen") private var dockWhenHalfOpen = true
    @AppStorage("gameMode") private var gameMode = false
    @AppStorage("controllerPreset") private var controllerPreset = ControllerPreset.arrows
    @AppStorage(ControllerKind.storageKey) private var controllerKind = ControllerKind.simple
    // 表示
    @AppStorage(RemoteSession.useHEVCKey) private var useHEVC = true
    @AppStorage(RemoteSession.transportKey) private var transport = "tcp"
    @AppStorage("showLatency") private var showLatency = true
    @AppStorage("edgeMargin") private var edgeMargin = 0
    @AppStorage(RemoteSession.virtualScaleKey) private var virtualScale = 1.0
    // 音声
    @AppStorage("audioOn") private var audioOn = false
    @AppStorage("audioMix") private var audioMix = false
    @AppStorage("audioVolume") private var audioVolume = 1.0
    @AppStorage("muteLocal") private var muteLocal = false

    /// 選択肢が少ない設定を、1回で選べる、横並びの切り替えにする（見出しは、上に小さく出す）
    private func segmentedRow<T: Hashable, Content: View>(
        _ title: String, selection: Binding<T>, @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.footnote)
                .foregroundStyle(.secondary)
            Picker(title, selection: selection) {
                content()
            }
            .pickerStyle(.segmented)
            .labelsHidden()
        }
        .padding(.vertical, 2)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("入力") {
                    Picker("タッチ操作", selection: $pointerMode) {
                        ForEach(PointerMode.allCases) { mode in
                            Text(mode.label).tag(mode)
                        }
                    }
                    Toggle("Command キーを Ctrl として使う", isOn: $commandAsControl)
                    Toggle("半角/全角キーで IME を切り替える", isOn: $zenkakuAsIME)
                }

                Section {
                    Picker("キーボード配列", selection: $overlayLayout) {
                        ForEach(OverlayKeyboardLayout.allCases) { layout in
                            Text(layout.label).tag(layout)
                        }
                    }
                    segmentedRow("キーボードの位置", selection: $keyboardAtBottom) {
                        Text("フロート").tag(false)
                        Text("下部に固定").tag(true)
                    }
                    segmentedRow("トラックパッドの位置", selection: $padAtBottom) {
                        Text("フロート").tag(false)
                        Text("下部に固定").tag(true)
                    }
                    Toggle("固定した部分を避けて映像を表示", isOn: $videoAboveDock)
                    Toggle("トラックパッドを表示", isOn: $showTrackpad)
                    Picker("ピンチ操作", selection: $trackpadPinch) {
                        Text("映像を拡大・縮小").tag("video")
                        Text("PC 側で拡大・縮小（Ctrl+ホイール）").tag("pc")
                    }
                    segmentedRow("不透明度", selection: $overlayOpacity) {
                        Text("100%").tag(1.0)
                        Text("80%").tag(0.8)
                        Text("60%").tag(0.6)
                        Text("40%").tag(0.4)
                    }
                    Toggle("キーを色分け", isOn: $colorKeys)
                    Toggle("キーを押したときに振動", isOn: $keyHaptics)
                } header: {
                    Text("アプリ内キーボードとトラックパッド")
                } footer: {
                    Text("フロートは、映像の上で自由に動かせます。下部に固定すると、画面の下に並びます。「固定した部分を避けて映像を表示」をオンにすると、固定したキーボードやトラックパッドに映像が隠れません。")
                }

                Section {
                    Toggle("半開き時に下の画面へキーボードを表示", isOn: $dockWhenHalfOpen)
                } header: {
                    Text("Duo")
                }

                Section {
                    Toggle("ゲームモード", isOn: $gameMode)
                    Picker("コントローラの種類", selection: $controllerKind) {
                        ForEach(ControllerKind.allCases) { kind in
                            Text(kind.label).tag(kind)
                        }
                    }
                    Picker("コントローラのキー（キーボード入力）", selection: $controllerPreset) {
                        ForEach(ControllerPreset.allCases) { preset in
                            Text(preset.label).tag(preset)
                        }
                    }
                } header: {
                    Text("ゲーム")
                } footer: {
                    Text("上半分に映像、下半分にキーボード、コントローラ、トラックパッドを出します。キーボード入力は、キーボードのキーとして送ります。ゲームパッドは、PC の仮想のゲームパッド（Xbox 360）に送ります。PC に ViGEmBus が必要で、入っていないときはキーボード入力で動きます。")
                }

                Section {
                    Picker("画質", selection: qualityChoice) {
                        Text("自動").tag(autoChoice)
                        ForEach(VideoQuality.allCases) { q in
                            Text(q.label).tag(q.rawValue)
                        }
                    }
                    segmentedRow("通信方式（次回接続から）", selection: $transport) {
                        Text("TCP").tag("tcp")
                        Text("UDP").tag("udp")
                        Text("自動").tag("auto")
                    }
                    Toggle("H.265 を使用（次回接続から）", isOn: $useHEVC)
                    Toggle("遅延を表示", isOn: $showLatency)
                    if VirtualScale.enabled {
                        Picker("仮想画面の大きさ", selection: $virtualScale) {
                            ForEach(VirtualScale.all, id: \.self) { scale in
                                Text(VirtualScale.label(scale, aspect: virtualAspect)).tag(scale)
                            }
                        }
                    }
                    segmentedRow("画面の余白", selection: $edgeMargin) {
                        Text("なし").tag(0)
                        Text("小").tag(10)
                        Text("中").tag(20)
                        Text("大").tag(30)
                    }
                } header: {
                    Text("画質と表示")
                } footer: {
                    if VirtualScale.enabled {
                        Text("仮想画面の大きさは、縦横比はそのままで、解像度を大きくします。「×1」が標準（高さ 1080）で、×1.5 なら、高さ 1620 です。かっこの中は、いまの画面の縦横比のときの、おおよその大きさです（PC の仮想ディスプレイの設定ファイルを書き直したあと、使えます）。画質が「高」のとき、大きくした解像度のまま見られます。")
                    }
                }

                Section {
                    Toggle("PC の音声を再生", isOn: $audioOn)
                    Toggle("他のアプリの音声と同時に再生", isOn: $audioMix)
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text("音量")
                            Spacer()
                            Text("\(Int(audioVolume * 100))%").foregroundStyle(.secondary).monospacedDigit()
                        }
                        Slider(value: $audioVolume, in: 0...3, step: 0.05)
                        AudioMeterBar(width: 160)
                    }
                    Toggle("接続中、PC のスピーカーを消音", isOn: $muteLocal)
                } header: {
                    Text("音声")
                } footer: {
                    Text("音量は、この iPhone で鳴らす音の大きさです（0〜300%。100% を超えた分は、増幅します）。下の棒は、いま鳴っている音の大きさで、赤くなると、割れています。「PC のスピーカーを消音」をオンにすると、接続している間、PC のスピーカーから音が出なくなります（iPhone には届きます）。切断すると、元に戻ります。PC の音声を再生していなくても、PC のスピーカーは消音になります。")
                }

                if !info.isEmpty {
                    Section("接続の情報") {
                        ForEach(Array(info.enumerated()), id: \.offset) { _, row in
                            LabeledContent(row.label, value: row.value)
                        }
                    }
                }
            }
            .navigationTitle("設定")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完了") { dismiss() }
                }
            }
        }
        .presentationDetents([.fraction(0.7), .large])
        .presentationDragIndicator(.visible)
    }
}
