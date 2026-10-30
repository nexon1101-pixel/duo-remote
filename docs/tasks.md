# やることの一覧（2026-10-30 の調査）

リポジトリ全体（サーバー、アプリ、文書、CI）を読んで見つけた課題と、その直し方をまとめたもの。
対応するときは、この文書の番号を書いて、済んだら「済」に変える。

A〜C の 1〜23 は、2026-10-30 に対応した（各項目の「済」を参照）。実機での確認（D）は、まだ。

## 調べた結果の要点

- GitHub の Issue と、開いている PR は、どちらも 0 件
- iOS のビルド（GitHub Actions）は 8 回とも成功。最後のビルドは Xcode 27.2 RC、SDK は iphoneos27.2 で、Duo の内側画面の条件（27.1 以降）を満たしている
- サーバーのテストは 27 件すべて通る（Linux で `python -m pytest`）
- Windows と iPhone の実機では、まだ一度も動かしていない。コードを読んだ範囲では大きな作りの間違いはなく、下の 1〜8 を直してから実機で確かめるのがよい

## 直し方の決まり

- 通信の取り決めを変えるものは、`docs/protocol.md`、サーバー、アプリを同時に直す（9、10、22）
- サーバーの変更は `server/tests/` にテストを足す。Windows でしか動かない部分（SendInput、ddagrab）は、`NullInput` や偽の映像で動きを確かめ、残りは実機で確かめる項目に書く
- アプリの変更は、GitHub Actions のビルドが通ることまでを確かめる
- 文書は普通の言葉で書く（CLAUDE.md の決まり）

---

## A. 実機で試す前に直すもの

### 1. 前の接続が応答しないと、新しい接続が10秒待たされる（済）

- 済: 前の接続には閉じる合図だけを送り、閉じるのは裏で行う。新しい接続は、前の接続の後片付け（映像を止める、押したままのキーを離す）だけを待つ（最大3秒）。`close_timeout=2`。テスト `test_unresponsive_previous_client_does_not_delay_new_one`

- 場所: `server/duo_server/server.py:339`（`await previous.close(...)`）
- 何が起きるか: iPhone の電波が切れて、アプリがつなぎ直すと、サーバーには古い接続が残っている。サーバーは古い接続の close の返事を待つので（websockets の `close_timeout` の初期値 10 秒）、新しい接続は welcome が 10 秒来ない。手元で再現した（古い接続の読み取りを止めてから新しくつなぐと、welcome まで 10.0 秒）
- 直し方:
  - `previous.close()` を待たずに `asyncio.create_task(previous.close(...))` にする。あわせて、新しい接続の映像は、古い接続の後片付け（`stop_stream`、`release_held`）が終わってから始める（ffmpeg が同時に 2 つ動かないようにする）
  - `__main__.py:67` の `serve(...)` に `close_timeout=2` を付ける
- 確かめ方: `test_session.py` にテストを足す。1つ目の接続で `ws.transport.pause_reading()` してから 2つ目をつなぎ、welcome が 1 秒以内に届くこと

### 2. アプリが、通信が途切れたことに気づけない（済）

- 済: 5秒ごとに ping、10秒 pong が来なければつなぎ直す。welcome が10秒来なければ失敗。接続中の画面に「やめる」。メニューに「遅れ: 〇ms」

- 場所: `ios/DuoRemote/RemoteSession.swift:68-88`（`open()`）
- 何が起きるか: アプリは受け取るだけで、自分からは何も送らない。Wi-Fi が静かに切れると、iOS 側の TCP が諦めるまで（数十秒〜数分）映像が止まったままで、つなぎ直しも始まらない。サーバー側は websockets の ping で 40 秒以内に気づくが、アプリ側に同じ仕組みがない
- 直し方:
  - 接続中は 5 秒ごとに `ping`（`n` は通し番号）を送り、10 秒以内に `pong` が来なければ切れたとみなして `fail` を呼ぶ（つなぎ直しに入る）
  - `.connecting` のまま welcome が 10 秒来なければ、接続に失敗したことにする（今は URLSession の初期値の 60 秒まで「接続しています…」のまま）。接続画面に「やめる」を付ける
  - 往復の時間（ping を送ってから pong が来るまで）を覚えておき、メニューに「遅れ: 23ms」のように出す（11 の調整に使う）
