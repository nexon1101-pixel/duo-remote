import CoreHaptics
import Foundation
import GameController
import UIKit

/// iPhone につないだ本物のコントローラ（GameController フレームワーク）の状態を、PC の仮想のゲームパッドに送る。
/// 「コントローラの種類」がガチで、PC に仮想のゲームパッドが使えるときだけ送る（つないだだけでは送らない）。
/// ゲームからの振動（rumble）も、ここで受けて、つないだコントローラを振動させる
@MainActor
final class PhysicalController: ObservableObject {
    static let shared = PhysicalController()

    /// 使えるコントローラ（extendedGamepad があるもの）がつながっている
    @Published private(set) var isConnected = false

    private weak var session: RemoteSession?
    /// ガチで、PC に送ってよい
    private var inUse = false
    private var current: GCController?

    // 振動
    private var engine: CHHapticEngine?
    private var rumblePlayer: CHHapticAdvancedPatternPlayer?
    private let fallbackHaptic = UIImpactFeedbackGenerator(style: .medium)
    private var lastFallbackAt: CFTimeInterval = 0

    private init() {
        let center = NotificationCenter.default
        center.addObserver(forName: .GCControllerDidConnect, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
        center.addObserver(forName: .GCControllerDidDisconnect, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
        center.addObserver(forName: .duoRumble, object: nil, queue: .main) { [weak self] note in
            let l = (note.userInfo?["l"] as? Double) ?? 0
            let r = (note.userInfo?["r"] as? Double) ?? 0
            Task { @MainActor in self?.rumble(l: l, r: r) }
        }
        refresh()
    }

    /// 画面（ContentView）から呼ぶ。session と、ガチで送ってよいか（種類がガチで、PC が対応していて、つながっている）を伝える
    func configure(session: RemoteSession, inUse: Bool) {
        self.session = session
        guard inUse != self.inUse else { return }
        self.inUse = inUse
        if !inUse { release() }
    }

    // MARK: つながっているコントローラ

    private func refresh() {
        let pad = GCController.controllers().first { $0.extendedGamepad != nil }
        isConnected = pad != nil
        guard pad !== current else { return }
        // 前のコントローラが外れた。押したままにならないように、全部 0 にする
        if let old = current {
            old.extendedGamepad?.valueChangedHandler = nil
            release()
        }
        current = pad
        pad?.extendedGamepad?.valueChangedHandler = { [weak self] gamepad, _ in
            Task { @MainActor in self?.push(gamepad) }
        }
    }

    /// コントローラの状態を読んで、PC に送る。取れない項目は 0
    private func push(_ gamepad: GCExtendedGamepad) {
        guard inUse, let session else { return }
        var buttons = 0
        func add(_ bit: Int, _ pressed: Bool?) {
            if pressed == true { buttons |= bit }
        }
        add(0x0001, gamepad.dpad.up.isPressed)
        add(0x0002, gamepad.dpad.down.isPressed)
        add(0x0004, gamepad.dpad.left.isPressed)
        add(0x0008, gamepad.dpad.right.isPressed)
        add(0x0010, gamepad.buttonMenu.isPressed)
        add(0x0020, gamepad.buttonOptions?.isPressed)
        add(0x0040, gamepad.leftThumbstickButton?.isPressed)
        add(0x0080, gamepad.rightThumbstickButton?.isPressed)
        add(0x0100, gamepad.leftShoulder.isPressed)
        add(0x0200, gamepad.rightShoulder.isPressed)
        add(0x0400, gamepad.buttonHome?.isPressed)
        add(0x1000, gamepad.buttonA.isPressed)
        add(0x2000, gamepad.buttonB.isPressed)
        add(0x4000, gamepad.buttonX.isPressed)
        add(0x8000, gamepad.buttonY.isPressed)
        // yAxis は、上が正（プロトコルと同じ）
        session.sendPad(
            buttons: buttons,
            lx: Double(gamepad.leftThumbstick.xAxis.value), ly: Double(gamepad.leftThumbstick.yAxis.value),
            rx: Double(gamepad.rightThumbstick.xAxis.value), ry: Double(gamepad.rightThumbstick.yAxis.value),
            lt: Double(gamepad.leftTrigger.value), rt: Double(gamepad.rightTrigger.value)
        )
    }

    /// 全部 0 を送って、仮想のゲームパッドを取り外す。振動も止める
    private func release() {
        stopRumble()
        session?.sendPad(buttons: 0, lx: 0, ly: 0, rx: 0, ry: 0, lt: 0, rt: 0)
        session?.sendPadOff()
    }

    // MARK: 振動

    /// l は左の大きいモーター、r は右の小さいモーター（0.0〜1.0）。両方 0 なら止める
    private func rumble(l: Double, r: Double) {
        guard inUse else { return }
        let intensity = Float(min(1, max(l, r, 0)))
        if intensity <= 0 {
            stopRumble()
            return
        }
        // 大きいモーターが強いほど低く重く、小さいモーターが強いほど細かく鋭くする
        let sharpness = Float(min(1, max(0, r / max(l + r, 0.001))))
        if let player = rumblePlayer {
            do {
                try player.sendParameters(
                    [
                        CHHapticDynamicParameter(parameterID: .hapticIntensityControl, value: intensity, relativeTime: 0),
                        CHHapticDynamicParameter(parameterID: .hapticSharpnessControl, value: sharpness, relativeTime: 0),
                    ],
                    atTime: CHHapticTimeImmediate
                )
                return
            } catch {
                stopRumble()
            }
        }
        if startControllerRumble(intensity: intensity, sharpness: sharpness) { return }
        // コントローラが振動できないときは、iPhone を軽く振動させる（続けて鳴らしすぎない）
        let now = CACurrentMediaTime()
        guard now - lastFallbackAt >= 0.15 else { return }
        lastFallbackAt = now
        fallbackHaptic.impactOccurred(intensity: CGFloat(intensity))
    }

    /// つないだコントローラの振動を始める。できなければ false
    private func startControllerRumble(intensity: Float, sharpness: Float) -> Bool {
        guard let haptics = current?.haptics else { return false }
        do {
            let engine: CHHapticEngine
            if let existing = self.engine {
                engine = existing
            } else {
                guard let created = haptics.createEngine(withLocality: .default) else { return false }
                created.stoppedHandler = { [weak self] _ in
                    Task { @MainActor in
                        self?.rumblePlayer = nil
                        self?.engine = nil
                    }
                }
                self.engine = created
                engine = created
            }
            try engine.start()
            // 強さは、あとから intensity の制御で変える。長さは、止める合図が来なくても終わるように 30 秒
            let event = CHHapticEvent(
                eventType: .hapticContinuous,
                parameters: [
                    CHHapticEventParameter(parameterID: .hapticIntensity, value: 1),
                    CHHapticEventParameter(parameterID: .hapticSharpness, value: sharpness),
                ],
                relativeTime: 0,
                duration: 30
            )
            let pattern = try CHHapticPattern(events: [event], parameters: [])
            let player = try engine.makeAdvancedPlayer(with: pattern)
            try player.start(atTime: CHHapticTimeImmediate)
            try player.sendParameters(
                [CHHapticDynamicParameter(parameterID: .hapticIntensityControl, value: intensity, relativeTime: 0)],
                atTime: CHHapticTimeImmediate
            )
            rumblePlayer = player
            return true
        } catch {
            rumblePlayer = nil
            engine = nil
            return false
        }
    }

    private func stopRumble() {
        try? rumblePlayer?.stop(atTime: CHHapticTimeImmediate)
        rumblePlayer = nil
        engine?.stop(completionHandler: nil)
        engine = nil
    }
}
