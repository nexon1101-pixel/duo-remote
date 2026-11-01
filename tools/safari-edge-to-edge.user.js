// ==UserScript==
// @name         帯の下まで表示
// @description  iPhone Duo の Safari で、右の帯の下まで、ページを広げる。左下の「⇔」で、サイトごとに切り替える
// @version      1.0
// @match        *://*/*
// @run-at       document-start
// ==/UserScript==
(function () {
  var KEY = 'edge2edge:' + location.host;
  var on = localStorage.getItem(KEY) !== 'off';
  var style = document.createElement('style');
  style.textContent =
    'html{overflow-x:clip!important;touch-action:pan-y pinch-zoom!important;overscroll-behavior-x:none!important}' +
    'body{overflow-x:clip!important;max-width:100vw!important}';

  function viewport() {
    var m = document.querySelector('meta[name=viewport]');
    if (!m) {
      m = document.createElement('meta');
      m.name = 'viewport';
      m.content = 'width=device-width, initial-scale=1';
      (document.head || document.documentElement).appendChild(m);
    }
    var c = m.content.replace(/,?\s*viewport-fit=cover/, '');
    m.content = on ? c + ', viewport-fit=cover' : c;
  }

  var button;
  function apply() {
    viewport();
    if (on) (document.head || document.documentElement).appendChild(style);
    else style.remove();
    if (button) button.style.opacity = on ? '0.55' : '0.25';
  }

  function makeButton() {
    button = document.createElement('div');
    button.textContent = '⇔';
    button.style.cssText =
      'position:fixed;left:8px;bottom:90px;width:36px;height:36px;border-radius:18px;' +
      'background:#000;color:#fff;font:18px/36px sans-serif;text-align:center;' +
      'z-index:2147483647;user-select:none;-webkit-user-select:none';
    button.addEventListener('click', function () {
      on = !on;
      localStorage.setItem(KEY, on ? 'on' : 'off');
      apply();
    });
    document.documentElement.appendChild(button);
    apply();
  }

  apply();
  document.addEventListener('DOMContentLoaded', function () { viewport(); makeButton(); });
})();
