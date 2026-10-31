import SwiftUI

/// よく使うキーの組み合わせ。仮想キーコード（vk）で、修飾キーを押したまま押して離す。
/// Win+L（ロック）は入れない。ロックすると、iPhoneからは解除できないため。
/// Alt+F4 は、よく使うので、押す前の確かめはしない。
struct Shortcut: Identifiable {
    let id: String
    let name: String
    let systemImage: String
    let vk: Int
    let modifiers: [Int]

    private init(_ id: String, _ name: String, _ systemImage: String, vk: Int, _ modifiers: [Int] = []) {
        self.id = id
        self.name = name
        self.systemImage = systemImage
        self.vk = vk
        self.modifiers = modifiers
    }

    // ウィンドウ・アプリ
    static let appSwitch = Shortcut("appSwitch", "アプリの切り替え", "rectangle.2.swap", vk: KeyMap.vkTab, [KeyMap.vkMenu])
    static let taskView = Shortcut("taskView", "タスク ビュー", "square.grid.2x2", vk: KeyMap.vkTab, [KeyMap.vkLWin])
    static let showDesktop = Shortcut("showDesktop", "デスクトップを表示", "display", vk: KeyMap.vkD, [KeyMap.vkLWin])
    static let start = Shortcut("start", "スタート", "house", vk: KeyMap.vkLWin)
    static let explorer = Shortcut("explorer", "エクスプローラー", "folder", vk: KeyMap.vkE, [KeyMap.vkLWin])
    static let maximize = Shortcut("maximize", "最大化", "arrow.up.left.and.arrow.down.right", vk: KeyMap.vkUp, [KeyMap.vkLWin])
    static let snapLeft = Shortcut("snapLeft", "左に寄せる", "rectangle.lefthalf.inset.filled", vk: KeyMap.vkLeft, [KeyMap.vkLWin])
    static let snapRight = Shortcut("snapRight", "右に寄せる", "rectangle.righthalf.inset.filled", vk: KeyMap.vkRight, [KeyMap.vkLWin])
    static let closeWindow = Shortcut("closeWindow", "ウィンドウを閉じる", "xmark.rectangle", vk: KeyMap.vkF4, [KeyMap.vkMenu])

    // 編集
    static let undo = Shortcut("undo", "元に戻す", "arrow.uturn.backward", vk: KeyMap.vkZ, [KeyMap.vkControl])
    static let redo = Shortcut("redo", "やり直す", "arrow.uturn.forward", vk: KeyMap.vkY, [KeyMap.vkControl])
    static let selectAll = Shortcut("selectAll", "すべて選択", "selection.pin.in.out", vk: KeyMap.vkA, [KeyMap.vkControl])
    static let copy = Shortcut("copy", "コピー", "doc.on.doc", vk: KeyMap.vkC, [KeyMap.vkControl])
    static let paste = Shortcut("paste", "貼り付け", "doc.on.clipboard", vk: KeyMap.vkV, [KeyMap.vkControl])
    static let cut = Shortcut("cut", "切り取り", "scissors", vk: KeyMap.vkX, [KeyMap.vkControl])
    static let save = Shortcut("save", "保存", "square.and.arrow.down", vk: KeyMap.vkS, [KeyMap.vkControl])
    static let find = Shortcut("find", "検索", "magnifyingglass", vk: KeyMap.vkF, [KeyMap.vkControl])
    static let refresh = Shortcut("refresh", "更新", "arrow.clockwise", vk: KeyMap.vkF5)

    // システム
    static let taskManager = Shortcut("taskManager", "タスク マネージャー", "gauge.with.dots.needle.50percent", vk: KeyMap.vkEscape, [KeyMap.vkControl, KeyMap.vkShift])
    static let snip = Shortcut("snip", "画面の切り取り", "camera.viewfinder", vk: KeyMap.vkS, [KeyMap.vkLWin, KeyMap.vkShift])

    /// 制御メッセージの送り先（TouchInput の send など）に、キーの操作を順に渡す。
    /// RemoteSession.tapKey と同じ順番
    @MainActor
    func perform(send: @MainActor ([String: Any]) -> Void) {
        for m in modifiers { send(["t": "key", "vk": m, "d": "down"]) }
        send(["t": "key", "vk": vk, "d": "down"])
        send(["t": "key", "vk": vk, "d": "up"])
        for m in modifiers.reversed() { send(["t": "key", "vk": m, "d": "up"]) }
    }
}

/// メニューに出すときのまとまり
struct ShortcutGroup: Identifiable {
    let title: String
    let items: [Shortcut]
    var id: String { title }

    /// よく使う順
    static let all: [ShortcutGroup] = [
        ShortcutGroup(title: "ウィンドウ・アプリ", items: [
            .appSwitch, .taskView, .showDesktop, .start, .explorer,
            .maximize, .snapLeft, .snapRight, .closeWindow,
        ]),
        ShortcutGroup(title: "編集", items: [
            .undo, .redo, .selectAll, .copy, .paste, .cut, .save, .find, .refresh,
        ]),
        ShortcutGroup(title: "システム", items: [
            .taskManager, .snip,
        ]),
    ]
}

/// 接続中のメニューの「ショートカット」の中身。区切り線で、まとまりごとに分ける
struct ShortcutMenuItems: View {
    let session: RemoteSession

    var body: some View {
        ForEach(ShortcutGroup.all) { group in
            Section(group.title) {
                ForEach(group.items) { item in
                    Button(item.name, systemImage: item.systemImage) {
                        session.tapKey(vk: item.vk, modifiers: item.modifiers)
                    }
                }
            }
        }
    }
}
