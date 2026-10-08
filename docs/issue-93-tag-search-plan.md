# Issue #93：标签检索整合与检视

## 决策

采用 Codex `b9cef376` 为主体，保留一份标签检索意图及共享阅读状态。
吸收 Claude `17ca95e0` 的 1px 悬停描边修正，以及 CodeArts `6cbe9f0f` 将搜索清理移出 React 状态 updater 的处理。键盘焦点保留 2px 统一焦点环。补充持久化浏览器回归，避免混合四套检索逻辑。
从最新 `origin/main`（`752bcd1`）开始，其相对测评基线只改文档和日期敏感测试，功能相关代码未变。此前用户验收是原交付版本的历史证据，整合版本重新验证。

## 四份最终代码的取舍

| 实现 | 有价值的设计 | 取舍 |
| --- | --- | --- |
| Codex `b9cef376` | 单个 `tagSearch`、共用回调、推导关键词避免 effect 回写竞态；旧投影在截取六枚前稳定分组 | 采用主体及权限/无关注/形态隔离测试 |
| TRAE `ab09b92a` | 两类标签明确分流，后端分组清楚，修复后功能完整 | 两套回调和多份查询状态需更多互斥维护；等价排序不叠加 |
| Claude `17ca95e0` | 共用意图函数、API/浏览器验证较充分；显式描边避免正式按钮默认粗 outline | 取描边；不用缺失正式 ID 转全文检索的降级及多查询状态 |
| CodeArts `6cbe9f0f` | callback 传完整标签；关闭搜索副作用移出 updater | 取后者小修；不用双参数接线、多查询状态及工具专属配置 |

依据是最终代码的状态复杂度、契约一致性、入口复用和维护成本。
首轮速度、CLI 登录、额度和误停不作为生产代码选择标准。

## 契约与状态

- 正式标签点击走 `display_tag_id`，匹配指派记录或读取时解析出的同 ID 展示投影；非正式走既有 `display_tag`，不写标签指派、关注或订阅。原 `tag_ids` 仍只认指派，兴趣/日报的权威边界不变。无有效正式 ID 不伪装成关键词搜索，旧 `tags` 投影兼容。
- 标签点击立即生效，手输词保留300ms防抖，清空/关闭搜索或切换阅读容器时立即解除标签和关键词条件。
- 阅读器源内下钻、返回该容器聚合保留检索条件，沿用原关键词检索的导航语义；与来源/形态过滤相交。
- 桌面/移动共用 `useReaderState`，首拉/分页/分析轮询共用过滤器拼装。
- 管理抽屉/弹窗共用回调；手输提交、外部来源定位清除旧的两个标签参数。
- 权限、原容器、收藏/未读及已选兴趣轴保持原语义。
- 后端调整排序键优先级并增加独立的展示 ID 检索参数，不改模型、迁移或相关度门槛；六枚上限、主标签优先保持。

## 回归

```sh
.venv/bin/python -m pytest tests/test_article_display_tags.py tests/test_reader_interests.py tests/test_analysis_personal_api.py tests/test_mobile_e2e_sandbox.py -q
cd frontend
npm run lint
npm test
node --test src/utils/analysis.test.js
npm run test:e2e -- --flows tags,focus
```

新增 `tags` 流程复用沙箱、真实 FastAPI 和 Vite 构建，只在选择此流程时给合成数据加低相关度正式标签、高置信非正式标签，以及正文提词却无标签的噪声条目。
桌面/移动各检查正式/非正式的顺序、精确 ID、形态、噪声排除及不写关注/订阅。
浏览器计时器25ms后关闭搜索，600ms观察旧词是否复现；后者不是页面加载等待。
`focus` 检查既有控件聚焦样式。相关后端与沙箱测试48项、前端 `npm test` 51项、分析工具测试22项均通过，前端 lint 通过。
本地 Chromium 的 `tags,focus` 流程通过；扩展 `pwa,focus,tags` 也通过：八项标签检查、七项聚焦检查、八项PWA检查，真实 Vite 构建成功；未宣称Safari或真机验证。
默认四流程在既有 `mobile` 的请求审计处失败：无过滤的 `/api/articles` 与 `/api/reader/unread-counts` 被取消而报 `net::ERR_ABORTED`。此前页面操作、阅读/收藏落库与布局负控均通过；后续流程没有执行。
原主仓库 `main`（`474af04`）对照也出现相同失败，候选复核亦相同；原移动E2E、runner/fixture和阅读状态文件在该基线与最新 `752bcd1` 间无差异。作为既有审计问题记录，保留断言，不修改无关移动流程，不宣称默认完整E2E通过。

