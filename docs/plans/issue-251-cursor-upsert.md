# 并发首访 `/api/reader/unread-counts` 懒建已读水位撞唯一约束(issue #251)

> 状态:进行中(2026-11-09 开工;分支 `feat/issue-251-cursor-upsert`;纯后端机制修复,无可目检界面,按 2026-09-19 例外口径走「检视通过 + 全量套件绿 + CI 绿后直接合入」)。

> 实现方:Claude Code(Fable 5.1);检视方:Codex(见 §6)。
> 上接 #230 手机端波次 e2e:四种屏宽并行跑共用一个读者账号时稳定复现 500,e2e 当时改为每种屏宽一个读者绕开,生产代码未动,方案批 4b 登记为观察项并开本 issue。

## 1. 根因

- `src/services/reader_state.py` 的 `ensure_cursors()` 是「先读后插」:`load_cursors()` 查出缺水位的源,逐个 `session.add(ReaderReadCursorRecord(...))` 后 `commit()`。
- 同一账号两处同时首次挂载阅读器(两标签页 / 两台设备 / 会话恢复)→ 两个请求都查到「缺」→ 都插同一 `(owner_username, source_id)` → 后提交者撞主键,`IntegrityError` 冒到端点成 500。docstring 写的「幂等」只对串行成立。
- `unread-counts` 是阅读器「挂载即校准」入口,并发首访是正常路径,不是边角。SQLite WAL 下后写者会先等 `busy_timeout` 再撞约束,所以表现是其中一个请求 500,不是锁超时。
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
| `_set_read_state`(`mark_read` / `mark_unread`,主键 owner + article) | `POST /articles/{id}/read` 整段 `except Exception` 吞掉返回 `ignored`,不会 500;`mark-read` / `mark-unread` 是用户单点动作,双发只在真同时才撞,且先到者已落行、不丢数据 | 日志出现 `reader_article_read_state` 唯一约束实例 |
| `reset_cursor`(订阅 → `init_cursor_with_backlog`;`mark_all_read`) | 订阅路径在用户锁内复核(`reader.py` 「锁内复核配置仍在」);「全部标读」时行一般已由 `unread-counts` 校准建好,只走 UPDATE 分支 | 同上 |

两处若要改,照 `mark_ids_read` 的 `on_conflict_do_update` 翻译即可,语义等价(最后写者赢);本波不扩是为了不把 ORM 身份映射与 Core UPDATE 的交互拉进一个小修复的检视面。

## 4. 自测

- `tests/test_reader_unread.py` 新增两条:
  - `test_ensure_cursors_concurrent_first_visit_keeps_first_writer`:**确定性交错**——monkeypatch `_backlog_watermark`,在请求 A 已查到「缺」、尚未插行时让请求 B(另一 Session、另一连接)整段跑完 `ensure_cursors` 并 commit;断言 A 不抛、返回 B 落下的水位、库里只有一行。
  - `test_unread_counts_concurrent_first_visit_both_succeed`:**现场复刻**——同一账号经 `/api/subscriptions` 高级路径订两源(不播种水位),两个线程 `Barrier` 同步后同时 `GET /api/reader/unread-counts`;断言都 200、口径一致、每源一行。
- 反向对照:把 `origin/main` 的 `reader_state.py` 换回去跑,两条用例都抛 `sqlite3.IntegrityError: UNIQUE constraint failed: reader_read_cursors.owner_username, reader_read_cursors.source_id`(线程版也稳定复现,不是偶发);换回修复版 17 条全过。
- 端点并发用例连跑 15 次零失败(TestClient 内两线程经 anyio portal 并发,服务端走真实 SQLite 文件 + WAL + NullPool)。
- 全量套件(`-n 4 --dist worksteal`)结果见 §6 前。
- 无前端改动,`npm` 三件与 Playwright 沙箱不适用(无可目检界面);#230 e2e 当时的绕法(issue 正文所述)与本修无关,本波不动。

## 5. 遗留

- §3 两处同类写法记观察期。
- `ensure_cursors` 批量插入在源数很多(数百)时是一条多值 INSERT,SQLite 变量上限 32766,四列 × 源数远低于此;不设分批。

## 6. 检视

(待 Codex 交叉检视后填写:轮次、逐条表态、分歧与结论。)
