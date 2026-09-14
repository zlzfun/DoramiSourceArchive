# Podcast / Archive Sync v3 上线前验证（2026-09-14）

## 结论

代码与双节点协议门禁通过，可进入真实生产环境的配置、凭据和小流量验收阶段。
本记录不替代供应商合同、音频商业使用授权或真实付费账号验收；这些外部条件完成前，
不得把衍生音频的生产切流标记为最终通过。

## 确认的生产拓扑

| 节点 | Runtime | Taxonomy | Podcast | 职责 |
| --- | --- | --- | --- | --- |
| 外网 | `role=all` | `deployment=authority` | `installation=external` + 稳定唯一 authority ID | 公共源采集、分析、ASR、翻译、深度导读、旁白稿、TTS、音频 QA、发布和同步出口 |
| 内网 | `role=all` | `deployment=replica` | `installation=internal` + 稳定唯一 authority ID | 用户自定 RSS 本地处理、Archive Sync 拉取、Reader/feed/MCP 分发；不注册 Podcast 处理阶段 |

内网按 `sources → taxonomy → articles → analyses → media → podcast_texts → podcast_audio → source_states`
拉取。Podcast 两流由 producer capability 协商；旧兼容节点可以省略。`podcast_texts` 携带已发布的
逐字稿、中文稿、深度导读和旁白稿，`podcast_audio` 只携带已发布的 `digest_audio_zh` 元数据并下载、
校验、安装对应 CAS 二进制。原节目音频不进入同步 manifest；外网仅在临时 staging 校验，随后删除，
保留不含原始 URL query 的 `source_media_snapshot`。

部署顺序固定为：先外网 producer，确认健康与 capability 后，再部署内网 receiver；内网首次拉取完成并
核对八流 checkpoint 后再切 Reader 流量。

## 双节点实测

两项脚本都启动了两个隔离的真实 uvicorn HTTP 节点，使用临时数据库和媒体目录，未连接生产库。

1. `python scripts/verify_split_sync_e2e.py`：通过。
   - 外网 `127.0.0.1:61654`、内网 `127.0.0.1:61655`，`ports_distinct=true`。
   - 首轮同步：sources 37、taxonomy 1、articles 4、analyses 1、media 3、source_states 1；Podcast 两流为空。
   - 验证增量、完成态、全零 no-op、Source/Article/Analysis/SourceState tombstone、媒体 GC、Candidate evidence 反向通道与 Taxonomy authority 重启幂等。
2. `python scripts/verify_podcast_all_all_e2e.py`：通过。
   - 外网 `127.0.0.1:61729`、内网 `127.0.0.1:61730`，`ports_distinct=true`。
   - 外网阶段：`fetch,asr,translate,analyze,digest,script,tts,audio_qa,local_publish`；内网阶段为空。
   - 能力：`podcast-text-publications-v1`、`podcast-audio-publications-v1`。
   - 首轮 Podcast 同步：`podcast_texts=4`、`podcast_audio=1`；第二轮与重启后 Podcast 两流均为 0。
   - Reader 成功读取同步文字和精华音频；内网无原节目音频；checkpoint 在重启后保持稳定。
   - 使用彼此不同的本地合成源音频/衍生音频，供应商密钥被剥离，`provider_attempts=0`。

验证过程中修复了两个验收脚本缺陷：Podcast fixture 曾让源音频与衍生音频字节相同而被 CAS 合并；
通用双节点脚本仍硬编码旧六流。两者均已加入回归约束并重新跑通。

## 回归门禁

- Backend：`1743 passed in 226.18s`。
- Podcast/Archive Sync 定向回归：`95 passed in 12.24s`。
- 本次部署/同步定向复跑：`86 passed in 11.45s`。
- E2E fixture 回归：`5 passed in 0.39s`。
- Frontend：`npm run lint` 通过；`npm run build` 通过（Vite 8.0.11，2685 modules）。
- `git diff --check`：通过。
- 阿里云生产替换项和双节点操作步骤见 `docs/aliyun-dual-node-deployment.md`。

## 真实生产切流前仍需完成

1. 两台真实主机分别落地上述 installation/taxonomy/authority 配置；仓库中没有本机 `config/production.ini`，因此本次没有验证真实主机配置。
2. 通过主机秘密管理注入 Archive Sync service token、外网 ASR/TTS/OSS 凭据及真实计价参数；内网不得配置 Podcast provider 凭据。
3. 取得并归档 TTS 商业使用、长期缓存、重复播放、目标受众分发、AI 标识、供应商留存和 voice 权利的书面答案；确定衍生产物公开索引策略。
4. 在受控单集上执行真实 provider canary，核对 ASR/TTS task ID、费用结算、音频时长/响度/Range 播放、OSS 回退删除和管理面审计。
5. 备份两端数据库，执行迁移/健康检查；外网先发、内网后发。首轮同步后逐流核对 authority、count、checkpoint、CAS hash 和 Reader 页面，再逐步放量。

在第 3 项完成前，文字产物可以按既定权利策略单独验收；`digest_audio_zh` 的生产发布必须保持关闭或仅限已明确获权的样本。
