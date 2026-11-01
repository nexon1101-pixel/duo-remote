// ==UserScript==
// @name         DuoWide-Toggle
// @description  DuoWide を、その場で切り替えるボタン（再読み込みなし）。別のページを開くと、広げる状態に戻る
// @version      2.0
// @match        *://*/*
// @run-at       document-end
// @noframes
// @inject-into  content
// ==/UserScript==
// DuoWide.user.js を、window.__duoWideSwitch を足した版と一緒に使う。
(() => {
  if (window.top !== window.self) return;
  if (!matchMedia('(pointer: coarse)').matches || !/iPhone/.test(navigator.userAgent)) return;

  // 外側の画面か。DuoWide が広げているときは、<html> に data-duowide が付く。そうでなければ、
  // Safari が、右の帯にページを置かせていないかで見る（見えている幅は、ピンチに左右されない値で測る）
  var widened = document.documentElement.hasAttribute('data-duowide');
  if (!widened) {
    var vv = window.visualViewport;
    var shown = vv ? vv.width * vv.scale : document.documentElement.clientWidth;
    var strip = Math.round(screen.width - shown);
    if (strip < 20 || strip > 400) return;
  }

  var on = true; // いま広げているか（このページの間だけ。覚えない）
  var inward = '<path d="M3 12h7M7 8l3.5 4-3.5 4M21 12h-7M17 8l-3.5 4 3.5 4"/>'; // 広げている（押すと、広げない）
  var outward = '<path d="M3 12h18M7 8l-4 4 4 4M17 8l4 4-4 4"/>'; // 広げていない（押すと、広げる）
  var b = document.createElement('div');
  function draw() {
    b.innerHTML = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round" style="display:block;margin:7px">' + (on ? inward : outward) + '</svg>';
  }
  draw();
  b.style.cssText =
    'position:fixed;left:6px;top:calc(88vh - 18px);top:calc(88lvh - 18px);width:36px;height:36px;border-radius:18px;' +
    'box-sizing:border-box;opacity:0.32;color:#fff;background:rgba(28,28,32,0.5);' +
    '-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px);border:1px solid rgba(255,255,255,0.22);' +
    'z-index:2147483647;user-select:none;-webkit-user-select:none;-webkit-tap-highlight-color:transparent;';
  b.addEventListener('click', function () {
    if (typeof window.__duoWideSwitch !== 'function') return; // DuoWide が、足した版でない
    on = !on;
    window.__duoWideSwitch(on);
    draw();
  });
  document.documentElement.appendChild(b);
})();
