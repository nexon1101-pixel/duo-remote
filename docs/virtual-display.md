# 仮想ディスプレイの入れ方と確かめ方

iPhone 専用の作業用の画面として、仮想ディスプレイ（ソフトで作る、PCにつながっていないモニター）を使うための手順です。PCを操作する Claude に渡す指示を兼ねています。

使うドライバーは、[Virtual Display Driver](https://github.com/VirtualDrivers/Virtual-Display-Driver)（MIT ライセンス、Windows 標準の仕組み IddCx を使うもの）です。

## 守ること（PCを操作する Claude 向け）

- コードは書き換えない。GitHub に push しない
- ドライバーを入れる前に、ユーザーに「ドライバーを入れてよいか」を確かめる。管理者の確認画面（UAC）は、あなたは操作できないので、ユーザーに押してもらう
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

管理者のコマンドプロンプトか PowerShell で、次を実行する。

```powershell
winget install --id=VirtualDrivers.Virtual-Display-Driver -e
```

- 「vcruntime140.dll が見つからない」と出たら、Microsoft Visual C++ 再頒布可能パッケージ（最新）を入れる
- 入ったか確かめる: デバイス マネージャーの「ディスプレイ アダプター」に「Virtual Display Driver」が出る
- 入れ終わると、仮想モニターが1枚出る（画面が一瞬ちらつく）。「設定」→「システム」→「ディスプレイ」にモニターが増えていることを確かめる

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

### 4. 仮想モニターを確かめる

```bat
virtual.bat status
virtual.bat modes
```

- `status` の「モニター」に、`【仮想】` の付いたモニターが出ること。その行の「アダプター」と「出力」の番号を控える
- `modes` に、`720x1080` から `2560x1080` までの解像度が出ること（出なければ、設定が反映されていない。ドライバーを無効にして有効に戻してから、もう一度）

### 5. 映像が撮れるか確かめる

ffmpeg で、仮想モニターを撮れるかを確かめる（4 で控えた「出力」の番号を、`output_idx` に入れる。ここでは 1 とする）。

```bat
ffmpeg -hide_banner -f lavfi -i "ddagrab=output_idx=1:framerate=30:draw_mouse=1" -t 3 -c:v h264_nvenc -f null -
```

- エラーなく終わり、`frame=` の数が増えていれば、撮れている
- 「output index」に関するエラーが出たら、仮想モニターが、ffmpeg が使うグラフィックアダプターとは別のものにつながっている。`status` の「アダプター」の番号が 0 でないときが、これに当たる。その出力を、そのまま報告する

### 6. 管理者でサーバーを起動して、出し入れを確かめる

1. すでに動いているサーバーは、止める（Ctrl+C）
2. `server\start-admin.bat` をダブルクリックする（管理者の確認画面が出るので、ユーザーに「はい」を押してもらう）
3. 別の管理者のコマンドプロンプトで、次を順に実行して、画面の動きを見る

   ```bat
   cd /d "%USERPROFILE%\duo-remote\server"
   virtual.bat off
   virtual.bat monitors
   virtual.bat on
   virtual.bat monitors
   ```

   - `off` のあとは、`【仮想】` のモニターがなくなる。`on` のあとは、また出る
   - それぞれ、何秒かかったか（目安でよい）を控える

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
- off / on で、モニターが消えて、出たか
- それぞれ何秒かかったか

## そのほか気づいたこと
```

## 元に戻すには

- 仮想モニターを消す（ドライバーは残す）: 管理者で `virtual.bat off`
- ドライバーを消す: 「設定」→「アプリ」→「インストールされているアプリ」から「Virtual Display Driver」を削除する。または、デバイス マネージャーの「ディスプレイ アダプター」の「Virtual Display Driver」を右クリックして「デバイスのアンインストール」
- 設定を元に戻す: `C:\VirtualDisplayDriver\vdd_settings.xml.bak` を、`vdd_settings.xml` に上書きする
