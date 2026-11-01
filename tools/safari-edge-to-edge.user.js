// ==UserScript==
// @name         帯の下まで表示
// @description  iPhone Duo の外側の画面で、Safari の右の帯の下まで、ページを広げる。左の端のつまみで、サイトごとに切り替える。内側の画面では、何もしない
// @version      3.0
// @match        *://*/*
// @run-at       document-start
// ==/UserScript==
(function () {
  var KEY = 'edge2edge:' + location.host;
  var on = localStorage.getItem(KEY) !== 'off';
  var style = document.createElement('style');
  var handle;
  var gapNow = 0;
  var MARK = 'data-e2e';

  // 画面全体の幅と、ページの幅の差が、右の帯の幅
  function gap() {
    var d = Math.round(screen.width - document.documentElement.clientWidth);
    return d > 20 && d < 400 ? d : 0;
  }

  function viewport() {
    var m = document.querySelector('meta[name=viewport]');
    if (!m) return;
    var c = m.content.replace(/,?\s*viewport-fit=cover/, '');
    m.content = on ? c + ', viewport-fit=cover' : c;
  }

  function css(g) {
    var w = document.documentElement.clientWidth + g;
    return (
      'html{width:' + w + 'px!important;min-width:' + w + 'px!important;' +
      'overflow-x:clip!important;touch-action:pan-y pinch-zoom!important;overscroll-behavior-x:none!important}' +
      'body{width:100%!important;overflow-x:clip!important}' +
      'html::-webkit-scrollbar{display:none!important}'
    );
  }

  // 固定表示（position:fixed）は、広げる前の幅の中にしか描かれない。幅いっぱいのものは、帯の分だけ広げ、
  // 右の端に寄せたものは、帯の分だけ右へずらす
  function fixFixed(g) {
    var base = document.documentElement.clientWidth;
    var all = document.body ? document.body.getElementsByTagName('*') : [];
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (el.hasAttribute(MARK) || el === handle) continue;
      var cs = getComputedStyle(el);
      if (cs.position !== 'fixed') continue;
      var r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (r.left <= 2 && r.right >= base - 2) {
        el.style.setProperty('width', (r.width + g) + 'px', 'important');
        el.style.setProperty('max-width', 'none', 'important');
        el.setAttribute(MARK, 'w');
      } else if (r.right >= base - 2) {
        el.style.setProperty('translate', g + 'px 0', 'important');
        el.setAttribute(MARK, 't');
      }
    }
  }

  function unfixFixed() {
    var marked = document.querySelectorAll('[' + MARK + ']');
    for (var i = 0; i < marked.length; i++) {
      var el = marked[i];
      var kind = el.getAttribute(MARK);
      if (kind === 'w') { el.style.removeProperty('width'); el.style.removeProperty('max-width'); }
      else { el.style.removeProperty('translate'); }
      el.removeAttribute(MARK);
    }
  }

  var timer = null;
  function scheduleFix() {
    if (timer || !on || !gapNow) return;
    timer = setTimeout(function () { timer = null; if (on && gapNow) fixFixed(gapNow); }, 400);
  }

  function apply() {
    // 広げた状態では、幅を測れないので、いったん元に戻してから測る
    style.remove();
    unfixFixed();
    var g = gap();
    gapNow = g;
    if (!g) {
      // 右の帯がない（内側の画面など）。何もしない
      var m = document.querySelector('meta[name=viewport]');
      if (m) m.content = m.content.replace(/,?\s*viewport-fit=cover/, '');
      if (handle) handle.style.display = 'none';
      return;
    }
    if (handle) { handle.style.display = ''; handle.style.opacity = on ? '0.45' : '0.2'; }
    viewport();
    if (on) {
      style.textContent = css(g);
      (document.head || document.documentElement).appendChild(style);
      fixFixed(g);
    }
  }

  // 左の端の、細長いつまみ。ページの左の余白に収める。押すと、このサイトだけ、オン・オフを切り替える
  function makeHandle() {
    handle = document.createElement('div');
    handle.style.cssText =
      'position:fixed;left:0;top:70%;width:12px;height:56px;border-radius:0 8px 8px 0;' +
      'background:#000;z-index:2147483647;-webkit-tap-highlight-color:transparent';
    handle.addEventListener('click', function () {
      on = !on;
      localStorage.setItem(KEY, on ? 'on' : 'off');
      apply();
    });
    document.documentElement.appendChild(handle);
    handle.setAttribute(MARK + '-self', '1');
  }

  document.addEventListener('DOMContentLoaded', function () {
    makeHandle();
    apply();
    new MutationObserver(scheduleFix).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class'] });
  });
  window.addEventListener('load', apply);
  window.addEventListener('orientationchange', function () { setTimeout(apply, 300); });
  window.addEventListener('resize', function () { setTimeout(apply, 300); });
})();
