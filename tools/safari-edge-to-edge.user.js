// ==UserScript==
// @name         帯の下まで表示
// @description  iPhone Duo の外側の画面で、Safari の右の帯の下まで、ページを広げる。右の帯の空いている所のボタンで、サイトごとに切り替える。内側の画面では、何もしない
// @version      3.3
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

  // --- Safari が広げなかったときの方法。ページの幅を、帯の分だけ無理に広げる
  // html（外枠）は、いつも、帯の分だけ広げておく。そうすると、帯の中に、ボタンを置ける。
  // body（中身）は、オンのときだけ広げる。オフのときは、元の幅のままにする
  function css(g, wide) {
    var base = document.documentElement.clientWidth;
    var w = base + g;
    return (
      'html{width:' + w + 'px!important;min-width:' + w + 'px!important;' +
      'overflow-x:clip!important;touch-action:pan-y pinch-zoom!important;overscroll-behavior-x:none!important}' +
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
    timer = setTimeout(function () { timer = null; if (on && hackGap) fixFixed(hackGap); }, 400);
  }

  function apply() {
    // 広げた状態では、幅を測れないので、いったん元に戻してから測る
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
    placeHandle(q);
  }

  // ボタンの置き場所は、右の帯の、戻るボタンと「＋」ボタンの間。固定表示は、帯の手前までしか描かれないので、
  // ページの中に置いて、スクロールのたびに位置を合わせる
  function placeHandle(q) {
    if (!handle) return;
    handle.style.display = '';
    handle.textContent = on ? '\u2192\u2190' : '\u2190\u2192';
    var w = document.documentElement.clientWidth;
    if (q.cover) {
      handle.mode = 'fixed';
      handle.style.cssText = BUTTON_CSS + 'position:fixed;top:70%;left:' + (w - 54) + 'px';
    } else {
      handle.mode = 'follow';
      handle.style.cssText = BUTTON_CSS + 'position:absolute;left:' + (w + q.g - 54) + 'px';
      followHandle();
    }
  }

  function followHandle() {
    if (handle && handle.mode === 'follow') handle.style.top = (window.scrollY + window.innerHeight * 0.7) + 'px';
  }

  function patchAllOff() {
    var ms = document.querySelectorAll('meta[name=viewport]');
    for (var i = 0; i < ms.length; i++) {
      var c = ms[i].getAttribute('content') || '';
      if (/viewport-fit=cover/.test(c)) ms[i].setAttribute('content', c.replace(/,?\s*viewport-fit=cover/, ''));
    }
  }

  var BUTTON_CSS =
    'width:40px;height:40px;border-radius:20px;color:#222;' +
    'background:linear-gradient(#ececf0,#b4b4bc);box-shadow:0 2px 8px rgba(0,0,0,0.35),inset 0 1px 0 rgba(255,255,255,0.8);' +
    'font:700 15px/40px sans-serif;letter-spacing:-1px;text-align:center;' +
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
    new MutationObserver(scheduleFix).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class'] });
  });
  window.addEventListener('load', apply);
  window.addEventListener('scroll', followHandle, { passive: true });
  window.addEventListener('orientationchange', function () { setTimeout(apply, 300); });
  window.addEventListener('resize', function () { setTimeout(apply, 300); });
})();
