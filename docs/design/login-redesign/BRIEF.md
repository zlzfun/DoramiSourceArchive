# 登录页重设计 · 五方向样页 共用任务书

> 2026-10-22 设计探索。用户原话:「重新设计我的登录页;可以自由发挥……总体上我希望它能合理应用动效,呈现出高级感与设计感,让用户印象深刻。先生成 5 个不同方向的样页,我将在其中进行挑选。」
> 本文件是五个方向共用的约束与工具;每个方向的创意简报在各自任务里。

## 1. 产品与品牌

- **哆啦美 · Dorami**:自托管 AI 资讯阅读器。读者每天在这里读个人早报、公共日报、订阅的资讯源、播客、社交流,可以让 AI 翻译与问答。登录后进入阅读器;`admin` 另有管理面。登录页是读者每天最先看到的画面。
- **吉祥物**:像素画风的哆啦美(红蝴蝶结、黄色头罩、右眼戴一枚紫色全息目镜,深紫底 + 像素星点)。
  - `frontend/public/brand/dorami-logo-512.png`(另有 256.jpg / 128 / 48 / 32 / 16)
  - `frontend/public/brand/dorami-logo-cover-eyes-256.jpg`(双手捂眼,另有 -128.png)——现行登录页的**蒙眼彩蛋**:密码框聚焦时 logo 换成捂眼图。新方向请保留「密码聚焦时有一个识趣的隐私动作」这条品牌记忆,形式可以重新发明。
- **品牌色**:墨靛 `#5252c5`(亮端 `#6f6bd7`,深端 `#4745b3`);界面整体是「墨色 + 单宝石色」。方向可以重定配色,但要说明它与品牌靛的关系。
- **字体(全部自托管,禁止外网字体域名)**:
  - 标题宋体 `Dorami Serif`(思源宋体,600 / 900):`<link rel="stylesheet" href="../../../../frontend/public/fonts/dorami-serif-v1/dorami-serif.css">`
  - 西文衬线 `Dorami Latin Serif`(Source Serif 4,600 / 900 / 400 斜体):`../../../../frontend/public/fonts/dorami-latin-v1/dorami-latin.css`
  - 界面黑体走系统栈:`-apple-system, BlinkMacSystemFont, "Segoe UI Variable Text", "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif`
  - 想引入新字体:只能是 OFL 等可自托管许可,woff2 下载进本方向目录,并在 NOTES 写明体量代价。
- **现行登录页**(v3.13「深空剧场」):近黑深空 + 三维点云星座(鼠标偏航 / 推轨)+ 左题词右玻璃卡 + 三幕演出。完整剧本 `docs/design/dorami-login-constellation-script.md`。**新方向必须与它明显不同**:不要再做三维点云 / 星座连线 / 玻璃拟态浮卡 + 描边流光这一套。

## 2. 硬约束

1. **样页贴合平台现实**:登录前平台只开放 `/api/auth/*` 与 `/api/health`,**没有任何未登录可读的内容数据**。所以样页里不能出现看起来像真实数据的东西——不要编造新闻标题、「今日 1,284 条」之类的计数、订阅数、在线人数。氛围文字用品牌 / 产品能力语句(早报、翻译、问答、播客……)或纯装饰字形。若方向确实想展示实时内容,必须在页面上明标「待实现 · 需新增公开端点」并写进 NOTES,默认不要这样做。客户端本地能拿到的(本地时间、日期、星期、时区、屏幕尺寸)可以真用。
2. **读者面文案纪律**:不出现「归档 / 采集 / 分发 / 层 / 归档中枢」等内部架构词;口径用「兴趣」不用「关注」。登录字段叫「账号」「密码」,按钮「登录」。后端错误文案原样是「账号或密码错误」。
3. **字体纪律**:字重只用 400 / 600(展示性大标题可用 900);字号下限 12px;中文展示字用 Dorami Serif。
4. **动效纪律**:
   - 动效档位读 `<html data-motion="full|reduce">`(由共用控制条设置,监听 `sample:motion` 事件随时切换),**不要写 `prefers-reduced-motion` 媒体查询**(应用内开关是单一事实来源;登录页品牌演出本身是豁免项,但档位开关照样要尊重)。减弱档要是一个**完成度同样高的静态版本**,不是坏掉的半成品。
   - 开场要可被任意点击 / 按键跳过(忽略 `[data-sample-kit]` 内的事件),并且可通过 `LoginSample.replay()` 重播。
   - 性能:MacBook 上 60fps;DPR 封顶 2;`document.hidden` 时暂停;没有交互时 CPU 不该持续高占用(环境动效可低帧率或停住)。优先原生 Canvas 2D / WebGL / CSS / SVG,**避免引入重型库**(真实实现是 React + Vite,登录页包体要轻);若确需库,用固定版本 CDN 并在 NOTES 写明体量与理由。