- 確かめ方: ビルドが通ること。実機で、PC の Wi-Fi を切ってから 15 秒以内に「つなぎ直しています…」が出ること

### 3. 使っている最中に iPhone の画面がロックされる（済）

- 済: 接続中（つなぎ直し中も）は `isIdleTimerDisabled = true`

- 場所: `ios/DuoRemote/ContentView.swift`（接続中の画面）
- 何が起きるか: 映像を見ているだけだと触らないので、自動ロックの時間で画面が消える
- 直し方: 接続中（`session.isConnectedOrReconnecting`）は `UIApplication.shared.isIdleTimerDisabled = true`、切れたら `false` に戻す。`onChange` で切り替える
- 確かめ方: ビルドが通ること。実機で、自動ロックを 30 秒にして、接続中は消えないこと

### 4. アプリを裏に回して戻すと、映像が止まったままになる（済）

- 済: `requiresFlushToResumeDecoding` か `failed` なら flush して、SPS/PPS を忘れて次のキーフレームを待つ。`scenePhase` が `.background` で接続を閉じ、`.active` でつなぎ直す

- 場所: `ios/DuoRemote/H264Renderer.swift:28-66`、`ios/DuoRemote/DuoRemoteApp.swift`
- 何が起きるか: iOS は、アプリが裏に回るとデコーダーを止める。戻ったとき `AVSampleBufferDisplayLayer` は `requiresFlushToResumeDecoding` が true になり、`flush()` を呼ぶまで何も映らない。今はこれを見ていない。また、裏に回っている間もサーバーは 8Mbps で送り続け、戻ったときに古いフレームがまとめて届く
- 直し方:
  - `enqueue` の前に `layer.requiresFlushToResumeDecoding` を見て、true なら `flush()` してから、次のキーフレームが来るまで待つ（`formatDescription = nil` にして SPS/PPS 付きのキーフレームを待つ、今の仕組みが使える）
  - `scenePhase` を見て、`.background` になったら `disconnect()` に近い処理で接続を閉じ（接続先は覚えておく）、`.active` に戻ったら自動でつなぎ直す。見ていたウィンドウに戻す処理（`welcome` の `reconnected`）をそのまま使う
- 確かめ方: ビルドが通ること。実機で、ホーム画面に戻って 10 秒後にアプリに戻り、2 秒以内に映像が動くこと

### 5. 映像の受け取りと切り分けが、画面を描くスレッドで動いている（済）

- 済: 受信を `Task.detached` にし、映像はそのままデコーダーへ、制御メッセージだけメインスレッドへ。`H264Renderer` の状態はロックで守る。`splitNALs` は `withUnsafeBytes` で走査（前の実装と同じ結果になることを、乱数の入力2万件で確かめた）

- 場所: `ios/DuoRemote/RemoteSession.swift:76-88`（`receiveTask`）、`ios/DuoRemote/H264Renderer.swift:30, 73-95`（`splitNALs`）
- 何が起きるか: `RemoteSession` は `@MainActor` なので、中で作った `Task` もメインスレッドで動く。60fps の各フレームで `[UInt8](data)` にコピーして 1 バイトずつ見る処理がメインスレッドに乗り、タッチやメニューの描画と取り合いになる
- 直し方:
  - 受信のループを `Task.detached` にして、バイナリは直接 `renderer.enqueue` に渡す（`H264Renderer` は `@MainActor` ではないので、そのまま呼べる）。JSON の制御メッセージだけ `await MainActor.run { handleControl(...) }` で渡す
  - `splitNALs` は `Data.withUnsafeBytes` で走査して、コピーを減らす
- 確かめ方: ビルドが通ること。実機で、60fps のときにメニューやタッチが引っかからないこと

### 6. IME の状態を取るときに、PC 側のアプリが固まっているとサーバーごと止まる（済）

- 済: `SendMessageTimeoutW`（`SMTO_ABORTIFHUNG | SMTO_BLOCK`、200ms）。`ime_state`、`fit_window`、`focus_window` は `asyncio.to_thread` で呼ぶ

