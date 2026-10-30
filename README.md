# duo-remote

iPhone Duo から、自宅の Windows PC を遠隔操作するアプリです。

- 計画: [docs/plan.md](docs/plan.md)
- 通信の取り決め: [docs/protocol.md](docs/protocol.md)

## 構成

| フォルダ | 内容 |
|---|---|
| `server/` | Windows側のサーバー（Python）。画面をH.264にして送り、入力を受け付ける |
| `ios/` | iPhoneアプリ（SwiftUI）。映像を表示する |
| `docs/` | 計画と通信の取り決め |

## 現在できること（0.2）

- 画面全体の映像を、iPhoneに表示する
- 映像を触って、マウスを操作する

  | iPhoneでの操作 | PCでの動き |
  |---|---|
  | タップ | 左クリック（続けて2回でダブルクリック） |
  | 長押し | 右クリック |
  | 1本指で動かす | 左ボタンを押したまま動かす（範囲の選択、ウィンドウの移動など） |
  | 2本指で動かす | スクロール（iPhoneと同じ向き） |

- キー、文字、IMEの受け付けはサーバー側にあります。iPhoneアプリからの入力は、まだ付けていません
- ピンチでの拡大、ウィンドウ単位の表示は、まだありません

## サーバーを動かす（Windows）

必要なもの:

- Python 3.10以上
- ffmpeg（NVENCを使うため、`full` 版のビルド。例: `winget install Gyan.FFmpeg`）。PATHに通すか、`--ffmpeg` でパスを指定する

手順:

```powershell
cd server
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m duo_server
```

初回の起動で `server/config.json` が作られ、接続用のトークンが表示されます。トークンはアプリに入力します。`config.json` はコミットしないでください（`.gitignore` に入れてあります）。

主なオプション:

| オプション | 意味 |
|---|---|
| `--port` | ポート（初期値 8765） |
| `--fps` | フレームレート（初期値 60） |
| `--bitrate` | ビットレート（kbps、初期値 8000） |
| `--max-height` | 映像の高さの上限。0で元の大きさ。通信が細いときは 1080 や 720 にする |
| `--encoder` | `auto`（初期値）、`h264_nvenc`、`libx264` |
| `--show-token` | トークンを表示する |

Windowsのファイアウォールで、ポート8765を許可する必要があります。外出先からつなぐ場合は、PCとiPhoneの両方にTailscaleを入れ、Tailscaleのアドレスで接続します。ルーターのポートは開けないでください。

## サーバーのテスト

```bash
cd server
pip install -r requirements-dev.txt
python -m pytest
```

Windows以外でも動きます。画面の取得（ddagrab）と入力（SendInput）は、Windowsでしか動かないため、テストには含まれません。

## iPhoneアプリを作る

Macがないため、GitHub Actions でビルドします。

1. GitHubの「Actions」から「iOS build」を実行する（`ios/` を変更してpushしても動く）
2. 終わったら、成果物の `DuoRemote-ipa` を取り出す
3. SideStoreで入れる

無料のApple IDでは、同時に入れられるアプリが3つまでです。
