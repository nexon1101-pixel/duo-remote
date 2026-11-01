// ==UserScript==
// @name         帯の下まで表示
// @description  iPhone Duo の外側の画面で、Safari の右の帯の下まで、ページを広げる。左下のボタンで、サイトごとに切り替える。内側の画面では、何もしない
// @version      4.1
// @match        *://*/*
// @run-at       document-start
// ==/UserScript==
(function () {
  var KEY = 'edge2edge:' + location.host;
  var GKEY = 'e2e-gap:' + screen.width; // 画面の幅ごとに、帯の幅を覚える（外側の画面だけに、値が残る）
  var MARK = 'data-e2e';
  var on = safeGet(KEY) !== 'off';
  var style = document.createElement('style');
  var handle;
  var hackGap = 0; // 幅を無理に広げているときの、帯の幅

  function safeGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function safeSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

  // 画面全体の幅と、ページの幅の差が、右の帯の幅。Safari が、帯まで広げているとき（差がないとき）は、覚えた値を使う
  function measure() {
    var d = Math.round(screen.width - document.documentElement.clientWidth);
    if (d > 20 && d < 400) { safeSet(GKEY, String(d)); return { g: d, cover: false }; }
    var saved = parseInt(safeGet(GKEY) || '0', 10);
    if (saved && d >= -2 && d <= 2) return { g: saved, cover: true };
    return { g: 0, cover: false };
  }

  // --- 読み込みの最初に、viewport-fit=cover を足す（覚えた帯の幅があるとき = 外側の画面だと分かっているとき）
  function patchMeta(m) {
    var c = m.getAttribute('content') || '';
    var has = /viewport-fit=cover/.test(c);
    if (on && !has) m.setAttribute('content', c + (c ? ', ' : '') + 'viewport-fit=cover');
    else if (!on && has) m.setAttribute('content', c.replace(/,?\s*viewport-fit=cover/, ''));
  }

  function patchAll() {
    var ms = document.querySelectorAll('meta[name=viewport]');
    for (var i = 0; i < ms.length; i++) patchMeta(ms[i]);
  }

  var knownOuter = parseInt(safeGet(GKEY) || '0', 10) > 0;
  if (knownOuter) {
    new MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        var added = records[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          var n = added[j];
          if (n.nodeType !== 1) continue;
          if (n.tagName === 'META' && n.getAttribute('name') === 'viewport') patchMeta(n);
          else if (n.querySelector) {
            var m = n.querySelector('meta[name=viewport]');
            if (m) patchMeta(m);
          }
        }
      }
    }).observe(document, { childList: true, subtree: true });
    patchAll();
  }

  // --- 描画の前に広げる。覚えた帯の幅があれば、スクリプトが動いた瞬間に、CSS を入れる
  // （100vw は、広げる前の幅。html は広げ、body は、オンのときだけ広げる）
  var earlyStyle = null;
  (function () {
    var saved = parseInt(safeGet(GKEY) || '0', 10);
    if (!saved) return;
    earlyStyle = document.createElement('style');
    earlyStyle.textContent =
      'html{width:calc(100vw + ' + saved + 'px)!important;min-width:calc(100vw + ' + saved + 'px)!important;' +
      'overflow-x:hidden!important;touch-action:pan-y pinch-zoom!important;overscroll-behavior-x:none!important}' +
      'body{width:' + (on ? '100%' : '100vw') + '!important;overflow-x:clip!important}';
    (document.head || document.documentElement).appendChild(earlyStyle);
  })();

  // --- Safari が広げなかったときの方法。ページの幅を、帯の分だけ無理に広げる
  // html（外枠）は、いつも、帯の分だけ広げておく。そうすると、帯の中に、ボタンを置ける。
  // body（中身）は、オンのときだけ広げる。オフのときは、元の幅のままにする
  function css(g, wide) {
    var base = document.documentElement.clientWidth;
    var w = base + g;
    return (
      'html{width:' + w + 'px!important;min-width:' + w + 'px!important;' +
      'overflow-x:hidden!important;touch-action:pan-y pinch-zoom!important;overscroll-behavior-x:none!important}' +
      'body{width:' + (wide ? '100%' : base + 'px') + '!important;overflow-x:clip!important}' +
      'html::-webkit-scrollbar{display:none!important}'
    );
  }

  // 固定表示（fixed）と、張り付く表示（sticky）の、幅いっぱいのものは、帯の分だけ広げる。右の端に寄ったものは、右へずらす
  function fixFixed(g) {
    var base = document.documentElement.clientWidth;
    var all = document.body ? document.body.getElementsByTagName('*') : [];
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (el.hasAttribute(MARK) || el === handle) continue;
      var pos = getComputedStyle(el).position;
      if (pos !== 'fixed' && pos !== 'sticky') continue;
      var r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (r.left <= 2 && r.right >= base - 2) {
        el.style.setProperty('width', (r.width + g) + 'px', 'important');
        el.style.setProperty('max-width', 'none', 'important');
        el.setAttribute(MARK, 'w');
      } else if (pos === 'fixed' && r.right >= base - 2) {
        el.style.setProperty('translate', g + 'px 0', 'important');
        el.setAttribute(MARK, 't');
      }
    }
  }

  function unfixFixed() {
    var marked = document.querySelectorAll('[' + MARK + ']');
    for (var i = 0; i < marked.length; i++) {
      var el = marked[i];
      if (el.getAttribute(MARK) === 'w') { el.style.removeProperty('width'); el.style.removeProperty('max-width'); }
      else el.style.removeProperty('translate');
      el.removeAttribute(MARK);
    }
  }

  var timer = null;
  function scheduleFix() {
    if (timer || !on || !hackGap) return;
    timer = setTimeout(function () { timer = null; if (on && hackGap) fixFixed(hackGap); }, 1200);
  }

  function apply() {
    // 広げた状態では、幅を測れないので、いったん元に戻してから測る
    if (earlyStyle) { earlyStyle.remove(); earlyStyle = null; }
    style.remove();
    unfixFixed();
    hackGap = 0;
    var q = measure();
    if (!q.g) {
      // 右の帯がない（内側の画面など）。何もしない
      patchAllOff();
      if (handle) handle.style.display = 'none';
      return;
    }
    patchAll();
    if (!q.cover) {
      // Safari が、帯まで広げていない。外枠を広げる（中身を広げるのは、オンのときだけ）
      hackGap = on ? q.g : 0;
      style.textContent = css(q.g, on);
      (document.head || document.documentElement).appendChild(style);
      if (on) fixFixed(q.g);
    }
    placeHandle();
  }

  // ボタンは、ページの左下に、固定して置く。帯の中は、タップが届かないので、置かない
  // オンのとき（広げている）は、内向きの矢印（押すと、元の幅に戻る）。オフのときは、外向きの矢印（押すと、広がる）
  function icon(path) {
    return '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round" style="display:block;margin:7px"><path d="' + path + '"/></svg>';
  }
  var ICON_ON = icon('M3 12h7M7 8l3.5 4-3.5 4M21 12h-7M17 8l-3.5 4 3.5 4');
  var ICON_OFF = icon('M3 12h18M7 8l-4 4 4 4M17 8l4 4-4 4');

  function placeHandle() {
    if (!handle) return;
    handle.style.display = '';
    handle.innerHTML = on ? ICON_ON : ICON_OFF;
    // 高さは、画面の高さの 88% が、ボタンの真ん中。下のバーが出入りしても動かないように、バーが引っ込んだときの
    // 大きい画面の高さ（lvh）を基準にする（lvh が使えないときは vh）
    handle.style.cssText = BUTTON_CSS + 'position:fixed;left:6px;top:calc(88vh - 18px);top:calc(88lvh - 18px);color:#fff;';
  }

  function patchAllOff() {
    var ms = document.querySelectorAll('meta[name=viewport]');
    for (var i = 0; i < ms.length; i++) {
      var c = ms[i].getAttribute('content') || '';
      if (/viewport-fit=cover/.test(c)) ms[i].setAttribute('content', c.replace(/,?\s*viewport-fit=cover/, ''));
    }
  }

  var BUTTON_CSS =
    'width:36px;height:36px;border-radius:18px;box-sizing:border-box;opacity:0.32;' +
    'background:rgba(28,28,32,0.5);-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px);' +
    'border:1px solid rgba(255,255,255,0.22);' +
    'z-index:2147483647;user-select:none;-webkit-user-select:none;-webkit-tap-highlight-color:transparent;';

  // 押すと、このサイトだけ、オン・オフを切り替える
  function makeHandle() {
    handle = document.createElement('div');
    handle.addEventListener('click', function () {
      on = !on;
      safeSet(KEY, on ? 'on' : 'off');
      apply();
    });
    document.documentElement.appendChild(handle);
  }

  document.addEventListener('DOMContentLoaded', function () {
    makeHandle();
    apply();
    new MutationObserver(scheduleFix).observe(document.documentElement, { childList: true, subtree: true });
    document.addEventListener('click', function () { setTimeout(function () { if (on && hackGap) fixFixed(hackGap); }, 300); }, true);
  });
  window.addEventListener('load', apply);
  window.addEventListener('orientationchange', function () { setTimeout(apply, 300); });
  window.addEventListener('resize', function () { setTimeout(apply, 300); });
})();
