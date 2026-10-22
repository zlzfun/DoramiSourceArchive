# 登录页重设计 · 五方向样页(探索稿,待拍板)

2026-10-22 设计探索:在现行「深空剧场」登录页(`../dorami-login-constellation-script.md`)之外,从零做五个方向供挑选。
共用约束与样页契约见 [`BRIEF.md`](BRIEF.md);每个方向的设计说明在各目录 `NOTES.md`。
五个方向由五个子代理(Claude Opus 5.5 · max)各自实现,图片素材由 codex(gpt-6-astra,内置 image_gen)生成。

| 方向 | 目录 | 一句话 | codex 素材 |
|---|---|---|---|
| A 晨刊 Broadsheet | `a-broadsheet/` | 登录 = 翻开今天的头版;表单是一栏「读者回执」 | hedcut 点刻像(正常 / 捂眼) |
| B 信号 Signal | `b-signal/` | 从噪声里听见信号;层叠脊线 + 调谐器,调准时浮出吉祥物浮雕 | 头部深度图 → 高度图 |
| C 像素 Pixel | `c-pixel/` | 哆啦美从夜城背后升起;整幅画面同一像素网格 | 半身像 + 四帧表情精灵 |
| D 透镜 Lens | `d-lens/` | 液态玻璃透镜把失焦的文字折射成清晰 | 凹版线刻小像(彩蛋) |
| E 天色 Sky | `e-sky/` | 天色、日月、月相、问候都随读者本地时间 | 写实满月贴图 |

## 查看

**在线**(仓库是 public,经 raw.githack 直接渲染本分支文件,含完整动效):

- 对比页:<https://raw.githack.com/zlzfun/DoramiSourceArchive/feat/issue-214-login-redesign/docs/design/login-redesign/index.html>
- 单个方向把末段换成 `<slug>/index.html`;githack 有几分钟缓存,刚推送的改动稍后才生效

**本机**:起静态服务(样页用相对路径引用 `frontend/public/` 的字体与 logo):

```bash
# 在仓库根(或本分支 worktree 根)执行
python3 -m http.server 5191 --bind 127.0.0.1
```

- 对比页:<http://127.0.0.1:5191/docs/design/login-redesign/index.html>
- 单个方向:`…/login-redesign/<slug>/index.html`;左下角样页控制条可重播开场、切动效档位、预演登录中 / 出错 / 成功
- URL 参数:`motion=reduce` · `intro=skip` · `state=loading|error|success` · `kit=0`;E 另有 `hour=6.5` / `seed=3`,D 另有 `gl=0`(降级路径)
- 演示账号 `admin` / `admin` 走成功路径,其它组合走出错路径
- 重拍截图:`.venv/bin/python docs/design/login-redesign/shared/shoot.py <slug>`

## 入库前须知

- `shots/` 已转 JPEG(q84,合计约 8 MB);拍板后只保留选中方向的关键截图。
- 三个以上方向的正文用到 Dorami Serif,首访字体分片体量要在实现阶段做登录页专用子集。
- AI 生成素材(hedcut、精灵、线刻、月面)正式启用前需确认素材口径;C 的精灵需像素画师精修。
