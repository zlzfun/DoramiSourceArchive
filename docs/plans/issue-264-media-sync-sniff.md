# Archive Sync 媒体流坏图:入缓存只认内容 + 导出端自检 + 接收端单条拒收(issue #264)

> 状态:进行中(2026-11-06 实现与自测完成,Codex 两轮交叉检视通过;生产只读统计待用户授权)

> 实现方:Claude Code(Opus 5.5);检视方:Codex(见 §7)。分支 `feat/issue-264-media-sync-sniff`。
> 上接 #261 双栈沙箱联调:开发副本 → 空库,图片流第 1 页报「同步媒体不是可识别的图片」,之后每次同步都卡在这一页。

## 1. 根因(开发副本实证)

- 16 条 `cached` 媒体全是网易订阅 CDN `dingyue.ws.126.net` 的图,2026-07-20 12:04:00–04 四秒内入库,**共用同一个 194 字节 CAS 文件**:
  内容是文本 `data:image/png;base64,iV…`,响应头却是 `Content-Type: image/png`。
- 本地下载器 `_sniff_image_mime(body[:64], content_type)`:魔数认不出时**信任 `image/*` 响应头**,于是按 PNG 落盘;
  同步接收端 `validate_synced_image` 只认内容(`_sniff_image_mime(body[:512], "")`),拒收 → `install_media_bytes` 抛错 →
  checkpoint 不前进,排在 media 后面的 `podcast_texts` / `podcast_audio` / `source_states` 本轮都跑不到,下轮重放同一页再败。
- 两侧口径不一致才是病根:入缓存集合 = 魔数 ∪ 响应头,同步可接收集合 = 魔数。差集里的行必然卡流。
  开发副本 1759 条 cached 里,靠响应头兜底入缓存的**只有这 16 条坏图**,没有一条合法图片依赖兜底。
- 本机读者侧同样坏:代理把这段文本当 `image/png` 回给浏览器,裂图。

## 2. 方案(三层,任一端单独升级都能解卡)

### 2.1 入缓存只认内容(`media_store`)

- `_sniff_image_mime(head)` 去掉响应头兜底;下载器与浏览器取图都按 `SNIFF_HEAD_BYTES = 512` 嗅探,与接收端同一窗口。
  认不出 → 原有失败负缓存「响应不是图片」(退避重试,读者端 302 回源)。
- 代价:TIFF / JPEG XL / `<!DOCTYPE svg` 开头的 SVG 等魔数表外格式不再入缓存、改走回源;实测零命中,且这些格式本来就过不了同步。
- 检视 R1 补:命中判定 `_restore_cached` 同时核大小(截断副本当缺失、走重下);重下时既有 CAS 文件大小与新字节不符则原子覆写(原先 `if not target.is_file()` 会保留坏文件);
  失败冷却 `_failed_still_cooling` 兼容带时区的时间戳(同步接收行的 `updated_at` 可能来自 `_now_iso()`)。
- 判定函数**只能收紧不能放宽**:导出端按它自检,若新版比旧版接收端宽松,混版本窗口里旧接收端又会被卡(函数 docstring 写明)。

### 2.2 导出端自检(`archive_sync_v2.media_export_verdict`)

media manifest 只列「二进制端点供得出、且接收端会认」的图片。逐候选判定(只判本页窗口,凑满 `limit` 即停):

| 判定 | 条件 | 处置 |
|---|---|---|
| `ok` | 本地文件存在、大小 = `size_bytes`、前 512 字节过 `validate_synced_image`(MIME / 扩展名 / 内容三者一致);或本地缺文件 / 大小不符,但 OSS 启用且有登记、大小 / MIME / bucket / region 与记录一致(二进制端点 `materialize` 会恢复;冷副本不逐个回 OSS 验头) | 列入 |
| `invalid` | 本地文件在、大小对,但内容不是声明的图片;或 `content_hash` / 扩展名不能安全寻址 | 跳过 + 本地行标 `failed`(`last_error`「缓存内容不是声明的图片(同步导出自检)」,`fail_count+1`,时间戳同 `media_store` 格式),读者端随之 302 回源,冷却后按新口径重下 |
| `unavailable` | 本地缺文件 / 大小不符,且**完全没有** OSS 登记 | 只跳过不改行。恢复:读者取图或媒体回填时该行无可用文件 → 重下 → 行 UPDATE 推进 revision → 下一次增量自然重发;手工恢复文件(不经写库)后在接收端重置 media 单流 |
| 抛错(整页失败、checkpoint 不动) | 读盘 / `lstat` 出 `FileNotFoundError` 以外的 `OSError`;OSS 登记存在但对不上(OSS 已关、大小 / bucket / region / MIME 不符——`materialize` 会失败,读者路径又因登记存在而不回源);媒体库关闭且有候选 | 这些状态可能不经写库就修好(改权限、修配置、重开媒体库),静默跳过会让增量永久越过它们;媒体库关闭但没有候选时仍返回空 complete 页,不卡合法关闭媒体库的节点 |

