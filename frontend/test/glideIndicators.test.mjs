// 滑动指示器的关键帧(issue #301 第三批):纯函数部分——首帧在旧位置、末帧落在新位置、朝目标的那条边先到。
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { GLIDE_HOSTS, liquidKeyframes, slideKeyframes } from '../src/utils/glideIndicators.js';

const parse = (kf) => {
  const m = kf.transform.match(/translate\(([-\d.]+)px, ([-\d.]+)px\) scale\(([-\d.]+), ([-\d.]+)\)/);
  return m ? { tx: +m[1], ty: +m[2], sx: +m[3], sy: +m[4] } : null;
};
// 关键帧是相对新矩形 r 的 transform(原点左上):还原出那一帧指示器的左右边
const edges = (kf, r) => { const t = parse(kf); return { left: r.x + t.tx, right: r.x + t.tx + r.w * t.sx, top: r.y + t.ty, bottom: r.y + t.ty + r.h * t.sy }; };

test('前后缘滑动:首帧就是旧矩形,末帧就是新矩形', () => {
  const p = { x: 10, y: 4, w: 60, h: 24 };
  const r = { x: 200, y: 4, w: 44, h: 24 };
  const kf = liquidKeyframes(p, r, [0.2, 0.4]);
  const first = edges(kf[0], r);
  assert.ok(Math.abs(first.left - p.x) < 0.05 && Math.abs(first.right - (p.x + p.w)) < 0.05);
  const last = edges(kf.at(-1), r);
  assert.ok(Math.abs(last.left - r.x) < 0.05 && Math.abs(last.right - (r.x + r.w)) < 0.05);
  assert.equal(kf.length, 29);
});

test('前后缘滑动:往右走时右边先到、左边后跟(中途被拉长),往左走反过来', () => {
  const p = { x: 10, y: 0, w: 60, h: 24 };
  const r = { x: 200, y: 0, w: 60, h: 24 };
  const right = liquidKeyframes(p, r, [0.2, 0.4]);
  const mid = edges(right[4], r);   // 约 80ms:前缘已走过大半,后缘才起步
  const lead = (mid.right - (p.x + p.w)) / (r.x + r.w - (p.x + p.w));
  const trail = (mid.left - p.x) / (r.x - p.x);
  assert.ok(lead > trail + 0.2, `前缘 ${lead.toFixed(2)} 应明显领先后缘 ${trail.toFixed(2)}`);
  assert.ok(mid.right - mid.left > p.w + 20, '中途被拉长');
  const left = liquidKeyframes(r, p, [0.2, 0.4]);
  const back = edges(left[4], p);
  const leadL = (r.x - back.left) / (r.x - p.x);
  const trailL = (r.x + r.w - back.right) / (r.x - p.x);
  assert.ok(leadL > trailL + 0.2, '往左走时左边先到');
});

test('前后缘滑动:纵向同理(导轨、来源栏);宽度不被压成 0', () => {
  const p = { x: 6, y: 60, w: 52, h: 46 };
  const r = { x: 6, y: 260, w: 52, h: 46 };
  const kf = liquidKeyframes(p, r, [0.34, 0.42]);
  const mid = edges(kf[8], r);
  assert.ok(mid.bottom - mid.top > p.h + 10);
  assert.ok(kf.every((k) => parse(k).sx >= 0.05 && parse(k).sy >= 0.05));
});

test('整块滑动(列表选中块):两帧,从旧位置到原位,不带中途拉伸', () => {
  const kf = slideKeyframes({ x: 0, y: 100, w: 360, h: 80 }, { x: 0, y: 300, w: 360, h: 104 });
  assert.equal(kf.length, 2);
  assert.deepEqual(parse(kf[0]), { tx: 0, ty: -200, sx: 1, sy: +(80 / 104).toFixed(4) });
  assert.equal(kf[1].transform, 'none');
});

test('宿主表:每个宿主都有选中项选择器、滑法与外观;滚动容器标了 scroll,导轨标了 front', () => {
  assert.ok(GLIDE_HOSTS.length >= 9);
  for (const h of GLIDE_HOSTS) {
    assert.ok(h.host && h.item, JSON.stringify(h));
    assert.ok(['near', 'far', 'row'].includes(h.kind));
    assert.ok(['seg', 'select', 'line'].includes(h.look));
  }
  assert.ok(GLIDE_HOSTS.filter((h) => h.scroll).every((h) => /scroll/.test(h.host)));
  assert.equal(GLIDE_HOSTS.find((h) => h.host === '.reader-vrail').front, true);
});
