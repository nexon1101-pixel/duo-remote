// ==UserScript==
// @name         帯の下まで表示
// @description  iPhone Duo の Safari で、右の帯の下まで、ページを広げる。左の「⇔」で、サイトごとに切り替える
// @version      2.1
// @match        *://*/*
// @run-at       document-start
// ==/UserScript==
(function () {
  var KEY = 'edge2edge:' + location.host;
  var on = localStorage.getItem(KEY) !== 'off';
  var style = document.createElement('style');
  var button;

  // 画面全体の幅と、ページの幅の差が、右の帯の幅（縦向きのとき）
  function gap() {
    var full = Math.max(screen.width, screen.height) === screen.width ? screen.width : Math.min(screen.width, screen.height);
    var page = document.documentElement.clientWidth;
    var d = Math.round(full - page);
    return d > 20 && d < 400 ? d : 0;
  }

  function viewport() {
    var m = document.querySelector('meta[name=viewport]');
    if (!m) return;
    var c = m.content.replace(/,?\s*viewport-fit=cover/, '');
    m.content = on ? c + ', viewport-fit=cover' : c;
  }

  function css() {
    var g = gap();
    if (!on || !g) return '';
    var w = document.documentElement.clientWidth + g;
    return (
      'html{width:' + w + 'px!important;min-width:' + w + 'px!important;' +
      'overflow-x:clip!important;touch-action:pan-y pinch-zoom!important;overscroll-behavior-x:none!important}' +
      'body{width:100%!important;overflow-x:clip!important}' +
      'html::-webkit-scrollbar{display:none!important}'
    );
  }

  function apply() {
    viewport();
    style.textContent = css();
    if (style.textContent) (document.head || document.documentElement).appendChild(style);
    else style.remove();
    if (button) {
      button.style.opacity = on ? '0.55' : '0.25';
      placeButton();
    }
  }

  // ボタンは、右の帯の、空いている所（戻るボタンと「＋」ボタンの間）に置く。帯がないときは、左の端
  function placeButton() {
    var g = gap();
    button.style.left = (g ? document.documentElement.clientWidth + g - 36 - 14 : 8) + 'px';
  }

  function makeButton() {
    button = document.createElement('div');
    button.textContent = '⇔';
    button.style.cssText =
      'position:fixed;left:8px;top:70%;width:36px;height:36px;border-radius:18px;' +
      'background:#000;color:#fff;font:18px/36px sans-serif;text-align:center;' +
      'z-index:2147483647;user-select:none;-webkit-user-select:none';
    button.addEventListener('click', function () {
      on = !on;
      localStorage.setItem(KEY, on ? 'on' : 'off');
      // 広げる前の幅で測り直すために、いったん消してから、つけ直す
      style.remove();
      requestAnimationFrame(apply);
    });
    document.documentElement.appendChild(button);
  }

  document.addEventListener('DOMContentLoaded', function () { makeButton(); apply(); });
  window.addEventListener('load', apply);
  window.addEventListener('orientationchange', function () { style.remove(); setTimeout(apply, 300); });
})();