- 場所: `server/duo_server/input_win.py:405-413`（`ime_state`）
- 何が起きるか: 0.5 秒ごとに `SendMessageW` で前面のウィンドウの IME に問い合わせている。`SendMessageW` は相手が返事するまで戻らないので、前面のアプリが固まると、サーバーの処理（映像の送信も）が全部止まる
- 直し方: `SendMessageTimeoutW`（`SMTO_ABORTIFHUNG | SMTO_BLOCK`、200ms）に替える。時間切れなら `None` を返す。`fit_window` の `SetWindowPos` と `focus_window` の `SetForegroundWindow` も同じ理由で止まることがあるので、`asyncio.to_thread` で呼ぶ
- 確かめ方: Windows で `python -c "from duo_server.input_win import WinInput; print(WinInput().ime_state())"` が動くこと。固まったアプリを前にしても映像が止まらないこと（実機）

### 7. 改行を含む文字を送ると、PC では改行にならない（済）

- 済: `text` を改行（`\r\n`、`\r`、`\n`）で分け、間で Enter を押す。テスト `test_text_newlines_become_enter`

- 場所: `server/duo_server/server.py:104-107`（`text` の処理）、`ios/DuoRemote/KeyboardInput.swift:340-343`
- 何が起きるか: アプリは Enter を押したときだけ `vkReturn` にしているので、複数行の貼り付けや音声入力の改行は `"\n"` のまま `text` で届く。サーバーは U+000A をそのまま Unicode で送るので、メモ帳などでは改行にならない（無視されるか、変な文字になる）
- 直し方: サーバーの `dispatch_input` で `text` を `"\r\n"` と `"\n"` で分け、間に `backend.key(VK_RETURN=0x0D, down/up)` を入れる。`docs/protocol.md` の `text` に「改行は Enter として入力する」と書く
- 確かめ方: `test_session.py` に、`"a\nb"` を送ると `("text","a"), ("key",0x0D,True), ("key",0x0D,False), ("text","b")` になるテストを足す

### 8. ウィンドウの切り替えで ffmpeg が起動できないと、接続ごと切れる（済）

- 済: `_switch_to_window` で `StreamError` を受け、`error` と `mode: screen` を送って画面全体に戻す。テスト `test_window_that_cannot_be_captured_falls_back_to_screen`

- 場所: `server/duo_server/server.py:218-248`（`set_mode`）、`server.py:194-203`（`_switch` → `open_stream`）
- 何が起きるか: `_switch` の中で `open_stream` が `StreamError` を出すと、`receive` のタスクが落ちて `handler` が接続を閉じる。アプリは自動でつなぎ直すが、ウィンドウを選び直すたびに 1 秒以上切れる
- 直し方: `set_mode` と `watch_window` で `StreamError` を受け取り、`error`（「このウィンドウは表示できませんでした」）を送ってから画面全体（`_switch(None, None)`）に戻す。それも失敗したときだけ接続を閉じる
- 確かめ方: `test_session.py` に、`region` が `None` 以外のとき `start()` で `StreamError` を出す偽の映像を使い、`error` と `mode: screen` が届いて接続が続くテストを足す

## B. 外出先で使うために

### 9. 画質（ビットレート、大きさ、フレームレート）をアプリから選べるようにする（済）

- 済: `hello` の `bitrate`（500〜20000）。アプリに画質（高・中・低）。接続中に変えたらつなぎ直す。テスト `test_hello_quality_settings`

- 場所: `docs/protocol.md`（`hello`）、`server/duo_server/server.py:345-347`、`ios/DuoRemote/ContentView.swift`（接続画面かメニュー）
- 何が起きるか: 今は `--bitrate` と `--max-height` をサーバー側でしか変えられない。外（Tailscale 経由、4G/5G）では 8Mbps は多すぎて、家の中では落としたくない
- 直し方:
  - `hello` に `bitrate`（kbps）を足す。サーバーは `fps` と同じように受け取り、`per_conn` の設定に入れる（上限と下限を決める。例: 500〜20000）
  - アプリに「画質」の設定を足す。高（元の大きさ、8000kbps、60fps）、中（1080、4000kbps、60fps）、低（720、1500kbps、30fps）。`hello` で送る。接続中に変えたときは、つなぎ直す
