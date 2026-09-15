// 早报板块网格的版式规划(issue #74「分值驱动」,样页 docs/design/dorami-brief-grid-quiet.html 策略 F)。
// 网格是 6 等分单元:2 = 小卡(三列之一)、3 = 半宽、4 = ⅔、6 = 通栏。宽度跟着分数走,每一处横跨都有理由:
//   ① 分数 ≥ SOLO_SCORE 的卡独占一行(绝对通栏;9.0 与「9+ 完整渐变」同档——见渐变即值得看,通栏是同一个信号);
//   ② 两卡并排时分差 ≥ PAIR_GAP 用 ⅔ + ⅓,否则各半(一个整数档;复评本身有 ±0.5 摆动,更小的差异不该驱动版式);
//   ③ 余 1 时头卡比第二张高 ≥ PAIR_GAP 才通栏(相对通栏),否则头两行改 2 + 2,每行再按 ② 定;
//   ④ 余 2 时头两张按 ② 配对;整行恒三小卡。
// 前提:板块内条目按分数降序(后端落库即如此,前端分组时再保一次)。两列容器只保留通栏 / 各半——⅓ 只有 246 宽,
// 放不下衬线标题;单列容器(移动壳、窄窗)全部通栏。骨架屏没有分数,固定画「通栏 + 一行小卡」。
// 纯函数,不读 DOM;列数由页面用 ResizeObserver 量报纸面内容宽后经 gridColsFor 给出。

export const GRID_UNITS = 6;
export const SOLO_SCORE = 9.0;
export const PAIR_GAP = 1.0;

// 与旧 auto-fill minmax(320px, 1fr) 的断点一致:三列 ≥ 988,两列 ≥ 654(卡最小宽 320 + 列距 14)
const CARD_MIN = 320;
const CARD_GAP = 14;

export function gridColsFor(contentWidth) {
  const width = Number(contentWidth) || 0;
  if (width >= CARD_MIN * 3 + CARD_GAP * 2) return 3;
  if (width >= CARD_MIN * 2 + CARD_GAP) return 2;
  return 1;
}

// 缺分是「缺席」而不是 0 分(与 utils/analysis.qualityScoreText 同口径):永不通栏、配对时永远是矮的那一方
export function scoreOf(item) {
  const raw = item?.quality_score ?? item?.snapshot?.quality_score;
  if (raw == null || (typeof raw === 'string' && raw.trim() === '')) return null;
  const number = Number(raw);
  return Number.isFinite(number) ? number : null;
}

const asNumber = (value) => (value == null ? -Infinity : value);

export function planSectionSpans(scores, cols = 3, { solo = SOLO_SCORE, gap = PAIR_GAP } = {}) {
  const list = (scores || []).map((value) => {
    if (value == null || (typeof value === 'string' && value.trim() === '')) return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  });
  const total = list.length;
  if (total === 0) return [];
  if (cols <= 1) return list.map(() => GRID_UNITS);

  const rows = [];
  let index = 0;
  while (index < total && asNumber(list[index]) >= solo) {
    rows.push([GRID_UNITS]);
    index += 1;
  }
  let rest = list.slice(index);
  const pair = (a, b) => (cols >= 3 && asNumber(a) - asNumber(b) >= gap ? [4, 2] : [3, 3]);
  if (rest.length === 1) {
    rows.push([GRID_UNITS]);
    rest = [];
  }
  if (cols >= 3) {
    const remainder = rest.length % 3;
    if (rest.length && remainder === 1) {
      if (asNumber(rest[0]) - asNumber(rest[1]) >= gap) {
        rows.push([GRID_UNITS]);
        rest = rest.slice(1);
      } else {
        rows.push(pair(rest[0], rest[1]), pair(rest[2], rest[3]));
        rest = rest.slice(4);
      }
    } else if (rest.length && remainder === 2) {
      rows.push(pair(rest[0], rest[1]));
      rest = rest.slice(2);
    }
    while (rest.length) {
      rows.push([2, 2, 2]);
      rest = rest.slice(3);
    }
  } else {
    if (rest.length % 2 === 1) {
      rows.push([GRID_UNITS]);
      rest = rest.slice(1);
    }
    while (rest.length) {
      rows.push([3, 3]);
      rest = rest.slice(2);
    }
  }
  return rows.flat();
}

// 骨架屏:固定版式(三列 通栏 + 3 小,两列 通栏 + 2 半,单列 两通栏)
export function skeletonSpans(cols = 3) {
  if (cols >= 3) return [GRID_UNITS, 2, 2, 2];
  if (cols === 2) return [GRID_UNITS, 3, 3];
  return [GRID_UNITS, GRID_UNITS];
}
