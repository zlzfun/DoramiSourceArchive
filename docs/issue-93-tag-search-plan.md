# Issue #93：标签检索整合与检视

## 决策

采用 Codex `b9cef376` 为主体，保留一份标签检索意图及共享阅读状态。
吸收 Claude `17ca95e0` 的 1px 描边修正，以及 CodeArts `6cbe9f0f` 将搜索清理移出 React 状态 updater 的处理。补充持久化浏览器回归，避免混合四套检索逻辑。
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

- 正式走既有 `tag_ids`，非正式走 `display_tag`，不写关注/订阅。无有效正式 ID 不伪装成关键词搜索，旧 `tags` 投影兼容。
- 标签点击立即生效，手输词保留300ms防抖，清空/导航立即解除标签和关键词条件。
- 桌面/移动共用 `useReaderState`，首拉/分页/分析轮询共用过滤器拼装。
- 管理抽屉/弹窗共用回调；手输提交、来源导航清除旧的两个标签参数。
- 权限、原容器、收藏/未读及已选兴趣轴保持原语义。
- 后端只改排序键优先级，不改模型、迁移或相关度门槛；六枚上限、主标签优先保持。

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
检查相对最新主线完整diff，包括新增未跟踪测试。收到意见后记录结论和处理。
