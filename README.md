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

## 現在できること（0.4）

- 画面全体の映像を、iPhoneに表示する
- ウィンドウを1つ選んで、それだけを表示・操作する（右上のメニューの「ウィンドウを選ぶ」）
  - 「この画面に大きさを合わせる」をオンにすると、ウィンドウの大きさを、Duoの画面と同じ縦横比に変える。文字は、PCで見るのとほぼ同じ大きさになる
  - 選んだウィンドウは前に出す。他のウィンドウが上に重なると、それも映る
  - ウィンドウを動かすと、表示もついていく。閉じると画面全体に戻る
- 映像を触って、マウスを操作する

  | iPhoneでの操作 | PCでの動き |
  |---|---|
  | タップ | 左クリック（続けて2回でダブルクリック） |
  | 長押し | 右クリック |
  | 1本指で動かす | 左ボタンを押したまま動かす（範囲の選択、ウィンドウの移動など） |
  | 2本指で動かす | スクロール（iPhoneと同じ向き） |

- 文字を入力する。入力の方式は、右上のメニューの「入力」で選ぶ

  | 方式 | 内容 |
  |---|---|
  | タッチだけ | マウスの操作だけ |
  | iPhoneのキーボード | iPhone側で変換まで済ませ、確定した文字を送る。キーボードの上に、Esc、Tab、矢印、Ctrl、IMEの切り替えなどのキーが並ぶ。右下のボタンでも、出す・しまうができる |
  | Bluetoothキーボード | キーをそのままPCに送る。日本語の変換はPCのIMEで行う。「英数」「かな」キーは、IMEのオフ・オン |

  - Bluetoothキーボードをつなぐと、自動でBluetoothキーボードの方式になる。外すと、タッチだけに戻る
  - Ctrl を押してから英字か数字を打つと、Ctrl と一緒に押したことになる（Ctrl+C、Ctrl+V など）
  - PCのIMEの状態（あ / A）を、右下に出す
- 接続が切れたときは、押したままのボタンやキーを、サーバーが離す
- ピンチでの拡大は、まだありません
- 主モニターだけに対応しています

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

Macがないため、GitHub Actions でビルドします。iPhone Duo の内側画面いっぱいに表示するため、`xcode-27` のランナーで、入っている中でいちばん新しい Xcode（iOS 27.1 以降の SDK）を使います。それより前の SDK でビルドすると、内側画面では iPhone 用の枠（左右が黒）で表示されます。使った SDK は、ビルドの「使ったSDKを確かめる」に出ます。

1. GitHubの「Actions」から「iOS build」を実行する（`ios/` を変更してpushしても動く）
2. 終わったら、成果物の `DuoRemote-ipa` を取り出す
3. SideStoreで入れる

無料のApple IDでは、同時に入れられるアプリが3つまでです。
