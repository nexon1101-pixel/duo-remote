# PCでの動作確認の手順（PCを操作する Claude 向け）

この文書は、Windows PC を操作する Claude に渡すための指示です。PCの準備とサーバーの確認を行い、結果を報告してもらいます。iPhone での確認は、人が行います。

## あなたにお願いしたいこと

iPhone から自宅の Windows PC を操作するアプリ「duo-remote」の、PC側のサーバーを動かして、正しく動くかを確認してください。リポジトリは https://github.com/nexon1101-pixel/duo-remote です。

## 守ること

- コードは書き換えない。GitHub に push しない
- サーバーのトークン（`server/config.json` の `token`、サーバーの起動時に表示される値）は、報告に書かない。画面に表示されたことだけ伝える
- ルーターの設定は変えない。Windows のファイアウォールを丸ごと無効にしない
- 管理者として実行するのは、手順で求められたときか、ユーザーに確認してからにする
- 途中で止まったら、先に進まず、出たメッセージをそのまま報告する

## 手順

### 1. PCの状態を調べる

PowerShell で次を実行し、結果を控える。

```powershell
[System.Environment]::OSVersion.VersionString
Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion
Get-Command git, python, py, ffmpeg -ErrorAction SilentlyContinue | Select-Object Name, Source
```

### 2. リポジトリを取る

- `%USERPROFILE%\duo-remote` がすでにあれば、そのフォルダで `git pull` を実行する
- なければ、git があるとき: `git clone https://github.com/nexon1101-pixel/duo-remote.git "$env:USERPROFILE\duo-remote"`
- git がないとき: https://github.com/nexon1101-pixel/duo-remote/archive/refs/heads/main.zip を取り、`%USERPROFILE%\duo-remote` に展開する（フォルダの中に `server` フォルダがある形にする）

### 3. サーバーを起動する

1. `%USERPROFILE%\duo-remote\server\start.bat` をダブルクリックする
2. 「Python 3.10 以上 が見つかりません。winget で入れますか？」や「ffmpeg が見つかりません。winget で入れますか？」と聞かれたら、`Y` と答える。入れたあとに「見つけられませんでした」と出たら、その画面を閉じて、もう一度 `start.bat` を起動する
3. Windows のファイアウォールの確認が出たら、「プライベート ネットワーク」にチェックを入れて許可する
4. 次の行が出るまで待つ（初回は数分かかる）
   ```
   duo_server 0.6.0 を起動しました: 0.0.0.0:8765（エンコーダー: h264_nvenc）
   ```
   - エンコーダーが `libx264` になっていたら、そのことを報告する（NVIDIA のグラフィックボードの機能が使えていない）
5. この画面は、最後まで閉じない

### 4. サーバーを自己診断する

1. `%USERPROFILE%\duo-remote\server\check.bat` を、コマンドプロンプトから `--window` を付けて実行する
   ```bat
   cd /d "%USERPROFILE%\duo-remote\server"
   check.bat --window
   ```
   - 一覧の先頭のウィンドウが前に出て、少しのあいだ、そのウィンドウだけを撮る。マウスとキーボードは動かさない
2. 出力を全部控える。`[OK]` と `[NG]` の行が大事
3. `[NG]` があったら、サーバーの画面（`start.bat` の画面）の最後の30行ほども控える

### 5. ネットワークを調べる

PowerShell で次を実行する。

```powershell
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | Select-Object InterfaceAlias, IPAddress
Get-NetConnectionProfile | Select-Object InterfaceAlias, Name, NetworkCategory
Get-NetFirewallRule -Direction Inbound -Enabled True | Where-Object { $_.DisplayName -match 'python' } | Select-Object DisplayName, Profile, Action
```

- iPhone から接続するときに使う IP アドレスは、Wi-Fi か有線（Ethernet）の `192.168.x.x` などのアドレス
- `NetworkCategory` が `Public` だと、iPhone からつながらないことがある。その場合は、変えずにユーザーに伝える（「設定」→「ネットワークとインターネット」→ 使っているネットワーク →「ネットワーク プロファイルの種類」を「プライベート」にするかどうかを、ユーザーに決めてもらう）

### 6. スリープの設定を調べる

```powershell
powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE
```

「現在の AC 電源設定のインデックス」が 0 以外なら、その秒数でスリープする。報告に書く（変えない）。

## 報告の形

次の形で報告してください。

```
## PCの状態
- Windows: （1 の結果）
- グラフィックボード: （名前とドライバーの版）
- git / Python / ffmpeg: （あったか、なかったか。winget で入れたか）

## サーバーの起動
- 起動できたか: はい / いいえ
- エンコーダー: h264_nvenc / libx264
- 途中で出たエラー: （あれば、そのまま貼る）

## 自己診断（check.bat --window）
（出力をそのまま貼る。トークンは出ないので、そのままでよい）

## ネットワーク
- iPhone から使う IP アドレス: （Wi-Fi か有線のアドレス）
- ネットワーク プロファイル: Private / Public
- ファイアウォールの許可: （5 の3つ目の結果）
- スリープまでの時間: （6 の結果）

## そのほか気づいたこと
```

## このあと人が行うこと（参考）

1. iPhone の Safari で https://github.com/nexon1101-pixel/duo-remote/releases/tag/latest を開き、`DuoRemote.ipa` を SideStore で入れる
2. アプリに、ホスト（報告の IP アドレス）、ポート（8765）、トークン（サーバーの画面に出たもの）を入れて接続する
3. `docs/tasks.md` の「D. 実機で確認すること」を順に試す