- 確かめ方: `test_session.py` に、`hello` の `bitrate` が映像の設定に入るテストを足す（`factory` に渡る `settings` を見る）

### 10. 通信が細いと遅れがたまる（古いフレームを捨てていない）（済）

- 済: ffmpeg からの読み取りと送信を分け、送り待ちが4フレームを超えるか、送信バッファが256KBを超えたら、次のキーフレームまで捨てる。捨てた数をログに出す。テスト `test_frames_are_dropped_until_keyframe_when_sending_falls_behind`（送るのが遅い接続のかわりを使う）

- 場所: `server/duo_server/server.py:173-176`（`_send_video`）
- 何が起きるか: `ws.send` が詰まると、ffmpeg の出力が待たされるだけで、フレームを捨てない。TCP の送信バッファの分（数百 KB〜）が遅れとして残り、操作してから画面に出るまでが 1 秒以上になりうる
- 直し方: `ws.transport.get_write_buffer_size()` が閾値（例: 256KB）を超えていたら、次のキーフレームが来るまでフレームを捨てる（キーフレームの間隔は 2 秒なので、最大 2 秒で立て直せる）。捨てた数をログに出す
- 確かめ方: `test_session.py` に、送信が詰まった状態（読み取りを止めた接続）で、再開後に最初に届くのがキーフレームであるテストを足す。実機では、`--bitrate 20000` でわざと詰まらせて、遅れが戻ること

### 11. NVENC の遅れを減らす設定を試す（済）

- 済: `-delay 0 -zerolatency 1` を足した。`-tune ull` との比べは、実機で行う

- 場所: `server/duo_server/capture.py:97-102`
- 何が起きるか: `-tune ll` と `-preset p1` は付いているが、`-delay 0`（出力をフレーム単位で遅らせない）と `-zerolatency 1` が付いていない。初期値では数フレーム（60fps で 50ms 前後）遅れることがある
- 直し方: `-delay 0 -zerolatency 1` を足す。`test_capture.py` の `test_ffmpeg_cmd_nvenc_no_scale_stays_on_gpu` に確認を足す。実機で 2 の「遅れ」の表示と、画面の動きを見て、`-tune ull` の方がよければそちらにする
- 確かめ方: テストと、実機での比較

## C. 細かい直し

### 12. 主モニター以外が ddagrab の output 0 になることがある（済）

- 済: `--output-idx` を足し、README の「うまくいかないとき」に書いた。主モニターの位置に合わせる処理は、まだ（E の複数モニターと一緒に行う）

- 場所: `server/duo_server/capture.py:78, 86`、`server/duo_server/input_win.py:272`（`screen_size`）
- 何が起きるか: 映像は DXGI の output 0 を撮り、入力の座標は Windows の主モニター（左上が 0,0）を基準にしている。モニターが 2 枚以上あると、output 0 が主モニターとは限らず、映像と入力がずれる
- 直し方: まず `--output-idx` のオプションをサーバーに足して、ずれたときに手で直せるようにする。README の「うまくいかないとき」に書く。本当に合わせるなら、`EnumDisplayMonitors` で主モニターの位置と大きさを取り、`screen_size` と `_absolute` の原点をそれに合わせる
- 確かめ方: `test_capture.py` で `output_idx=1` がコマンドに入ること。実機（2 枚以上のモニター）

### 13. サーバーのテストを GitHub Actions で回す（済）

- 済: `.github/workflows/server-test.yml`（ubuntu-latest と windows-latest）。Windows では `create_input()` も読み込む

- 場所: `.github/workflows/`（新しく `server-test.yml` を作る）
- 直し方: `ubuntu-latest` で `pip install -r server/requirements-dev.txt` と `python -m pytest`。`windows-latest` でも同じテストを回す（`input_win.py` の Windows 側の定義が壊れていないかは、`python -c "import duo_server.input_win"` で分かる）。`server/**` と workflow の変更で動かす
- 確かめ方: Actions が通ること