- 标记在导出读会话关闭后另开短写事务、条件更新(`url_hash` + `status=cached` + `content_hash` + 非同步行),失败只记日志、下页再判。
- 翻页不变式:被跳过的行一定落在本页最后一条导出行之前(或本页已 complete),游标取最后导出行;不会出现「空且未完」的页。
- 首页分母 `pending` 仍是候选数(不为算分母逐个读盘),首轮可能比实际导出多出坏图条数;坏图标 failed 后下一轮即准确。
- 路由 `export_archive_v2_jsonl` 对 media 流传 `_media_export_check()`(媒体库关闭时为逐候选抛错的检查);`export_page(media_check=None)` 保留无检查列举,仅供无媒体库的单测夹具。

### 2.3 接收端单条拒收(`_install_media_file` + `run_pull_v2`)

issue 要求先评估与「一页即一个事务」是否冲突。结论:**不冲突**。

- `import_page` 的事务只含本页元数据(媒体行先落 `pending_sync`);二进制本来就在事务外逐条安装,checkpoint 只在本页全部二进制处理完才前进。
- 拒收条件收得很窄:字节已对上权威声明的大小与 SHA-256,**只是内容不是图片**——这就是权威手里的字节,重试必败,属确定性结论。
  此时行记 `failed`(`last_error`「同步拒收:…」)、抛 `SyncV2MediaRejected`,`run_pull_v2` 计入该流 `skipped`(控制台逐流抽屉已有「跳过」格)并继续。
- 同一 revision 重放(`import_page` 按 revision 跳过、行仍 `failed`)时,声明与上次拒收的字节一致 → 直接判拒、不再重下。
- 权威改版(新字节 → 新 revision)照常重发,`_apply_media` 见状态非 cached 回到 `pending_sync` 再装。
- 仍整页失败、不在本次放宽之列:校验和 / 大小不符(传输或篡改,重试可能好)、声明大小超本机上限(配置可修)、属于其它权威、音频流全部情形。

## 3. 部署顺序与混版本窗口

协议零变化:manifest 行格式、capability、端点、计数字段均不变,无迁移,不涉分析流(`ANALYSIS_PAYLOAD_GENERATION` 不动)。拉模式下外网看不到内网版本,本方案也不需要。

| 外网(导出端) | 内网(接收端) | 结果 |
|---|---|---|
| 新 | 旧 | 坏图不再进 manifest,旧接收端解卡(推荐顺序:外网先上,内网可滞后) |
| 旧 | 新 | 坏图照发,新接收端逐条拒收、记 failed、整流跑完 |
| 新 | 新 | 坏图不出权威;接收端拒收分支只兜底 |

已卡住的内网:外网升级后同一游标重导出的页不再含坏行;内网上此前导入的坏行元数据停在 `pending_sync`,无任何消费方(代理对非 cached 的同步行直接回源),无害,不清理。

## 4. 自测

