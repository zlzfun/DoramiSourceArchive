/* 总览页:把原型按 1440×900 装进框里,只露出要看的那一块(data-crop="x,y,宽,高"),按框宽等比缩放。 */
(() => {
  'use strict';
  const V = '2';
  const FRAME_W = 1440, FRAME_H = 900;
  const shots = Array.from(document.querySelectorAll('.shot'));
  for (const el of shots) {
    const [x, y, w, h] = el.dataset.crop.split(',').map(Number);
    const f = document.createElement('iframe');
    f.loading = 'lazy';
    f.title = el.dataset.src;
    f.width = FRAME_W; f.height = Math.max(FRAME_H, y + h);
    f.src = `${el.dataset.src}&v=${V}`;
    el.appendChild(f);
    el._crop = { x, y, w, h };
  }
  const fit = () => {
    for (const el of shots) {
      const { x, y, w, h } = el._crop;
      const s = el.clientWidth / w;
      el.style.height = `${Math.round(h * s)}px`;
      el.firstElementChild.style.transform = `scale(${s}) translate(${-x}px, ${-y}px)`;
    }
  };
  fit();
  window.addEventListener('resize', fit);
})();
