# 并发首访 `/api/reader/unread-counts` 懒建已读水位撞唯一约束(issue #251)

> 状态:进行中(2026-11-09 开工;分支 `feat/issue-251-cursor-upsert`;纯后端机制修复,无可目检界面,按 2026-09-19 例外口径走「检视通过 + 全量套件绿 + CI 绿后直接合入」)。

> 实现方:Claude Code(Fable 5.1);检视方:Codex(见 §6)。
> 上接 #230 手机端波次 e2e:四种屏宽并行跑共用一个读者账号时稳定复现 500,e2e 当时改为每种屏宽一个读者绕开,生产代码未动,方案批 4b 登记为观察项并开本 issue。

## 1. 根因

- `src/services/reader_state.py` 的 `ensure_cursors()` 是「先读后插」:`load_cursors()` 查出缺水位的源,逐个 `session.add(ReaderReadCursorRecord(...))` 后 `commit()`。
- 同一账号两处同时首次挂载阅读器(两标签页 / 两台设备 / 会话恢复)→ 两个请求都查到「缺」→ 都插同一 `(owner_username, source_id)` → 后提交者撞主键,`IntegrityError` 冒到端点成 500。docstring 写的「幂等」只对串行成立。
- `unread-counts` 是阅读器「挂载即校准」入口,并发首访是正常路径,不是边角。SQLite WAL 下有写锁竞争时后写者等待(`busy_timeout=5000` 只是等待上限;先写者提交即继续,等满仍拿不到锁才是 `SQLITE_BUSY`),先写者及时提交后后写者继续执行、撞唯一约束——所以表现是其中一个请求 500。本修消除的是这条唯一约束冲突,不保证任意锁竞争下都不 500。
- 影响面:只影响缺水位行的首访;500 只落在一个请求上,前端这一次未读数拉空、下次刷新恢复,不丢数据;内网 replica 有真实读者,多设备首访会碰到。

## 2. 方案(与 issue 建议一致)

`ensure_cursors()` 改为一条 `INSERT … ON CONFLICT (owner_username, source_id) DO NOTHING`(SQLite 方言 `insert().on_conflict_do_nothing()`,同文件 `mark_ids_read` 已有 `sqlite_insert` + `on_conflict_do_update` 先例),commit 后按 `load_cursors()` **重读**,以库里实际落下的行为准——并发首访时两个请求都拿到先到者的 backlog 水位。

- 已有行永不改动(与原语义一致);缺的源一条语句批量插,顺手对 `source_ids` 去重(原先重复 id 会 `session.add` 两次同主键)。
- 返回值仍是该用户**全部**水位行的映射,调用方(`unread_counts` / `unread_filter_condition`)照旧按 `source_ids` 过滤,契约不变。
- 代价:有缺行时多一次 `load_cursors` SELECT,只发生在首访。
- 不涉迁移、不涉 Archive Sync(`reader_read_cursors` 是读者私有态,不在同步流),无混版本问题。
- 锁语义实证(本机 SQLAlchemy 2.0.49 + pysqlite legacy 事务控制):`load_cursors()` / `_backlog_watermark()` 的 SELECT 取尽后 `sqlite3.Connection.in_transaction == False`,连接不持读事务;INSERT 才隐式 `BEGIN` 并升级写锁,另一连接已提交的行对它可见,不会撞 `SQLITE_BUSY_SNAPSHOT`;写锁竞争由 `busy_timeout=5000` 等待。另一连接先 `ensure_cursors` 提交后本连接再调,返回对方落下的行。

## 3. 同类盘点(有意不改)

同文件另有两处 get-then-add,暴露面与本 issue 不同,本波不动、记触发:

| 处 | 路径 | 为何不改 | 触发 |
|---|---|---|---|
| `_set_read_state`(`mark_read` / `mark_unread`,主键 owner + article) | 竞争需同账号 + 同文章 + 缺状态行 + 写窗口重叠。`POST /articles/{id}/read` 整段 `except Exception` 吞掉返回 `ignored`、不会 500,但**不记日志**,且同事务的阅读计量会一并没落;`mark-read` / `mark-unread` 是用户单点动作,后到的那一下可能失败,前端回滚提示后可重试。只能说「同值重复已读」的最终状态正确;首次 `/read` 与 `mark-unread` 相撞时后到操作可能失败,不是「不丢数据」 | 用户报告手动标读 / 标未读失败;或日志出现 `reader_article_read_states` 唯一约束实例(只有手动端点会冒出来) |
| `reset_cursor`(订阅 → `init_cursor_with_backlog`;`mark_all_read`) | 单源 / 合集 / 自定源的订阅写入口(`reader.py` 六处 `Depends(_guard_source_subscription_mutation)`,经 `services/subscription_mutations` 的进程内用户级互斥,前提是当前两条部署路径都是 uvicorn 单 worker)同用户重叠写入直接 409;OPML 导入与 `app.py` 里的播种点不在该守卫内,余留。「全部标读」时行一般已由 `unread-counts` 校准建好,只走 UPDATE 分支;剩余的缺行竞争不是本波引入 | 日志出现 `reader_read_cursors` 唯一约束实例(非 `ensure_cursors` 栈) |