### 14. サーバーのバージョンが 0.1.0 のまま（済）

- 済: サーバーとアプリを 0.6.0 にそろえた。README の「作る人向け」に、版の上げ方を書いた

- 場所: `server/duo_server/__init__.py:1`（0.1.0）、`ios/project.yml`（0.5.0）
- 直し方: 0.5.0 にそろえる。README の「作る人向け」に「版を上げるときは両方を同時に変える」と書く

### 15. 使っていない関数を消す（済）

- 済: 消した

- 場所: `ios/DuoRemote/RemoteSession.swift:232-245`（`fail(_ error: Error)`）
- 直し方: 消す。どこからも呼ばれていない（`fail(_ failedTask:)` だけが使われている）

### 16. config.json に token がないと落ちる（済）

- 済: `token` がないか空なら、作り直して保存し、表示する。テスト `test_load_config_recreates_missing_token`

- 場所: `server/duo_server/__main__.py:49`
- 何が起きるか: 手で編集して `token` を消すと `KeyError` で止まる
- 直し方: `token` がない、または空なら、作って保存し直し、「トークンを作り直しました」と表示する
- 確かめ方: `test_main.py` にテストを足す

### 17. ウィンドウの一覧に、デスクトップ（Program Manager）が出る（済）

- 済: `GetClassNameW` が `Progman` か `WorkerW` のものを外す

- 場所: `server/duo_server/input_win.py:244-254`（`_is_listable`）
- 直し方: クラス名（`GetClassNameW`）が `Progman` か `WorkerW` のものを外す
- 確かめ方: 実機で一覧を見る

### 18. トラックパッド方式で、カーソルの位置が実際とずれる（済）

- 済: `move_rel`（`dx`, `dy` は表示している範囲を1とした割合）を足した。サーバーは今のカーソルの位置（`GetCursorPos`）に足して、絶対座標で送る（相対移動の SendInput はマウスの加速の設定で量が変わるため）。トラックパッドの方式のクリックは、`x`, `y` を付けずに送る。テスト `test_move_rel`

- 場所: `ios/DuoRemote/TouchInput.swift:53, 230-238`
- 何が起きるか: アプリは「カーソルは (0.5, 0.5) にある」と思って始め、動かした分を足して絶対座標で送る。PC 側でマウスを動かしたり、直接触る方式でクリックしたりすると実際の位置が変わるので、次に指を動かしたとき、カーソルが飛ぶ
- 直し方（取り決めを変える）: `move_rel`（`dx`, `dy`、画素）を `docs/protocol.md` に足し、サーバーは `MOUSEEVENTF_MOVE` だけ（絶対座標のフラグなし）で送る。トラックパッド方式では `move` の代わりに `move_rel` を送る。クリックは `x`, `y` を付けずに送る（今のカーソル位置で押す）
- 確かめ方: `test_session.py` に `move_rel` のテストを足す。実機で、PC のマウスを動かしたあとに iPhone で動かしても飛ばないこと

### 19. Bluetooth キーボードの F13 以降が送れない（済）

- 済: HID 0x68〜0x72 をスキャンコード 0x64〜0x6E、0x73（F24）を 0x76 にした

- 場所: `ios/DuoRemote/KeyMap.swift:71-72`
- 直し方: HID 0x68〜0x73（F13〜F24）をスキャンコード 0x64〜0x6F に足す。Mac のキーボードは F13〜F19 がある

### 20. 右上のメニューのボタンが、ノッチや Dynamic Island と重なることがある（済）

- 済: 画面の安全な範囲の余白（`safeAreaInsets`）の分、ボタンとお知らせを内側に寄せた。実機で見て、必要なら直す

- 場所: `ios/DuoRemote/ContentView.swift:53, 73, 176`
- 何が起きるか: 映像は `.ignoresSafeArea(.container)` で端まで出しているので、重ねたボタンも端に寄る。縦向きでは状態バーの下に入る可能性がある
- 直し方: ボタンの `padding` を `safeAreaInsets` の分だけ増やす（`GeometryReader` か `.safeAreaPadding`）。実機で見てから決める

