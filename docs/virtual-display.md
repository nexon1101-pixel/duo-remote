# 仮想ディスプレイの入れ方と確認方法

iPhone 専用の作業用の画面として、仮想ディスプレイ（ソフトで作る、PCにつながっていないモニター）を使うための手順です。PCを操作する Claude に渡す指示を兼ねています。

使うドライバーは、[Virtual Display Driver](https://github.com/VirtualDrivers/Virtual-Display-Driver)（MIT ライセンス、Windows 標準の仕組み IddCx を使うもの）です。

## 守ること（PCを操作する Claude 向け）

- コードは書き換えない。GitHub に push しない
- ドライバーを入れる前に、ユーザーに「ドライバーを入れてよいか」を確認する。管理者の確認画面（UAC）は、あなたは操作できないので、ユーザーに押してもらう
- サーバーのトークンは、報告に書かない
- 途中で止まったら、先に進まず、出たメッセージをそのまま報告する
- 仮想ディスプレイは、いつでも元に戻せる（手順の最後の「元に戻すには」）

## 手順

### 1. サーバーを最新にして、状態を調べる

```bat
cd /d "%USERPROFILE%\duo-remote"
git pull
cd server
virtual.bat status
```

`virtual.bat` は、先に `start.bat` を一度実行して、準備（.venv）ができていないと使えない。`status` の出力を全部控える（管理者か、ドライバーが見つかるか、モニターの一覧、DXGI の番号）。

### 2. ドライバーを入れる

`winget install VirtualDrivers.Virtual-Display-Driver` で入るのは、**操作用のアプリ（VDD Control）だけ**で、ドライバー本体は入らない。ドライバー本体は、次のどちらかで入れる。

**入れる前に、ユーザーに次を伝えて、入れてよいかを確認する。**

- 入れるのは、Virtual Display Driver 25.7.23（MIT ライセンス、署名つき）
- 入れるときに、署名の証明書を、このPCの「信頼された発行元」に登録する
- グラフィックボードやチップセットのドライバーを大きく更新するときは、先にこのドライバーを消す。更新のあとに、画面が真っ黒になったときは、セーフ モードで起動して消す（公式の README より）
- 消し方は、この文書の最後の「元に戻すには」

**方法A（おすすめ）: 公式のスクリプトで入れる**

管理者の Windows PowerShell（`powershell.exe`。`pwsh` ではない）で、次を実行する。

```powershell
$url = "https://raw.githubusercontent.com/VirtualDrivers/Virtual-Display-Driver/master/Community%20Scripts/silent-install.ps1"
Invoke-WebRequest $url -OutFile "$env:TEMP\silent-install.ps1" -UseBasicParsing
Get-Content "$env:TEMP\silent-install.ps1"   # 中身を見て、ユーザーに「何をするか」を伝える
powershell -ExecutionPolicy Bypass -File "$env:TEMP\silent-install.ps1"
```

このスクリプトがすること（公式のもの）:
1. Nefcon 1.14.0（ドライバーを入れる道具）と、ドライバー本体（25.7.23）を、GitHub から取る
2. ドライバーの署名（`mttvdd.cat`）にある証明書を、「信頼された発行元」に登録する
3. `nefconw.exe install MttVDD.inf Root\MttVDD` でドライバーを入れる（10秒ほどかかる）

**方法B: VDD Control のボタンで入れる**

winget で入れた「VDD Control」を起動し、「Install」を押す（管理者の確認画面が出る）。

**入ったか確認する**

- デバイス マネージャーの「ディスプレイ アダプター」に、「Virtual Display Driver」が出る
- `virtual.bat status` の「ドライバー」に、`Virtual Display Driver` が出る（「見つかりません」でなくなる）
- 「設定」→「システム」→「ディスプレイ」に、モニターが増える（増えないときは、手順 3 の設定のあとに出る）

### 3. 設定ファイルを書く

高さ 1080 で、幅の違う解像度の一覧（縦長から横長まで）を、仮想ディスプレイの設定に書く。

1. 使っているグラフィックボードの名前を調べる。`virtual.bat status` の DXGI の欄の、アダプターの名前（例: `NVIDIA GeForce RTX 3070 Ti`）
2. 管理者のコマンドプロンプトで、次を実行する（名前は、1 で調べたもの）

   ```bat
   cd /d "%USERPROFILE%\duo-remote\server"
   virtual.bat write-config --gpu "NVIDIA GeForce RTX 3070 Ti" --apply
   ```

   - 元の設定は `vdd_settings.xml.bak` に控える
   - `--apply` で、ドライバーを無効にして有効に戻す（モニターが一度消えて、また出る）

### 4. 仮想モニターを確認する

```bat
virtual.bat status
virtual.bat modes
```

- `status` の「モニター」に、`【仮想】` の付いたモニターが出ること。その行の「アダプター」と「出力」の番号を控える
- `modes` に、`720x1080` から `2560x1080` までの解像度が出ること（出なければ、設定が反映されていない。ドライバーを無効にして有効に戻してから、もう一度）

### 5. 映像が撮れるか確認する

ffmpeg で、仮想モニターを撮れるかを確認する（4 で控えた「出力」の番号を、`output_idx` に入れる。ここでは 1 とする）。

```bat
ffmpeg -hide_banner -f lavfi -i "ddagrab=output_idx=1:framerate=30:draw_mouse=1" -t 3 -c:v h264_nvenc -f null -
```

- エラーなく終わり、`frame=` の数が増えていれば、撮れている
- 「output index」に関するエラーが出たら、仮想モニターが、ffmpeg が使うグラフィックアダプターとは別のものにつながっている。`status` の「アダプター」の番号が 0 でないときが、これに当たる。その出力を、そのまま報告する

### 6. 出し入れを確認して、普段は無効にしておく

ドライバーは、**使わないときは無効にしておく**（サーバーが、必要なときだけ有効にする）。

1. 管理者のコマンドプロンプトで、次を順に実行して、画面の動きを見る

   ```bat
   cd /d "%USERPROFILE%\duo-remote\server"
   virtual.bat off
   virtual.bat monitors
   virtual.bat on
   virtual.bat monitors
   virtual.bat off
   ```

   - `off` のあとは、`【仮想】` のモニターがなくなる。`on` のあとは、また出る
   - 最後に `off` で、無効に戻しておく
   - それぞれ、何秒かかったか（目安でよい）を控える
2. すでに動いているサーバーは、止める（Ctrl+C）
3. `server\start-admin.bat` をダブルクリックする（管理者の確認画面が出るので、ユーザーに「はい」を押してもらう）。「仮想ディスプレイ: 使えます」と出ること
4. 別のコマンドプロンプトで、サーバーを自己診断する

   ```bat
   cd /d "%USERPROFILE%\duo-remote\server"
   check.bat --virtual
   ```

   - 仮想ディスプレイを出して、そのモニターを映し、画面全体の表示に戻す。`[OK]` と `[NG]` を全部控える
   - 出す時間も出る（`出すまで ○秒`）
   - `[NG]` が出たときは、**サーバーのログ**（`server\duo_server.log`）の、最後の80行ほどを控える（メモ帳で開くか、`Get-Content server\duo_server.log -Tail 80`）。ffmpeg のエラーが、そこに出ている
   - サーバーは、撮り始めに失敗すると、3回までやり直す。それでも撮れないときは、接続を切らずに、本物の画面に戻して、理由を `[NG]` の行に出す
5. 終わったら、仮想モニターが消えていること（`virtual.bat monitors`）。サーバーを管理者で動かしたまま、iPhoneを切断すると、無効に戻る

## 報告の形

```
## 状態（virtual.bat status、手順 1）
（出力をそのまま）

## ドライバー（手順 2）
- 入れたか、途中で出たエラー

## 設定（手順 3、4）
- write-config の出力
- virtual.bat modes の出力

## 映像（手順 5）
- ffmpeg の結果（成功か、エラーの文）
- 仮想モニターのアダプターと出力の番号

## 出し入れ（手順 6）
- off / on で、モニターが消えて、出たか。それぞれ何秒かかったか
- check.bat --virtual の出力（そのまま）
- `[NG]` があったときは、server\duo_server.log の最後の80行ほど（そのまま）

## そのほか気づいたこと
```

## 元に戻すには

- 仮想モニターを消す（ドライバーは残す）: 管理者で `virtual.bat off`
- ドライバーを消す: 「設定」→「アプリ」→「インストールされているアプリ」から「Virtual Display Driver」を削除する。または、デバイス マネージャーの「ディスプレイ アダプター」の「Virtual Display Driver」を右クリックして「デバイスのアンインストール」
- 設定を元に戻す: `C:\VirtualDisplayDriver\vdd_settings.xml.bak` を、`vdd_settings.xml` に上書きする
