# duo-remote

iPhone Duo から自宅の Windows PC を操作するアプリ。計画は `docs/plan.md`、通信の取り決めは `docs/protocol.md`。

## 構成

- `server/` Windows側サーバー（Python 3.10以上）。画面をffmpeg（NVENC）でH.264にして、WebSocketで送る。入力はSendInputで受ける
- `ios/` iPhoneアプリ（SwiftUI）。XcodeGenで `project.yml` からプロジェクトを作る。Macはないので、ビルドはGitHub Actions（`.github/workflows/ios-build.yml`）で行い、ipaをSideStoreで入れる
- `docs/` 計画と通信の取り決め

## 決まりごと

- 通信の取り決めを変えるときは、`docs/protocol.md`、サーバー、アプリを同時に直す
- サーバーのトークン（`server/config.json`）や自宅のIPアドレスは、コミットしない
- 日本語の文書は、普通の言葉で書く（「効く」「強い」などの言い回しや、強調のカギ括弧、造語は使わない）
- Linuxのクラウド環境ではWindowsの画面取得とiOSのビルドは確認できない。`server/tests/` で確認できる範囲は確認し、残りは、実機での確認をお願いする
