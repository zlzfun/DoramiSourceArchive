# 社交流翻译(issue #84)

状态:实现完成,待交叉检视与用户目检。实现方 Claude Code(`claude-opus-5-5`),检视方 Codex(本地协商式检视,型号以检视时实际为准)。
样页:[`docs/design/social-translate/`](design/social-translate/README.md)。

## 1. 背景

社交媒体容器里的推文几乎都是英文(本机库 94 条里 90 条),文章阅读窗已有「原语言 | ✦ 译为中文」,
推文卡片没有任何翻译入口。X App 自带的翻译(2026-04 起由 Grok 自动翻译)不在公开 API 里:
X API v2 没有翻译端点或译文字段,只有 X 检测的 `lang`;借用 App 内部接口属于非 API 自动化,
按开发者指南会被永久封禁。所以译文走平台自己的 LLM。

## 2. 拍板(2026-10-21)

| 项 | 结论 | 被否的选项 |
|---|---|---|
| 入口 | **卡片底栏**:「✦ 译为中文」是卡片最后一行(媒体与引用推之后),开合都在这一行 | 正文下与「显示更多」同行(首次使用会误以为只翻主推、不管引用推);卡片动作区「译」字钮(桌面悬停才显,常显时单字不够直白);流头整流开关(成本撑不住,留作后续) |
| 译文呈现 | **原文下追加**:原文一字不动,主推译文在正文下方细线隔开、带「✦ 译文」标签,引用推译文在引用卡内 | 替换原文(X 展示规范要求作者名下面那一行显示推文原文,按字面读有被认定违规的空间;规范没有提翻译,属灰色地带) |
| 计量 | 一次点击 = 一次模型调用(主推 + 引用推合并成一次 JSON 调用),计入读者 `translate` 日额度;收起再展开读缓存,不扣 | — |

## 3. 翻什么、哪些推文出入口

- 主推:转推取原推全文 `reposted.text`(顶层 `content` 是「RT @xxx: …」截断形式);否则取 `content`。
- 引用推:`quoted.text`,和主推一次翻完;推文标题不翻(只是首行截断,卡片上不显示)。
- 判定(后端 `reader_ai.social_text_needs_translation` 与前端 `utils/socialTranslate.js` 同一规则,两侧测试同一组用例):
  去掉链接后没有任何字母(纯表情 / 纯链接)→ 不译;`lang` 以 `zh` 开头或属于 X 的无语言码
  (`zxx` / `art` / `qme` / `qam` / `qht` / `qst` / `qct`)→ 不译;其它明确语言码 → 译;
  `und` 或缺失 → 退回 `looks_chinese` 启发式。
- 转推的语言取 `reposted.lang`,缺失时用顶层 `lang`;引用推取 `quoted.lang`,缺失时走启发式。
  规范化 `_normalized_reference` 本波补存内嵌推文的 `lang`(采集时已请求 `tweet.fields=lang`,
  expansions 带回的引用推同样有);存量可用管理员既有的社交 metadata 回填(只读本地 `raw_data`)补齐,不回填也只是走启发式。
- 门控与文章翻译一致:AI Beta 全局总闸 ∧ 账户开关 ∧ 模型已配置,任一不满足整个入口不出现;
  隐藏源 / 未订阅私有源 / 不可外送源的拒绝口径同 `/ai/translate`。

## 4. 后端

- `POST /api/reader/ai/social/{article_id}/translate` → `{status, text, quoted, cached}`;不需要翻译的一侧为 `null`。
  非社交推文 400、没有可译内容 400、结构校验失败 502、额度 429(只在缓存未命中时判)。
- `reader_ai.translate_social_post`:沿用 #201 的 `plan_translation`(链接等保护成占位符、逐段核对)与
  `require_complete=True`;主推和引用推的片段按 3,500 字符预算装进 JSON 批次,一批一次调用
  (常规推文一批;超长 note tweet 才会多批)。提示词 `SOCIAL_TRANSLATE_SYSTEM_PROMPT`:@用户名、#话题、表情、占位符原样保留。
- 缓存:`extensions_json.translation_zh_social = {text, quoted, fp, v}`,`fp` 是两段原文的指纹,任一变化即重译。
  独立于文章的 `translation_zh`(后者被文章旁白读取,不混用)。写回复用 `_save_translation` 的合并 + compare-and-set,
  并在写入前用最新 `extensions_json` 复核原文指纹(新增 `verify_ext` 钩子),原文变了返回 409 不落缓存。

## 5. 前端

- 状态提升在 `useReaderState`(`socialTranslations` + `toggleSocialTranslation`),桌面 `ReaderTab` 与移动 `MobileReader` 共用;
  右键 / 长按菜单(`buildSocialMenuItems`)第一组加「译为中文 / 收起译文」。
- `SocialFlow` / `SocialPost`:底栏 `.social-tr-row` 恒为卡片最后一个元素——初始「✦ 译为中文」、进行中「翻译中…」、
  完成「✦ 已译为中文 · 收起」、失败「翻译失败 重试」、额度用尽只给说明;主推译文 `.social-tr` 在「显示更多」之后、
  媒体之前;引用推译文 `.social-quote-tr` 在引用卡内(引用卡整卡是原推链接,卡内不放按钮)。
- 样式并入 `index.css` 推文卡段;触屏点击区在 `mobile.css` 上下各扩 6px(负外边距抵消,不改高度)。

## 6. 不做 / 后续

- 流头整流开关(样页方案 C):开着时每条推一次调用,现行每人每天 50 次撑不住;要做先解决批量合并或单独额度。记入 backlog。
- 不自动翻译、不在列表接口里带回译文(收起后重新打开走服务端缓存,一次 HTTP、零模型调用)。
- 译文缓存在 `extensions_json` 里,和文章的 `translation_zh` 一样会随 feed 交付的 `metadata.extensions` 与归档同步一起外传;
  这是既有翻译缓存的同一口径,本波不另做过滤。

## 7. 自查

- 后端 `tests/test_social_translate.py` 30 项(含与前端共用的 19 条判定用例),`test_x_timeline` 期望值补 `lang`;
  前端 `test/socialTranslate.test.mjs` 4 项;lint / 前端 117 项单测 / build 通过。
- 沙箱端到端(真实库副本 + 本地 OpenAI 兼容模拟服务,不接真模型)20 项全过,截图覆盖桌面暗 / 亮与手机:
  底栏恒为卡片最后一个元素、纯链接转推无底栏、一次请求出主推与引用推译文且链接原样、收起再开不再请求、右键开合、
  只译引用推、模型报错与输出截断都是「翻译失败 重试」且不落缓存、额度打满只给说明、AI 未开启账号无底栏与菜单项、
  手机点击区 32px、长按动作单含「译为中文」。
- 没有用真实模型验证译文质量,也没验证各家 OpenAI 兼容端点对 JSON 模式的兼容性(client 遇 400 会去掉 `response_format`
  重试一次;解析失败按 502 不落缓存)。详细记录见 `docs/version-history.md` 本波一节。

## 8. 检视记录

(待检视后补)
