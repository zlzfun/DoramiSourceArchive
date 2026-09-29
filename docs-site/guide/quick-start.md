# 快速开始

在自己电脑上从零跑起哆啦美，直到看到第一份早报。

- **不配大模型**：大约十分钟可以走完第 1–6 步，看到一份「降级生成」的早报（列出订阅源的最新更新，没有分数）。
- **配上大模型**：再走第 7 步，新采集的文章会被打分，早报才按新闻价值精选。

这是本机开发方式（后端热重载 + 前端开发服务器）。要在服务器上长期运行，用 Docker 部署，见 [Docker 部署手册](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/deploy-docker.md)；装不了 Docker 的机器见[裸机部署手册](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/deploy-baremetal.md)。

## 准备

| 需要 | 版本 | 用途 |
|---|---|---|
| Git | 任意 | 取代码 |
| Python | 3.12（最低 3.10） | 后端 |
| [uv](https://docs.astral.sh/uv/) | 较新版本 | 安装后端依赖 |
| Node.js | 20.19 以上或 22.12 以上 | 前端（Vite 8 的要求） |
| 外网 | — | 采集内容需要访问各来源网站 |

数据全部落在仓库的 `data/` 目录（SQLite），不需要数据库或其他外部服务。

## 1. 取代码

```bash
git clone https://github.com/zlzfun/DoramiSourceArchive.git
cd DoramiSourceArchive
```

## 2. 准备配置文件

```bash
cp config/backend.example.ini config/backend.ini
```

打开 `config/backend.ini`，改两处：

```ini
[auth]
secret = 换成一串足够长的随机字符

[taxonomy]
deployment = authority
```

- `secret` 用来签发登录会话和订阅令牌。本机不改也能启动，只会在日志里告警；改了以后别再变，否则已发的会话和令牌全部失效。
- `deployment = authority` 让本机安装仓库自带的标签目录。示例里的默认值 `manual` 什么都不装，这时兴趣选择和历史文章回填都用不了。背景见 [标签目录上线手册](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/taxonomy-v1-deployment.md)。

其余各节保持默认即可。每一节的含义见 [配置说明](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/configuration.md)。

## 3. 启动后端

```bash
uv sync
.venv/bin/python src/main.py
```

第一次启动会依次看到：一长串数据库迁移日志、`已自动生成根管理员 admin/admin`、`已安装 37 个默认播客采集节点`，最后是 `Uvicorn running on http://127.0.0.1:8088`。接口文档在 `http://127.0.0.1:8088/docs`。

::: tip 为什么不是 `python src/main.py`
`uv sync` 把依赖装进仓库里的 `.venv`。没有激活这个虚拟环境时，`python` 指向系统 Python，找不到依赖。用 `.venv/bin/python`，或者先 `source .venv/bin/activate`。
:::

## 4. 启动前端

另开一个终端：

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 `http://localhost:5173`。前端开发服务器会把 `/api` 请求转给 8088 端口的后端。

::: tip 打不开时
Vite 监听的是 `localhost`。在部分 macOS 上 `localhost` 解析到 IPv6，这时 `http://127.0.0.1:5173` 打不开，用 `http://localhost:5173` 即可。
:::

## 5. 登录并改密码

用 `admin` / `admin` 登录。管理员登录后先进入管理台（左侧一列图标：台账、节点、运行、日报、运维……）。

登录后立刻改密码：左下角「设置」→「账户」。

## 6. 采集内容，看到第一份早报

### 6.1 采一批文章

管理员账号首次登录时会自动订阅 9 个默认来源（OpenAI、Anthropic、Google DeepMind、量子位、IT之家、新智元、The Decoder、X 上的 OpenAI 账号，以及站内的 AI 日报）。**但新库里只有一个播客采集任务，这些来源不会自己开始采集**，需要手动触发一次：

- **只想先看看**：进入「节点」（节点管理），选中「量子位」「The Decoder」等来源，点「立即运行」。每个来源几秒钟就能采到十几篇。
- **想让它每天自动采**：仓库带了一个脚本，一次建好「每日全量采集」任务（覆盖所有内置来源，每天 07:10 运行）：

  ```bash
  PYTHONPATH=src .venv/bin/python scripts/ensure_daily_collection_job.py
  ```

  先让后端启动过一次（它负责建库和迁移），再跑这个脚本。脚本直接写数据库，正在运行的后端不会察觉，跑完后重启一次后端。之后在「运行」（任务与运行）里能看到这个任务的下次运行时间，也可以点「运行」立即执行一次；全部来源跑完需要几分钟。

### 6.2 打开早报

个人早报和文章分析在新库里默认都是关闭的，关闭时阅读器里没有「早报」入口。

1. 进入「运维」（运维管理）→「分析与标签」→「分析链路」。
2. 打开「文章分析」和「个人早报」两个开关。
3. 点左下角「阅读器」切到阅读器，第一个入口就是「早报」。

这时还没有配置大模型，文章没有分数，早报会标注「降级生成」：顶部写着「今天没有达到入选标准的内容」，下面列出订阅源的最新更新，不计入正式精选。看到这一页，说明采集、订阅和早报编排都已经通了。

## 7. 接上大模型，得到正式早报

::: warning 本节未经实跑
第 1–6 步都在一台干净环境里照做过；本节需要真实的大模型凭据，写作时没有跑通，内容依据界面和仓库文档整理。遇到对不上的地方，欢迎在 [issue #89](https://github.com/zlzfun/DoramiSourceArchive/issues/89) 反馈。
:::

1. **填模型**：在管理台（不是阅读器）左下角点「设置」→「管理」→「凭据」，在大模型卡片里填 `base_url`、`api_key`、`model`，先测试再保存。任何 OpenAI 兼容端点都可以（DeepSeek、Kimi、智谱、通义、OpenRouter、Ollama、vLLM 等）。也可以用环境变量 `DORAMI_LLM_BASE_URL` / `DORAMI_LLM_API_KEY` / `DORAMI_LLM_MODEL` 注入。
2. **发布标签目录**：「运维」→「分析与标签」→「标签治理」，点「发布目录 v1」。这一步是人工决定，启动时只安装、不自动发布。
3. **给文章打分**：打开「文章分析」后新采集的文章会自动排队分析。在打开之前已经入库的文章不会自动补上，要在「分析链路」底部的「历史文章完整分析」里发起一次回填。
4. **重新编排**：分析完成后，回到早报页点「重新编排」。之后每天 08:30 自动出新一版。

各环节的细节：评分见 [统一新闻价值评分](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/unified-news-scoring-plan.md)，早报选篇见 [早报的订阅与兴趣](https://github.com/zlzfun/DoramiSourceArchive/blob/main/docs/personal-brief-interest-union.md)。

## 下一步

- 想了解系统怎么分层、数据怎么流：[设计理念](/design/overview)。
- 给团队用：在管理台「运维」→「用户」里建读者账号。读者登录后直接进入阅读器，落地页就是早报。
