# PCを操作する Claude への依頼

この文書は、Windows PC を操作する Claude に読ませるためのものです。ユーザーは、次のように伝えるだけです。

> duo-remote のリポジトリで git pull して、docs/pc-requests.md を読んで、依頼A をやってください。

リポジトリは `%USERPROFILE%\duo-remote` にあります（なければ `git clone https://github.com/nexon1101-pixel/duo-remote.git`）。

## 共通の決まり

- コードは書き換えない。GitHub に push しない
- サーバーのトークンは、報告に書かない
- 管理者の確認画面（UAC）は、あなたは操作できない。出たら、ユーザーに「許可を押してください」と伝えて、待つ
- サーバーは、Ctrl+C で止める。強制的に止めない（仮想モニターが有効のまま残る）
- 途中で止まったら、先に進まず、出たメッセージをそのまま報告する

---

## 依頼A: 切断のあとの後片付けの確認（仮想ディスプレイ）

目的: iPhone やテストが接続を閉じたあと、仮想ディスプレイが、すぐ（数秒で）無効に戻るかを確かめる。

1. 動いているサーバーがあれば、Ctrl+C で止める
2. 最新にする

   ```bat
   cd /d "%USERPROFILE%\duo-remote"
   git pull
   ```

   （ブランチ指定が必要と出たら: `git pull origin claude/keen-wozniak-41r0ni`）
3. `server\start-admin.bat` を実行する（管理者の確認画面が出るので、ユーザーに押してもらう）。「仮想ディスプレイ: 使えます」が出ること
4. 別のコマンドプロンプトで、次を実行する

   ```bat
   cd /d "%USERPROFILE%\duo-remote\server"
   check.bat --virtual
   ```

   出力を全部控える（`[OK]`、`[NG]`、「出すまで ○秒」）
5. **サーバーは止めずに**、check が終わった時刻を覚えておき、次を順に実行する

   ```bat
   virtual.bat monitors
   ```

   - 終わってから **5秒後**、**10秒後**、**20秒後** の3回。それぞれ、`【仮想】` のモニターが、あるか、ないかを控える
   - なくなるまでに、何秒かかったか（目安でよい）を控える
6. サーバーのログを控える

   ```powershell
   Get-Content "$env:USERPROFILE\duo-remote\server\duo_server.log" -Tail 40
   ```

7. サーバーの画面で、Ctrl+C を押して止める。止めたあとも、`virtual.bat monitors` で、`【仮想】` がないことを確かめる

### 報告の形

```
## check.bat --virtual の出力
（そのまま）

## 切断のあと、仮想モニターは消えたか
- 5秒後: ある / ない
- 10秒後: ある / ない
- 20秒後: ある / ない

## duo_server.log の最後の40行
（そのまま。トークンは伏せる）

## サーバーを止めたあと
- 仮想モニター: ある / ない

## そのほか気づいたこと
```

期待する結果: 5〜10秒後には「ない」。ログに「切断しました」の行がある。
