// 播放条「封面模糊」配色(issue #199):封面缩成 64×64 的静态模糊位图当底,上盖一层表面色遮罩。
// 遮罩浓度按封面逐张算——模糊位图里最差的那个像素叠上遮罩后,条上的文字仍过 4.5:1(留 0.1 余量)。
// 不用 backdrop-filter、不用运行时 filter:位图算一次、按封面地址缓存,条上只是一张静态背景图。
// 读不到像素(跨域被污染、加载失败、没有封面)一律回中性底,条照常可用。

// ── 纯计算(node --test 覆盖) ──
export function channelToLinear(c) {
  const v = c / 255;
  return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
}

export function luminance([r, g, b]) {
  return 0.2126 * channelToLinear(r) + 0.7152 * channelToLinear(g) + 0.0722 * channelToLinear(b);
}

export function contrastRatio(a, b) {
  const x = luminance(a);
  const y = luminance(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

/** 浏览器在 sRGB 编码空间里叠加:结果 = 上层 × a + 下层 × (1 − a)。 */
export function compositeOver(top, alpha, under) {
  return [0, 1, 2].map((k) => top[k] * alpha + under[k] * (1 - alpha));
}

export const VEIL_MIN_CONTRAST = 4.6;

/**
 * 最低遮罩浓度:从基础浓度 lo 起,二分到「每个像素叠上遮罩后,所有墨色都 ≥ min」。
 * pixels 是 RGBA 字节数组;inks 是要保对比度的文字色(sRGB 三元组)。返回 0–1,向上取到 0.01。
 */
export function veilAlphaFor(pixels, base, inks, { lo = 0.7, hi = 0.96, min = VEIL_MIN_CONTRAST } = {}) {
  if (!pixels || !pixels.length || !base || !inks?.length) return lo;
  const ok = (alpha) => {
    for (let i = 0; i < pixels.length; i += 4) {
      const bg = compositeOver(base, alpha, [pixels[i], pixels[i + 1], pixels[i + 2]]);
      for (const ink of inks) if (contrastRatio(ink, bg) < min) return false;
    }
    return true;
  };
  if (ok(lo)) return lo;
  let a = lo;
  let b = hi;
  for (let n = 0; n < 8; n += 1) {
    const mid = (a + b) / 2;
    if (ok(mid)) b = mid; else a = mid;
  }
  return Math.ceil(b * 100) / 100;
}

/** 可分离盒式模糊(原地,RGBA,忽略 alpha 通道并置满)。三遍近似高斯。 */
export function boxBlurRGBA(data, size, radius = 4, passes = 3) {
  const tmp = new Uint8ClampedArray(data.length);
  for (let pass = 0; pass < passes; pass += 1) {
    for (let y = 0; y < size; y += 1) {
      for (let x = 0; x < size; x += 1) {
        for (let k = 0; k < 3; k += 1) {
          let sum = 0;
          for (let dx = -radius; dx <= radius; dx += 1) {
            const xx = Math.min(size - 1, Math.max(0, x + dx));
            sum += data[(y * size + xx) * 4 + k];
          }
          tmp[(y * size + x) * 4 + k] = sum / (radius * 2 + 1);
        }
        tmp[(y * size + x) * 4 + 3] = 255;
      }
    }
    for (let y = 0; y < size; y += 1) {
      for (let x = 0; x < size; x += 1) {
        for (let k = 0; k < 3; k += 1) {
          let sum = 0;
          for (let dy = -radius; dy <= radius; dy += 1) {
            const yy = Math.min(size - 1, Math.max(0, y + dy));
            sum += tmp[(yy * size + x) * 4 + k];
          }
          data[(y * size + x) * 4 + k] = sum / (radius * 2 + 1);
        }
        data[(y * size + x) * 4 + 3] = 255;
      }
    }
  }
  return data;
}

// ── 浏览器侧(canvas) ──
const BITMAP = 64;
const cache = new Map();

const idle = (fn) => {
  if (typeof window !== 'undefined' && typeof window.requestIdleCallback === 'function') {
    window.requestIdleCallback(fn, { timeout: 600 });
  } else {
    setTimeout(fn, 0);
  }
};

function makeCanvas(size) {
  const el = document.createElement('canvas');
  el.width = size;
  el.height = size;
  return el;
}

function blurCover(img) {
  const small = makeCanvas(16);
  small.getContext('2d').drawImage(img, 0, 0, 16, 16);
  const big = makeCanvas(BITMAP);
  const ctx = big.getContext('2d', { willReadFrequently: true });
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(small, 0, 0, BITMAP, BITMAP);
  // 跨域图片会让画布被污染,这里抛 SecurityError → 调用方回中性底
  const image = ctx.getImageData(0, 0, BITMAP, BITMAP);
  boxBlurRGBA(image.data, BITMAP, 4, 3);
  ctx.putImageData(image, 0, 0);
  return { blurUrl: big.toDataURL('image/png'), pixels: image.data };
}

/** 封面地址(已经过媒体库代理)→ { blurUrl, pixels } | null。空闲时算,按地址缓存。 */
export function loadCoverTone(src) {
  if (!src || typeof document === 'undefined') return Promise.resolve(null);
  if (cache.has(src)) return cache.get(src);
  const job = new Promise((resolve) => {
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => idle(() => {
      try {
        resolve(blurCover(img));
      } catch {
        resolve(null);
      }
    });
    img.onerror = () => resolve(null);
    img.src = src;
  });
  cache.set(src, job);
  return job;
}

let probeCtx = null;
/** 任意 CSS 颜色串 → 叠在 under 上的 sRGB 字节:交给 canvas 换算,不自己解析 color-mix / oklab。 */
export function probeColor(color, under = [255, 255, 255]) {
  if (typeof document === 'undefined') return null;
  if (!probeCtx) probeCtx = makeCanvas(1).getContext('2d', { willReadFrequently: true });
  probeCtx.clearRect(0, 0, 1, 1);
  probeCtx.fillStyle = `rgb(${under[0]}, ${under[1]}, ${under[2]})`;
  probeCtx.fillRect(0, 0, 1, 1);
  probeCtx.fillStyle = '#010203';   // 哨兵:canvas 不认的写法会停在这个颜色上
  probeCtx.fillStyle = color;
  probeCtx.fillRect(0, 0, 1, 1);
  const d = probeCtx.getImageData(0, 0, 1, 1).data;
  if (d[0] === 1 && d[1] === 2 && d[2] === 3) return null;
  return [d[0], d[1], d[2]];
}

/** 读某个元素上自定义属性的计算色(color-mix 嵌套照样解析):借一个隐藏 span 的 color。 */
export function resolveVarColor(host, varName) {
  if (!host) return null;
  const probe = document.createElement('span');
  probe.hidden = true;
  probe.style.color = `var(${varName})`;
  host.appendChild(probe);
  const color = getComputedStyle(probe).color;
  probe.remove();
  return color ? probeColor(color) : null;
}
