"""C · 像素:把 codex 生成的吉祥物帧整理成严格网格的精灵图集(样页工具,非运行时代码)。

流程:按 N×N 网格取每格中心区中位色(AI 像素画网格不均匀,取中心避开糊边)→ 绿底 / 透明底统一抠成透明
→ 以「待机」为母版,只把各帧真正变化的连通区域(眼、嘴、手)移植到母版上,蝴蝶结、头罩等逐格不动
→ 全部帧共享一套 k-means 调色板(默认 32 色)→ 清掉眼区孤立杂点与浅色抗锯齿格 → 横排输出图集。

用法(在本目录外执行均可):
    python build_sprite.py <原图目录> <输出 png> [N=96] [K=32]
原图目录需含 codex-idle / codex-blink / codex-happy / codex-cover2 / codex-oops.png
(1254×1254,纯绿 #00FF00 或透明底;生成 prompt 见 ../NOTES.md)。图集帧序:idle, blink, happy, cover, oops。
"""
import json
import sys

import numpy as np
from PIL import Image
from scipy import ndimage
from scipy.cluster.vq import kmeans2

A = sys.argv[1].rstrip('/') + '/'
OUT_PNG = sys.argv[2]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 96
K = int(sys.argv[4]) if len(sys.argv) > 4 else 32
FRAMES = ['idle', 'blink', 'happy', 'cover2', 'oops']


def load(name):
    im = Image.open(A + f'codex-{name}.png').convert('RGBA')
    a = np.asarray(im).astype(np.float32)
    rgb = a[..., :3]
    al = a[..., 3]
    g = (rgb[..., 1] > 170) & (rgb[..., 0] < 140) & (rgb[..., 2] < 140)
    al = np.where(g, 0, al)
    return rgb, al


def cells(rgb, al):
    H, W, _ = rgb.shape
    xs = np.linspace(0, W, N + 1)
    ys = np.linspace(0, H, N + 1)
    col = np.zeros((N, N, 3), np.float32)
    alpha = np.zeros((N, N), bool)
    for j in range(N):
        y0, y1 = ys[j], ys[j + 1]
        cy0 = int(y0 + (y1 - y0) * 0.25)
        cy1 = max(int(y1 - (y1 - y0) * 0.25), cy0 + 1)
        for i in range(N):
            x0, x1 = xs[i], xs[i + 1]
            cx0 = int(x0 + (x1 - x0) * 0.25)
            cx1 = max(int(x1 - (x1 - x0) * 0.25), cx0 + 1)
            c = rgb[cy0:cy1, cx0:cx1].reshape(-1, 3)
            a = al[cy0:cy1, cx0:cx1].reshape(-1)
            vis = a > 128
            alpha[j, i] = vis.mean() > 0.5
            if vis.any():
                col[j, i] = np.median(c[vis], axis=0)
    return col, alpha


grids = {}
for f in FRAMES:
    rgb, al = load(f)
    grids[f] = cells(rgb, al)
    print('cells', f, grids[f][1].sum())

idle_c, idle_a = grids['idle']

# region transplant
final = {'idle': (idle_c.copy(), idle_a.copy())}
for f in FRAMES[1:]:
    c, a = grids[f]
    d = np.abs(c - idle_c).sum(-1)
    changed = ((d > 120) & a & idle_a) | (a != idle_a)
    # keep only big connected regions (eyes, mouth, hands) — drop 1-cell edge noise
    lab, n = ndimage.label(ndimage.binary_closing(changed, iterations=1))
    sizes = ndimage.sum(changed, lab, range(1, n + 1))
    keep = np.zeros_like(changed)
    for k, s in enumerate(sizes, start=1):
        if s >= 6:
            keep |= lab == k
    region = ndimage.binary_dilation(keep, iterations=(1 if f.startswith('cover') else 2))
    region = ndimage.binary_fill_holes(region)
    oc = idle_c.copy()
    oa = idle_a.copy()
    oc[region] = c[region]
    oa[region] = a[region]
    final[f] = (oc, oa)
    print('transplant', f, int(region.sum()), 'cells')

# shared palette (k-means on all opaque cells of all frames)
samples = np.concatenate([final[f][0][final[f][1]] for f in FRAMES])
np.random.seed(7)
# init with farthest-point-ish sampling for stable results
cent, _ = kmeans2(samples / 255.0, K, minit='++', seed=7, iter=40)
cent = np.clip(cent * 255.0, 0, 255)


def cleanup(f, q):
    """Variant frames: drop isolated dark cells and light AA cells around the left eye / mouth."""
    if f == 'idle':
        return q
    rgb = q[..., :3].astype(int)
    lum = (0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2])
    dark = (lum < 70) & (q[..., 3] > 0)
    white = np.array([252, 252, 251])
    zones = [(x0z, y0z, x1z, y1z) for (x0z, y0z, x1z, y1z) in ZONES.get(f, [])]
    for (zx0, zy0, zx1, zy1) in zones:
        for y in range(zy0, zy1):
            for x in range(zx0, zx1):
                if q[y, x, 3] == 0:
                    continue
                nb = dark[max(0, y - 1):y + 2, max(0, x - 1):x + 2]
                if dark[y, x] and nb.sum() == 1:
                    q[y, x, :3] = white  # isolated dark speck
                    continue
                r, g, b = rgb[y, x]
                mx, mn = max(r, g, b), min(r, g, b)
                lightaa = (not dark[y, x]) and lum[y, x] > 110 and (mx - mn) < 90 and lum[y, x] < 245
                if lightaa and nb.sum() >= 1:
                    q[y, x, :3] = white
    return q


def quant(c, a):
    flat = c.reshape(-1, 3)
    dd = ((flat[:, None, :] - cent[None, :, :]) ** 2).sum(-1)
    idx = dd.argmin(1).reshape(c.shape[:2])
    q = cent[idx].astype(np.uint8)
    out = np.zeros((N, N, 4), np.uint8)
    out[..., :3] = q
    out[..., 3] = np.where(a, 255, 0)
    return out


# cleanup zones (96-grid coords): left eye area outside the visor, mouth area
LEFT_EYE = (13 + 12, 6 + 30, 13 + 37, 6 + 55)
MOUTH = (13 + 24, 6 + 56, 13 + 47, 6 + 67)
ZONES = {'blink': [LEFT_EYE], 'happy': [LEFT_EYE, MOUTH], 'oops': [LEFT_EYE, MOUTH]}  # cover2 不清理

# bounding box (union)
union = np.zeros((N, N), bool)
for f in FRAMES:
    union |= final[f][1]
ys, xs = np.where(union)
x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
w, h = x1 - x0, y1 - y0
print('bbox', x0, y0, w, h)

atlas = Image.new('RGBA', (w * len(FRAMES), h), (0, 0, 0, 0))
for k, f in enumerate(FRAMES):
    q = cleanup(f, quant(*final[f]))
    atlas.paste(Image.fromarray(q[y0:y1, x0:x1], 'RGBA'), (k * w, 0))
atlas.save(OUT_PNG, optimize=True)
json.dump({'w': int(w), 'h': int(h), 'frames': ['idle', 'blink', 'happy', 'cover', 'oops'],
           'palette': ['#%02x%02x%02x' % tuple(int(v) for v in c) for c in cent]},
          open(OUT_PNG.rsplit('.', 1)[0] + '.json', 'w'), indent=1)
print('atlas', OUT_PNG, w * len(FRAMES), 'x', h, 'palette', len(cent))
