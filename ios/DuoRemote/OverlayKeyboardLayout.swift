import CoreGraphics

/// アプリのキーボード（映像の上に重ねて出すキーボード）の、キーの並び。
/// HHKB に近い 60% の配列。矢印やファンクションキーは、Fn を押している間だけ出る。
/// キーは全部スキャンコードで送るので、どの文字になるかは、PCのキー配列で決まる。
/// アプリの表示は、PCの配列（US か JIS）に合わせて選ぶ。

/// PCのキー配列
enum OverlayKeyboardLayout: String, CaseIterable, Identifiable {
    case us
    case jis

    var id: String { rawValue }

    var label: String {
        switch self {
        case .us: "US"
        case .jis: "JIS"
        }
    }

    /// 上の段から順に並べたキー。どの段も、幅の合計は rowWidth にそろえる
    var rows: [[OverlayKey]] {
        switch self {
        case .us: OverlayKeyboardTables.us
        case .jis: OverlayKeyboardTables.jis
        }
    }

    /// 1段の幅（普通のキーを1とした数）
    static let rowWidth: CGFloat = 15
}

/// 押している間、ほかのキーと一緒に押したことになるキー
enum OverlayModifier: Hashable {
    case shift
    case ctrl
    case alt
    case win
    /// アプリの中だけのキー。押している間、キーが fn の方に替わる。PCには何も送らない
    case fn
}

/// キー1つ
struct OverlayKey {
    /// キーに出す文字
    let label: String
    /// Shift を押したときの文字（キーの上の方に小さく出す）。なければ nil
    var shiftLabel: String? = nil
    /// 送るスキャンコード（拡張キーは 0xE0xx の形）。Fn のときだけ nil
    var sc: Int?
    /// 幅（普通のキーを1とした数）
    var width: CGFloat = 1
    /// 修飾キーなら、その種類
    var modifier: OverlayModifier? = nil
    /// Fn を押している間に替わるキー。なければ、Fn を押していても、このキーのまま
    var fn: OverlayFnKey? = nil
    /// true のとき、IME のオン・オフを切り替えるキー（スキャンコードではなく、`ime` を送る）。
    /// PC のキー配列（US か JIS）に関係なく、半角/全角と同じ働きをする。今の状態（あ / A）を、キーに出す
    var ime = false
}

/// Fn を押している間のキー
struct OverlayFnKey {
    let label: String
    let sc: Int
}
