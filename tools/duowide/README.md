# DuoWide（iPhone Duo の外側の画面で、Safari の右の帯の下まで、ページを広げる）

作者: [hyungchulc/duowide](https://github.com/hyungchulc/duowide)（MIT。`LICENSE` を参照）。
Userscripts（App Store の無料アプリ）で動く。

## 中身

| ファイル | 内容 |
|---|---|
| `DuoWide.user.js` | 本体（2.0.3）。**1か所だけ、書き足してある**（末尾の `window.__duoWideSwitch`。「広げる・広げない」を、その場で切り替える）。自動更新の行（`@updateURL`、`@downloadURL`）は、外してある |
| `DuoWide-Frames.user.js` | 本体の補助（必須）。作者のもの、そのまま |
| `DuoWide-WideText.user.js` | 文章を、帯の下まで通す（省略できる）。作者のもの、そのまま |
| `DuoWide-Toggle.user.js` | このリポジトリで作った、切り替えボタン（左下）。押すと、`window.__duoWideSwitch` を呼ぶ |

## 入れ方

1. 上の4つを、Userscripts のフォルダーに入れる（`DuoWide-WideText` は、省略できる）。
2. Userscripts のポップアップで「↻」を押す。ページを開き直す。

## 作者の新しい版が出たとき

自動更新を外してあるので、勝手には、上書きされない。新しい版に替えるときは、`DuoWide.user.js` の末尾（`start();` の前）に、`window.__duoWideSwitch` を、足し直す:

```js
window.__duoWideSwitch = (on) => {
  if (on) { yielded = false; rewrites = []; consider(); }
  else { yielded = true; restore(); }
};
```

実機での確認は、まだ十分ではない。