两处若要改,照 `mark_ids_read` 的 `on_conflict_do_update` 翻译即可,语义等价(最后写者赢);本波不扩是为了不把 ORM 身份映射与 Core UPDATE 的交互拉进一个小修复的检视面。

## 4. 自测

- `tests/test_reader_unread.py` 新增两条:
  - `test_ensure_cursors_concurrent_first_visit_keeps_first_writer`:**确定性交错**——monkeypatch `_backlog_watermark`,在请求 A 已查到「缺」、尚未插行时让请求 B(另一 Session、另一连接)整段跑完 `ensure_cursors` 并 commit;断言 A 不抛、返回 B 落下的水位、库里只有一行。
  - `test_unread_counts_concurrent_first_visit_both_succeed`:**现场复刻**——同一账号经 `/api/subscriptions` 高级路径订两源(不播种水位),两个线程 `Barrier` 同步后同时 `GET /api/reader/unread-counts`;断言都 200、口径一致、每源一行。
- 反向对照:把 `origin/main` 的 `reader_state.py` 换回去跑,两条用例都抛 `sqlite3.IntegrityError: UNIQUE constraint failed: reader_read_cursors.owner_username, reader_read_cursors.source_id`;线程版在旧码下连跑 10 次复现 9 次(本机 macOS,Python 3.12 / SQLite 3.47.1,`-k both_succeed` 单进程);换回修复版 17 条全过。
- 端点并发用例连跑 15 次零失败(TestClient 内两线程经 anyio portal 并发,服务端走真实 SQLite 文件 + WAL + NullPool)。
- 全量套件 `.venv/bin/python -m pytest tests/ -n 4 --dist worksteal`(2026-11-09,提交 b2e676e6 前的工作树):3654 通过、1 跳过、0 失败,7 分 19 秒。
- 无前端改动,`npm` 三件与 Playwright 沙箱不适用(无可目检界面);#230 e2e 当时的绕法(issue 正文所述)与本修无关,本波不动。

## 5. 遗留

- §3 两处同类写法记观察期。
- `ensure_cursors` 批量插入在源数很多(数百)时是一条多值 INSERT,SQLite 变量上限 32766,四列 × 源数远低于此;不设分批。

## 6. 检视

- 实现方 Claude Code(Fable 5.1);检视方 Codex(`gpt-6-astra`,effort ultra,本地 `codex exec` 只读沙箱);任务书 / 报告 / 表态在 worktree `.review/`(不入库)。
- **第 1 轮(2026-11-09)**:总评「可合入」,无 P1 / P2,三条 P3 全是文档口径,全部接受并修:
  ① `busy_timeout` 写成「等完再撞」不准确 → 改为「有竞争时等待、先写者提交即继续并撞唯一约束;本修不保证任意锁竞争下不 500」;
  ② 同类盘点「不丢数据」与触发范围过宽 → 限定为「同值重复已读最终状态正确;首次 `/read` 与 `mark-unread` 相撞后到者可能失败」,`/read` 吞异常不记日志故触发加「用户报告手动标读失败」,表名改为实际的 `reader_article_read_states`;
  ③ 订阅互斥引用错位(我引的「锁内复核」只属自定源分支)→ 改引 `_guard_source_subscription_mutation` / `services/subscription_mutations`(进程内用户级互斥、单 worker 前提、只覆盖挂守卫的六个入口,OPML 与 `app.py` 播种点余留)。
  另按其建议把「线程版稳定复现」换成观测数据(旧码 10 次复现 9 次)。
  检视方自行用内存库 + 独立连接复验:修复版确定性交错保留先写者水位、`origin/main` 旧函数同样交错撞约束、空输入 / 去重 / 生成器 / 返回全部行契约、`mark_all_read` 介入两次读之间时返回推进后的值;其沙箱无可用临时目录跑不了 pytest,属环境失败,方案里的套件数字是实现方自测记录。
- **第 2 轮(复检,只对照修复清单)**:待填。