### 21. アプリのアイコンがない（済）

- 済: `Assets.xcassets/AppIcon.appiconset`（1024x1024、紺の背景に白いモニターと D）

- 場所: `ios/DuoRemote/`（Assets.xcassets がない）、`ios/project.yml`
- 直し方: `Assets.xcassets/AppIcon.appiconset` に 1024x1024 の画像を 1 枚置く（単色の背景に「D」の文字でよい）。`project.yml` の `sources` に入れる。SideStore の一覧で区別しやすくなる

### 22. 文字を送るときの上限と区切り（済）

- 済: `docs/protocol.md` の `text` に、文字（コードポイント）で数えること、超えた分は捨てること、改行は Enter として入力することを書いた

- 場所: `ios/DuoRemote/RemoteSession.swift:166-172`（`sendText`）
- 何が起きるか: 4096 文字ごとに分けているが、サロゲートペア（絵文字など）の途中で切れることはない（Swift の `Character` 単位なので問題ない）。ただし `text` の取り決めは「4096 文字まで」で、UTF-16 の単位か文字かが書いていない
- 直し方: `docs/protocol.md` の `text` に「文字（コードポイント）で数える。超えた分はサーバーが捨てる」と書く。サーバーは `s[:4096]` のままでよい

### 23. README の「うまくいかないとき」に足すこと（済）

- 済: README の「うまくいかないとき」に、パブリック ネットワークとスリープのことを書いた

- Windows のファイアウォールで、Wi-Fi が「パブリック ネットワーク」になっていると、プライベートの許可だけではつながらない。「設定 → ネットワークとインターネット → Wi-Fi → ネットワーク プロファイル」をプライベートにするか、パブリックも許可する
- サーバーを起動したまま PC がスリープすると、接続が切れる。スリープを止める設定か、Wake on LAN は使えないことを書く

---

## D. 実機で確かめること（人が行う）

A の 1〜8 を直してから、次の順に試す。うまくいかなかったものは、この文書に「何をしたら、何が起きたか」を書き足す。

1. `start.bat` で初回の準備（Python と ffmpeg の自動インストール）が通ること。エンコーダーが `h264_nvenc` と表示されること
2. 同じ Wi-Fi で接続し、映像が出ること。`--fps 60` で動きが滑らかなこと。「遅れ」の表示（2 で足す）が 50ms 以下なこと
3. タッチ: タップ、ダブルクリック、長押し、ドラッグ、2 本指のスクロール、ピンチ、トラックパッド方式
4. ウィンドウ単位: 一覧、選択、「この画面に大きさを合わせる」、ウィンドウを動かしたとき、閉じたとき、最小化したとき
5. iPhone のキーボード: 日本語の変換と確定、Ctrl+C などの組み合わせ、Enter と BS、複数行の貼り付け（7）
6. Bluetooth キーボード: 英字、記号（JIS と US の違い）、英数・かな、command を Ctrl にする設定、押し続けたときの繰り返し
7. IME の表示（あ / A）が合っていること。メモ帳、ブラウザ、ターミナルで確かめる
8. 切断: PC の Wi-Fi を切る、サーバーを Ctrl+C で止める、別の端末でつなぐ。それぞれで、アプリの表示と、つなぎ直しが決まりどおりなこと。押したままのキーが離されること
9. アプリを裏に回して戻す（4）。iPhone をロックして解除する
10. Duo の内側画面でいっぱいに表示されること。開閉と回転でウィンドウの大きさが合い直ること
11. Tailscale で外からつなぐ。画質の設定（9）を低にして、使えるか。遅れがたまらないか（10）

## E. 後回し（計画にあるが、まだ作らないもの）

- 主モニター以外のモニターの切り替え
- 音
- 選んだウィンドウだけを映す（他のウィンドウが重なっても映さない。Windows Graphics Capture を使う）
- クリップボードの共有
- Windows にログインしたときにサーバーを自動で起動する（タスク スケジューラの設定を README に書くだけでもよい）
- 通信の暗号化（wss）。Tailscale の中なら不要。LAN ではトークンがそのまま流れることを README に書く
