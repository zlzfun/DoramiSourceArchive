# 精选播客目录（欧研观澜样本）

## 结论

内部分析列出的 36 个节目可以复用 Dorami 已有的 `SourceConfigRecord(source_type=podcast)`
与 `generic_podcast_rss` 接入，不需要为 Spotify、Simplecast、Libsyn、Fireside、Transistor、
Megaphone、小宇宙分发源等平台各写一个抓取器。2026-09-03 的真实网络验证结果：

- 35 个 feed 返回可解析的 RSS/Atom，且至少一个单集含音频 enclosure；
- `Voices from DARPA` 的 Apple 目录仍指向 Blubrry feed，但当前网络 TLS 握手 EOF，标为
  `blocked`，默认导入会跳过；
- `Inside AI` 的 feed 可用，但最新单集停在 2025-01，保留在扩展档观察；
- `Latent Space`、`20VC` 等历史 feed 接近或超过 10 MiB，目录参数统一设置 20 MiB
  响应上限和每轮 20 集，避免无界响应与首次全量处理。

目录单一事实来源为 `src/services/podcast_catalog.py`。每个条目记录稳定 source ID、feed、
发布方、语言、主题、首发档位、验证状态和最近单集日期。这里仅登记公开分发元数据，不复制
内部平台的 AI 摘要或逐字稿。

## 导入与上线

应用启动时会幂等安装 35 个验证通过的条目，使全新部署的节点管理不再缺少 Podcast；
这些节点保持停用，不会自动订阅给任何用户，也不会触发 RSS 抓取、ASR 或 TTS。重复启动只补充
目录新增项，不覆盖管理员对已有节点的名称、配置和启停选择；阻断项仍不安装。

命令行工具用于预览目录或执行选择性运维。默认命令只预览，不写库：

```bash
PYTHONPATH=src uv run python scripts/import_podcast_catalog.py
```

小批激活做内容质量观察：

```bash
PYTHONPATH=src uv run python scripts/import_podcast_catalog.py \
  --apply --activate \
  --source podcast_latent_space \
  --source podcast_semianalysis_weekly
```

手动补齐所有验证通过的条目但暂不启用：

```bash
PYTHONPATH=src uv run python scripts/import_podcast_catalog.py --apply
```

管理员 API 提供相同能力：

- `GET /api/source-configs/podcast-catalog`：目录、验证与当前安装状态；
- `POST /api/source-configs/podcast-catalog/import`：按 ID 幂等导入。

自动安装和手动导入的安全默认值都是 `activate=false`、`update_existing=false`、
`include_blocked=false`。更新已有条目不会把已启用源悄悄停用。所有新源标记为
`incubating`，需按源策展策略手工启用并抓取，检查标题、日期、show notes、封面、时长与
重复率后再扩大采集。启用共享 Podcast 后，服务会按该源的 `fetch_interval_minutes` 注册
独立定时任务，并按稳定的 source-id 散列错开首轮执行，避免批量启用或重启时集中请求；
启停、修改间隔或删除会即时刷新调度，任务真正执行前还会再次核对启用状态。节点运行史
可直接用逻辑播客源 ID 查询，即使底层多个节目共用 `generic_podcast_rss` 执行器也不会显示为空。
该定时任务只更新 feed/单集元数据，不调用 ASR 或 TTS。

## 与内部博客 RSS 的边界

内部导入的优质博客继续使用 `source_type=rss` / `generic_rss`，播客固定使用
`source_type=podcast` / `generic_podcast_rss`。即使两者来自同一域名，也必须使用不同
source ID；前者产生 `rss_article`，后者产生 `podcast_episode`，从而保持阅读器容器、
播放器、长音频处理资格和后续成本策略相互隔离。

## 权利与衍生内容

公开 RSS 可以支撑节目发现、原音频链接播放与 show notes 聚合，但不自动等于允许重新托管、
全文转录、翻译、改写或发布合成音频。长播客精华仍按 `docs/podcast-wave-plan.md` 的 rights
gate 执行：优先发布方 transcript；没有明确衍生授权时，只做登录态内部辅助或链接回原节目，
不发布新的公开音频/RSS。
