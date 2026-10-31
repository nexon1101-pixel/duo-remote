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

    // 入力
    @AppStorage("pointerMode") private var pointerMode = PointerMode.direct
    @AppStorage(KeyCaptureView.commandAsControlKey) private var commandAsControl = false
    @AppStorage(KeyCaptureView.zenkakuKey) private var zenkakuAsIME = false
    // キーボードとトラックパッド
    @AppStorage(OverlayKeyboard.layoutKey) private var overlayLayout = OverlayKeyboardLayout.us
    @AppStorage(OverlayKeyboard.atBottomKey) private var keyboardAtBottom = false
    @AppStorage(OverlayKeyboard.padAtBottomKey) private var padAtBottom = false
    @AppStorage("showFloatingTrackpad") private var showTrackpad = false
    @AppStorage(TrackpadCoordinator.pinchModeKey) private var trackpadPinch = "video"
    @AppStorage(OverlayKeyboard.opacityKey) private var overlayOpacity = 0.8
    @AppStorage(OverlayKeyboard.colorKeysKey) private var colorKeys = true
    @AppStorage(OverlayKeyboard.hapticsKey) private var keyHaptics = true
    @AppStorage("dockWhenHalfOpen") private var dockWhenHalfOpen = true
    // 表示
    @AppStorage(RemoteSession.useHEVCKey) private var useHEVC = true
    @AppStorage("showLatency") private var showLatency = true
    @AppStorage("edgeMargin") private var edgeMargin = 0
    // 音声
    @AppStorage("audioOn") private var audioOn = false
    @AppStorage("audioMix") private var audioMix = false

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
                    Picker("キーボードの位置", selection: $keyboardAtBottom) {
                        Text("フロート").tag(false)
                        Text("下部に固定").tag(true)
                    }
                    Picker("トラックパッドの位置", selection: $padAtBottom) {
                        Text("フロート").tag(false)
                        Text("下部に固定").tag(true)
                    }
                    Toggle("トラックパッドを表示", isOn: $showTrackpad)
                    Picker("ピンチ操作", selection: $trackpadPinch) {
                        Text("映像を拡大・縮小").tag("video")
                        Text("PC 側で拡大・縮小（Ctrl+ホイール）").tag("pc")
                    }
                    Picker("不透明度", selection: $overlayOpacity) {
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
                    Text("フロートは、映像の上で動かせます。下部に固定すると、画面の下に並びます。")
                }

                Section {
                    Toggle("半開き時に下の画面へキーボードを表示", isOn: $dockWhenHalfOpen)
                } header: {
                    Text("Duo")
                }

                Section("画質と表示") {
                    Picker("画質", selection: qualityChoice) {
                        Text("自動").tag(autoChoice)
                        ForEach(VideoQuality.allCases) { q in
                            Text(q.label).tag(q.rawValue)
                        }
                    }
                    Toggle("H.265 を使用（次回接続から）", isOn: $useHEVC)
                    Toggle("遅延を表示", isOn: $showLatency)
                    Picker("画面の余白", selection: $edgeMargin) {
                        Text("なし").tag(0)
                        Text("小（10）").tag(10)
                        Text("中（20）").tag(20)
                        Text("大（30）").tag(30)
                    }
                }

                Section {
                    Toggle("PC の音声を再生", isOn: $audioOn)
                    Toggle("他のアプリの音声と同時に再生", isOn: $audioMix)
                } header: {
                    Text("音声")
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
