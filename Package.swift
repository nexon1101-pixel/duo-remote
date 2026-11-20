// swift-tools-version: 5.9
import PackageDescription

// iOS のリモートクライアント本体（ios/DuoRemote）をライブラリとして切り出したパッケージ。
// アプリ本体は採用箱（duo-box リポジトリ）が持ち、このパッケージを取り込んで使う。
// PC サーバー（server/）とヘッドレスクライアント（client/）はこのパッケージとは無関係。
let package = Package(
    name: "DuoRemoteCore",
    platforms: [.iOS(.v17)],
    products: [
        .library(name: "DuoRemoteCore", targets: ["DuoRemoteCore"])
    ],
    targets: [
        .target(
            name: "DuoRemoteCore",
            path: "ios/DuoRemote",
            exclude: ["Assets.xcassets"]
        )
    ]
)