## 本地交叉检视

实现方：Codex（采用 gpt-6-astra 测评交付，由当前架构评审整合）。
检视方：本机 Claude Code CLI 2.1.284，实际 `glm-5.3[1m]`，只读工具集。
检查相对最新主线完整diff，包括新增未跟踪测试；冻结源码与首次整合提交 `250c813` 一致，仅方案验证备注后补。
首轮结论：通过，无阻塞问题；模型只读静态检视，未独立运行测试。

唯一 P3：管理抽屉/弹窗中标签的 1px 淡灰焦点环覆盖了原全局 2px 焦点环，文字/底色又与静止态一致。接受，未产生分歧。
修复把悬停与键盘焦点规则拆开：悬停保留 1px `--dorami-border-strong`，`:focus-visible` 恢复 2px `--dorami-focus` 及 1px offset；阅读窗后置规则不变。
修复后 lint、构建与真实 `tags,focus` E2E 再次通过。另用实际构建 CSS、Chromium Tab 在管理/阅读两种容器和两类标签共四种样例中检查计算样式，均为 2px solid 且颜色匹配 `--dorami-focus`。此为 CSS 容器夹具验证，不冒充管理端完整UI或人类验收。
同一 Claude Code / GLM-5.3 会话按唯一修复清单复检通过：`p3_status=resolved`、`verdict=pass`、`findings=[]`，不重开全站审查。实际模型、原始只读日志、冻结diff/hash及样式证据保存在本地测评输出目录，评审未改代码或独立跑测试。

阅读器手输防抖期间短暂使用旧词属于基线行为，记观察项而不扩修；默认移动流程的主线审计失败已在回归节记录。

## GitHub Codex 单次检视

按用户本次明确指令，针对 `5cb8de7` 在 PR #169 评论区只发起一次 `@codex review`；本次覆盖仓库默认只走本地交叉检视的约定，不改变后续默认流程。机器人未公开具体模型，不推测模型名。
请求与完成记录：[请求](https://github.com/zlzfun/DoramiSourceArchive/pull/169#issuecomment-6057814192)、[单次完成状态](https://github.com/zlzfun/DoramiSourceArchive/pull/169#issuecomment-6057818366)。

唯一 [P2](https://github.com/zlzfun/DoramiSourceArchive/pull/169#discussion_r4217793348)：读取时通过已归并/激活 Candidate 或 active 名称/别名解析出的正式 chip 没有指派记录；原点击传 `tag_ids`，会漏掉展示该 chip 的文章。接受，无分歧。
已先复现：详情 `tags=[]`、`display_tags` 中有正式 ID，点击查询却只返回另一个有指派的条目，漏掉两篇投影命中的文章。

修复增加只读 `display_tag_id`，在分页/count 前按「指派 EXISTS 或当前展示投影 ID 命中」过滤，再与原权限、形态、来源、兴趣、收藏、未读条件相交。展示解析复用 `load_display_tags`，按500条批量读取快照与旧 evidence，保留治理状态及分面内名称/别名规则；不把展示晋升写成指派，也不扩大原 `tag_ids`、兴趣或日报筛选。
前端保留统一的检索意图，只替换正式 chip 的参数；管理面清理对应条件。新增文章/播客 × 名称/别名/归并/激活/旧 evidence 共10个 API 回归，覆盖同名不同 ID、正文提词负例、权限、分页/count、低相关度及兴趣权威边界。
浏览器合成数据改为一篇有指派、一篇仅展示晋升；桌面/移动直接点击后者的 chip，要求两篇均返回，并确认指派总数仍为1。

修复后相关后端/沙箱58项、前端51项、分析工具22项及lint通过；真实构建后的桌面/移动标签检索与快速清空8项、聚焦7项通过。远端CI随修复提交重新执行，最终状态以PR为准。
遵照用户「只要 review 一次」的要求，不再请求机器人或模型复审；本段记录的是对该单次反馈的修复和测试结果，不声称机器人已复审修复后的代码。