- 单测:`tests/test_media_sync_export_check.py`(新,17 例:标记与跳过、无检查列举不变、MIME / 扩展名不符、limit=1 翻页不漏不停、读盘错误整页失败;真实 `ObjectStorage` 有效登记列入 / 截断 + 有效登记经 `readable_path` 恢复完整字节 / 登记大小·bucket 不符与 OSS 关闭整页失败;端到端:新导出端整流跑完且坏图零请求、媒体库关闭 checkpoint 不动重开补齐、缺文件与截断经回填重下后增量补发、手工恢复文件经 `reset_stream` 补齐、路由三态)、
  `tests/test_sync_binary_reuse.py`(图片拒收单条跳过并完成后续流;同 revision 重放不重下;权威修复后增量重发装上;音频仍整页失败)、
  `tests/test_media_store.py`(响应头 `image/png` + data URL / HTML 不入缓存;配错头的真图按内容入缓存且过同步校验;自检标 failed 后冷却内回源不抛、冷却后重下;带时区时间戳的冷却判定;截断副本重下覆写)。
- 反向对照:三组首轮测试分别在旧下载器、旧接收端、路由不传检查时失败;R1 六处修复(命中核大小、CAS 覆写、时区冷却、媒体库关闭抛错、登记不符抛错、读盘错误抛错)逐一回退,对应测试均失败。
- 真实数据(开发副本 + 真实媒体目录,均在会话 scratchpad 副本上跑,迁移到 heads):
  - 导出:首轮 1722 候选 → 导出 1706、标 failed 16;次轮 1706 / 1706;每轮约 0.5 s。
  - 进程内整流拉取(新导出端 → 空库):9 流全部完成,media 1706 条 cached、skipped 0。
  - 混版本(无检查导出端 → 新接收端):9 流全部完成,media 1706 cached + 16 failed、skipped 16。

## 5. 存量与生产核实

- 不写存量修复脚本:被公开文章引用的坏图在下一次被拉取时由导出端自检标记;未被引用的坏图只影响本机读者(本来就是裂图),新口径下重下也不会再入缓存。
- 生产只读统计(待用户授权,脚本在会话 scratchpad):`cached` 行里本地文件内容嗅探不过 / 大小不符 / 缺文件的条数与来源分布。若数量大或集中在某个源,再评估定点重抓。

## 6. 遗留(有意不做)

- 接收端 `media_max_bytes` 小于权威时,超限图仍整流失败——可由配置修复,且拒收后调大上限无法自动重发,故不并入单条跳过。
- OSS 冷副本不验头(逐个回源成本高);新口径下不会再产生新的坏冷副本。
- 手工恢复文件后增量不会自动补发,需在接收端重置 media 单流(不为此每轮 touch 跳过行:会产生持续写入、`pending` 永不归零)。
- 本地文件大小对但内容损坏(同大小位翻转)只有接收端全量 SHA-256 能发现,导出端不逐个算哈希。

## 7. 检视

检视方 Codex(`gpt-6-astra`,reasoning effort `ultra`,本机 `codex exec` / `exec resume`;任务书、报告、表态、答复在 worktree `.review/`)。

**R1(commit `e627aaf3`)**:2 P1 + 1 P2,三条均接受。
1. P1 临时不可用被越过:媒体库关闭 → 空 complete 页 → checkpoint 越过,重开不补发;读盘权限错误同理。
2. P1 OSS 已启用即信任冷副本:无登记时二进制端点 404,仍卡流。
3. P2 自检用 `_now_iso()` 写带时区时间,`_failed_still_cooling` 拿无时区 `now()` 相减抛 `TypeError`,读者代理 500。

**协商**:实现方对 1 提出分歧——缺文件 / 大小不符且无登记时不标记、不逐轮重查(标 failed 会让回填与读者取图卡在 6 小时起步冷却;逐轮 touch 产生持续写入、`pending` 不归零),恢复走重下(自动推进 revision)与手工恢复后重置单流。
Codex 接受这一原则,另补两条 P1:命中判定只看 `is_file()`,截断副本进不到重下;「登记存在但不匹配」不能并入「无登记」(`materialize` 失败且读者路径不回源,修配置不写库)——应抛错保留 checkpoint;并要求补「手工恢复 → 实际重置单流 → 补齐」「截断 + 有效登记 → 端点实际恢复」两条验证。一致后成批修复,见 §2.1 / §2.2 / §4。

**复检(最后一轮,commit `487dfee3`)**:对照 `.review/fixlist-r1.md` 六项逐一核对,修法到位、定向验证覆盖对应恢复路径,无新增 P1——复检通过(其定向 99 passed / 1 skipped;实现方全量 3439 passed / 1 skipped)。
