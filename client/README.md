# ヘッドレスクライアント（AI エージェント用）

画面を持たないクライアント。duo_server につなぎ、映像を PNG のスナップショット
として書き出し、マウス・キー・文字の操作を送る。AI エージェントが「画面を見て
操作する」ために使う。

## 使い方

依存: Python 3.11+、`websockets`（pip）、`ffmpeg`（PATH か imageio-ffmpeg）。

設定ファイル `~/.config/duo-client/config.json` に、接続先とトークンを書く
（トークンは PC 側の `server/config.json` のもの。リポジトリには入れない）。

```json
{"host": "your-pc", "port": 8765, "token": "..."}
```

起動（Tailscale 越し）:

```sh
python3 duo_client.py --tailscale
```

起動すると `{"ready": true, "role": "control", ...}` が1行出る。以後は標準入力に
JSON を1行ずつ送ると、1行ずつ返事が来る。コマンドの一覧は duo_client.py の
冒頭の説明を参照。

- `--role view` で視聴だけの接続にもできる。操作側が既にいると、操作でつないでも
  視聴に降格され、`ready` の `demoted` が true になる（protocol.md の「視聴モード」）
- `--tailscale` は、HTTPS_PROXY のポートを 3130 に変えた CONNECT プロキシ経由で
  つなぐ。同じネットワークなら付けなくてよい

## テスト

```sh
python3 -m pytest test_client.py
```

本物のサーバーを疑似バックエンドで立て、本物の H.264 で、スナップショットと
操作の往復と、視聴への降格を確かめる。
