import DuoFlickKeyboardCore

/// 同梱キーボード（DuoFlick）の入り口。本体は DuoFlick リポジトリの
/// DuoFlickKeyboardCore パッケージにあり、ここは拡張ターゲット側の薄いサブクラス
/// （Info.plist の NSExtensionPrincipalClass が指す）。
final class DuoFlickKeyboardHost: KeyboardViewController {}
