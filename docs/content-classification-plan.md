# 内容分类与双评分(issue #178)方案

> 状态:**第一波实施中**(2026-10-12 动工,分支 `feat/issue-178-content-kind`)。
> 结论与可视化见 [issue #178](https://github.com/zlzfun/DoramiSourceArchive/issues/178)
> 和样页 [`docs/design/dorami-classification-scores-quiet.html`](./design/dorami-classification-scores-quiet.html)
> (仓库公开,可经 htmlpreview 免登录打开)。#168 的历史探索不作为本方案的前提。

## 0. 拍板(2026-10-12)

1. **定位**扩展为「资讯 + 值得深读 / 收听」。
2. **双评分**:每条内容都打两个分。**新闻价值**(这件事有多重要)保留现有 `quality_score`,定义、数值、门槛不变;
   **内容价值**(值不值得花时间读或听)新增,标准随内容类型变化,所有类型都有领域相关性上限(与 AI 无关最高 4.9),
   播客现有附加尺迁入内容价值。这推翻了 issue #22(2026-09-07)「只留一把尺子」的拍板,理由写在 issue #178。
3. **条目级内容类型**:资讯 / 论文 / 教程 / 观点 / 版本更新 / 项目 / 汇编;体裁 `content_genre` 拆成「内容类型 + 事件类型」。
4. **公共日报**继续收少量论文:论文名额仍 3 篇,改为从内容类型为论文的条目里按内容价值挑选(第三波)。
5. **个人早报**新增「值得读」「值得听」(第三波)。
6. **阅读器**:每条显示一个主分(内容类型决定默认维度,新闻价值 ≥ 8.5 时以新闻价值为主);评分卡里 AI 速读不变,
   分数下方小字标当前维度,点分数切换维度,悬停分数显示该分数的评分依据(第二波)。条目挂类型签,列表头加类型筛选(第一波)。
7. **左轨撤销「动态」并入「文章」**,版本更新用紧凑行(第一波)。
8. **维度收口**:`content_type` 只是数据结构、不再当分类展示;`category` 拆出准入状态后退役;源级 `content_tags` 退役;
   标签与映射表合一。对外说法:文章 / 播客 / 社交是「呈现形态」,「类型」专指内容类型。

## 1. 分波

| 波次 | 范围 | 读者可见变化 |
|---|---|---|
| **第一波**(本分支) | 内容类型 / 事件类型(读取时推导,不改提示词、不加列);「动态」并入「文章」;类型签、类型筛选、紧凑行;词表收口 | 类型签、筛选带、左轨三个容器、发现页类型签 |
| 第一波补(下一个 PR) | `category` 拆出准入状态后退役;源级 `content_tags` 退役;停止把 category 写进条目扩展标签 | 无(管理面「观察」徽记换数据来源) |
| 第二波 | 内容价值:新分析提示词一次产出内容类型(LLM 补判)与两个分;黄金集按类型扩充;播客尺子迁入;评分卡新交互 | 主分与评分卡 |
| 第三波 | 分发:早报「值得读 / 值得听」;日报论文名额改按内容价值;播客门控改读内容价值;新闻尺子移除「非新闻体裁」条款 | 早报新分区 |

第一波拆出「第一波补」的原因:`category` 不只是分类,它还决定节点默认是否可见(`focused_curation_for`)、
是每日采集任务的排除条件(`scripts/ensure_daily_collection_job.py`)、会被写进条目扩展标签(经 feed / MCP / 同步外流),
牵涉 69 个抓取器与约 17 个测试夹具;对读者没有直接收益,单独成 PR 便于检视。

## 2. 第一波

### 2.1 词表与推导

单一事实来源 `src/services/content_classification.py`:内容类型 / 事件类型 / 体裁 / 数据结构四张中文名表,
体裁 → (内容类型, 事件类型) 映射,个人早报板块与公共日报分类两张分区映射,以及判定规则。

判定序(`classify_item`):

1. **来源先验**:抓取器类属性 `default_kind`。HF Daily Papers → 论文;8 个 changelog / release notes 与 3 个
   GitHub Release 源 → 版本更新;2 个 HF 模型、1 个 GitHub 仓库、1 个 GitHub 趋势源 → 项目;日报源 → 汇编。
2. **数据结构兜底**:只对注册表之外的历史 / 导入源(`github_release` → 版本更新,模型卡 / 仓库 / 趋势 → 项目,`arxiv` → 论文)。
3. **链接形状**:arXiv / OpenReview / HF papers 链接 → 论文(社交帖、播客、日报不套用,它们的链接指向自身)。
4. **体裁**:`research_paper` → 资讯 + 研究成果(分析看到的是讲研究的内容,不一定是论文原文);`tutorial` → 教程;
   `opinion` → 观点;`aggregation` → 汇编;其余 → 资讯 + 对应事件类型。
5. 都没有(通常是分析未完成)→ 未判定,读者面按资讯处理(不挂签)。

事件类型只对资讯 / 版本更新 / 项目填写。

**为什么读取时推导、不落库**:所有输入(源 id、链接、数据结构、体裁)都已入库并随归档同步到接收节点,两端同一份代码
算出同一结果,零迁移、零回填、同步协议不动;分析版本不变也就不触发重跑。SQL 镜像 `kind_condition` 与 Python 推导共用
规则表,`tests/test_content_classification.py` 用覆盖全部七类的夹具逐条对拍。第二波大模型给出的内容类型落库后,
作为额外输入接进 `classify_item` 即可。

### 2.2 接口

- `GET /api/articles` 列表 / 详情 / 收藏的条目新增 `kind`、`kind_label`、`kind_basis`、`event_type`、`event_label`、
  `content_genre_label`、`content_type_label`(中文名由后端下发,前端不再维护映射表)。
- 新参数 `kind=paper,tutorial`(CSV,非法值 400)与 `kind_facets=true`:附带当前范围(不含 kind 自身)的
  各类型计数 `[{kind, label, count}]`,筛选带的选项来自它(约定 §6「选项来自聚合端点」)。实测真实库副本约多 20ms。
- `GET /api/articles/facets` 的 `content_types` 每项附 `label`(数据结构中文名)。
- `GET /api/reader/sources` 每个源新增 `default_kind` / `default_kind_label`(发现页源卡、日报名单用)。
- 字段名用 `kind` 而非 issue 里写的 `content_kind`:后者已是自定源的源级字段(article|podcast),避免同一接口面二义。

### 2.3 「动态」并入「文章」

- 15 个原 bulletin 源(另 3 个模板)的 `content_shape` 回到默认 article,改设 `default_kind`。
- `api/sources.normalize_shape`:入参 `bulletin` 视同 `article`,其余未知值视为不过滤。用在 `/api/articles`、
  `mark-all-read`、`mark-scope-read`、`favorites` 四个入口——**不能把 bulletin 当未知值忽略**,否则旧客户端的
  `mark-all-read?shape=bulletin` 会把全部订阅(含社交)标读、列表混进社交与播客(测试锁定)。
- 未注册的历史源按数据结构兜底时,版本发布 / 模型卡也归文章容器(此前会落到不存在的容器)。
- **榜单口径不变**:`rankings._source_shapes` 把来源先验为版本更新 / 项目的源标为 excluded,继续不进文章榜与必读榜。
- **批量订阅**:「订阅全部文章源」随形态合并纳入原动态源(前后端同口径),待验收确认。
- 管理面节点目录去掉「动态」页签,计数量词按来源先验(版本更新 / 项目用「条」);日报名单的「· 动态」改为类型名。

### 2.4 读者面

- 条目行:源名后挂内容类型签(`.reader-entry-kind`,沿 `.reader-entry-tag` 细线语法,墨色提一档),资讯不挂;
  与主标签同名时主标签让位。主标签兜底从体裁中文名改为事件类型。
- 紧凑行由内容类型决定(版本更新):`.is-bulletin` 改名 `.is-compact`,与文章混排。
- 筛选带 `.reader-kind-band`:列头下,仅文章容器;单选,再点取消;选项只列计数 > 0 的类型(选中项除外)。
  选中值绑定在当时的作用域上,切容器 / 源 / 轴 / 收藏后自动失效,不先发「旧类型 × 新范围」的请求。
- 按类型筛选时收起「全部标读」:标读按源水位推进,会连同没显示的其它类型一并标掉。
- 阅读窗眉头「源 · ___」:非资讯写内容类型,资讯写事件类型。
- 移动端:底部 Tab 去掉「动态」;筛选带放在列表顶(顶栏须单行,e2e 有约束),横向滑动。
- 发现页:形态筛选 5 档 → 4 档;源卡「动态」签改为来源的默认类型(论文 / 版本更新 / 项目)。

### 2.5 词表收口与有意的偏离

- 前端删除 `utils/contentType.js` 与 `utils/analysis.js` 的体裁中文名表;后端 `GENRE_SECTIONS`、`CLASSIFICATION_FROM_GENRE`
  定义移入词表(取值逐字不变,测试锁定)。
- **公共日报分类未改**(偏离 issue #178「顺带修掉日报与早报对教程、观点分区不一致」):日报 `classification` 被内网神灯
  流水线原样写进 classification 与 type 两个字段,改字符串是对外变更,需要先与神灯侧确认。不一致在词表里注明为有意保留。
- `api/sources.CONTENT_TYPE_CATEGORY`(读者源目录的 `category` 字段)是 `docs/contracts/reader_subscription.md` 记载的字段,未动。
- 文档站「看动态」页改为「看版本更新」(路径不变),配图沿用原示例图,未重拍。

### 2.6 自测

- 后端:新增 `tests/test_content_classification.py`(17 条:词表覆盖全部体裁、判定序、Python ↔ SQL 逐条对拍、
  `kind` 筛选与分面、契约取值不变);更新 `test_reader_shapes` / `test_reader_unread` / `test_reader_favorites_avatar` /
  `test_fetcher_curation` / `test_repository_model_fetcher` / `test_article_facets`。全量 `pytest -n 4`。
- 前端:`test/contentKind.test.mjs`(类型签、紧凑、眉头、筛选选项、旧形态折叠)、`bulkSubscribe.test.mjs` 补旧形态用例;
  lint / build 通过;e2e `mobile_reader.py` 与 `check_mobile_navigation.py` 的「动态」步骤改用播客容器。
- 隔离栈(真实库副本,已清除副本中的模型密钥并停用采集任务):Playwright 亮 / 暗 / 390px 三组,左轨三容器、
  筛选带选项、论文筛选、版本更新紧凑行、眉头、发现页类型签、标读收起均符合预期,无 pageerror。

## 3. 第二波、第三波要点(待动工时细化)

- 第二波提示词一次产出内容类型与两个分;内容价值按类型给可核对的标准(论文看贡献 / 证据 / 开源 / 社区信号如 HF 赞数,
  教程看能否照做,观点与访谈看新事实或新框架与讲述者是否一手);黄金集现有 62 条中论文 8 条、播客 0 条,需按类型扩充。
- 主分例外线、日报论文门槛、早报两个新分区的名额、播客转写 6.0 / 优质 7.5 的新口径均为待定参数(见 issue #178)。
- 移动端没有悬停:评分依据改用长按或点按展开。

## 4. 检视记录

(待本地交叉检视后补写)