5. **可用性**:`<label>`、`autocomplete="username"` / `"current-password"`、回车提交、可见焦点环、错误 `aria-live`、错误槽固定高度不跳版、对比度 ≥ 4.5:1(输入与正文)。
6. **两种视口都要成立**:桌面 1440×900(也照顾 1280 与 1920)、手机 390×844(单列、键盘弹起不遮挡、重效果降级)。
7. **Windows**:只需在 NOTES 里做结构性的理论确认(引擎、合成属性、字体回退、DPR 取整等),不必实测。
8. **只在自己的目录里写文件**:`docs/design/login-redesign/<slug>/`。不改仓库其它任何文件,不 `git commit`。

## 3. 样页契约(必须实现)

- 文件:`docs/design/login-redesign/<slug>/index.html`(可拆 css / js / 素材,都放本目录)。
- `<head>` 里先同步引入共用控制条:`<script src="../shared/sample-kit.js"></script>`(它负责「重播 / 动效档位 / 状态切换 / 返回索引」按钮与 URL 参数,别自己再造一套;方向专属的样页按钮挂 `LoginSample.extraControls`)。
- 页面脚本注册:

  ```js
  window.LoginSample = {
    replay() {},          // 从头重播开场
    skipIntro() {},       // 立即到开场完成态
    setState(name) {},    // 'idle' | 'loading' | 'error' | 'success'
    extraControls: [],    // 可选 [{ label, run }]
  };
  ```
- 表单演示语义:账号 `admin` + 密码 `admin` → 「登录中」约 900ms → 成功;其它组合 → 「登录中」约 900ms → 错误「账号或密码错误」;空字段提交 → 错误「请输入账号和密码」。`setState` 直接切到对应状态(不需要填表)。
- **成功态** = 一段「交棒给应用」的离场动效,最后停在一个中性的落幅(例如淡到阅读器底色,角落一行小字「样页到此 · 实际会进入阅读器」),不要真的跳转。
- 截图自检:静态服务已在 `http://127.0.0.1:5191/`(根 = worktree 根)。完成后跑
  `.venv/bin/python docs/design/login-redesign/shared/shoot.py <slug>`
  (在 worktree 根执行;可用 `--film 300,900,...` 调开场取帧时刻)。必须 **errors: 0**,并逐张看截图自查;不满意就改,直到你愿意把它拿给一位挑剔的设计总监看。

## 4. 交付:`<slug>/NOTES.md`(中文,精炼,≤ 120 行)

1. 一句话概念 + 为什么适合哆啦美
2. 视觉系统:配色(给值)、字体、版式、与品牌靛 / 吉祥物的关系
3. 动效分镜:开场时间轴(ms)、常驻环境动效、交互反馈(聚焦 / 输入 / 密码隐私动作 / 错误 / 登录中 / 成功离场)
4. 减弱档长什么样;手机端怎么降级
5. 技术实现与代价:用了什么(Canvas / WebGL / SVG / CSS),新增素材与字体体量,落到 React 的大致工作量与风险
6. 素材来源:codex 生成的图片写清 prompt 摘要与文件名;参考过的案例链接(2–4 个即可)
7. Windows 理论确认
8. 已知不足 / 待用户拍板的点

## 5. 可用资源

- **联网找灵感**:可用 WebSearch / WebFetch 看 Awwwards、Godly、Codrops、Siteinspire 等案例,别照抄,吸收手法即可。
- **codex 生成图片素材**(内置 image_gen,不需要 API key):在本方向目录里执行
  ```bash
  codex exec -m gpt-6-astra -c model_reasoning_effort=ultra --sandbox workspace-write --skip-git-repo-check \
    -C <本方向目录的绝对路径> \
    "用内置 image_gen 工具生成……(详细描述:画风、构图、尺寸、透明底与否);可先用 view_image 看参考图 <绝对路径>;\
     生成后把最终图片从 \$CODEX_HOME/generated_images/... 复制到当前目录 assets/<文件名>.png,只做这件事,不要改其它文件。"
  ```
  - 参考图(如吉祥物)给绝对路径,让 codex 先 `view_image` 再以它为参考生成,保持角色一致。
  - 单张图可能要几分钟;可以 `run_in_background` 并行生成几张,同时先写代码。
  - 失败一次可重试一次;仍失败就改用代码原生画法(Canvas / SVG / CSS)并在 NOTES 说明。生成结果要亲自看过(Read 图片)再决定用不用;不好看就不用。
  - 素材体量要克制(登录页首屏),必要时压缩(`sips` / `cwebp` 如可用)。
