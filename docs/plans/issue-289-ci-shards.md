# CI 提速(二):后端 job 分片并行、降低用例开销(issue #289)

> 状态:进行中(2026-11-16 开工;分片与守卫已实现,CI 实测与交叉检视待做)。
> 实现方:Claude Code(Fable 5.2);检视方:Codex(见 §6)。分支 `feat/issue-289-ci-shards`(叠在 #288 之上,#288 合入后变基到 main)。
> 关联:[issue #289](https://github.com/zlzfun/DoramiSourceArchive/issues/289);前一波 [issue #288](https://github.com/zlzfun/DoramiSourceArchive/issues/288)(按改动面选 job,[方案](../archive/issue-288-ci-scope.md))。

## 1. 现状与目标

#288 让只动前端 / 文档的 PR 不再等后端;动到后端的 PR 仍要等约 10.5 分钟,由 `backend-unit` 一个 job 决定:约 3470 个用例,runner 上 `-n 4` 要 10 分 12 秒(本机同一条命令 3 分 27 秒),`backend-deploy` 4–6 分钟。

本机插桩(一次 unit 全量,worker 时间合计 801 s)看时间花在哪:

| 开销 | 次数 | 单次 | 占 worker 时间 |
|---|---|---|---|
| `SQLModel.metadata.create_all`(77 张表、253 个索引、78 个触发器,一次 625 条语句) | 2203 | 76 ms | 21% |
| PBKDF2 20 万轮(播种账号 + 登录校验) | 2401 | 23 ms | 7% |
| TestClient 启动(lifespan) | 749 | 72 ms | 7% |
| Alembic `upgrade` | 75 | 631 ms | 6% |

没有哪一项占多数,剩下的六成散在三千多个用例里——所以主手段是**并行**(把一个 job 拆成几片各占一台 runner,公共仓库分钟数免费),用例本身的开销按「收益 ÷ 风险」挑着做。目标:动到后端的 PR 等待降到 5 分钟以内。

## 2. 分片

### 2.1 `--shard I/N`

`tests/conftest.py` 加一个 pytest 选项:`--shard I/N` 只保留落在第 I 片的用例(`pytest_collection_modifyitems` 里 deselect)。

- 片号 = `crc32(用例函数的 nodeid) % N + 1`。纯函数:不依赖收集顺序、不依赖别的片跑了什么,所以「每条用例恰好落在一片里」是构造出来的,不是靠各片互相配合。
- **参数 id 不参与哈希**(取 nodeid 的 `[` 之前):同一个测试函数的全部参数化落在同一片。个别参数 id 来自对象 `repr`,不能指望它跨进程稳定;按函数哈希则只依赖文件路径、类名、函数名。
- 按用例而不是按文件分:deploy 侧只有 6 个文件、单文件上百条慢用例,按文件分不匀。代价是每片都要收集全部文件(本机 4–5 秒)。
- 形状不对(`0/3`、`4/3`、`1`、`a/b`)直接是用法错误(退出码 4),不默默退回全量或空集;某一片为空时 pytest 自己以退出码 5 报红。
- xdist 下每个 worker 各自收集、各自按同一函数筛,结果一致。

均衡性(本机):unit 3 片 1198 / 1226 / 1144 条,耗时 85 / 72 / 73 秒(不分片 210 秒);deploy 2 片 109 / 114 条。

### 2.2 `ci.yml`

`backend-unit` 用矩阵 `shard: [1, 2, 3]`,`backend-deploy` 用 `shard: [1, 2]`,命令各加 `--shard ${{matrix.shard}}/N`,每片仍是 `-n 4 --dist worksteal`。`fail-fast: false`:一片红了别的片照样跑完,一次看全。

汇总门不用改:`needs.<job>.result` 对矩阵 job 是汇总结果(全部成功才是 `success`),job 被 `if` 跳过时仍是 `skipped`——#288 的两条放行路径原样成立。

### 2.3 少一片的后果与守卫

少一片(矩阵写成 `[1, 2]` 而分母是 3、分母漂成 4、命令里把矩阵变量写死成 `1/3`)的后果是:那一片的用例哪个 job 都不跑,而 CI 照样是绿的。`tests/test_ci_workflow.py` 新增 `_assert_shards`:

- 矩阵只有 `shard` 一维、不许 `include` / `exclude`、取值恰是 `1..N`;
- 命令里 `--shard` 恰好出现一次,值恰是 `${{matrix.shard}}/N`,N 等于矩阵长度。

外加一条端到端:按 `ci.yml` 里的片数真跑 `--collect-only --shard I/N`,各片互不相交、并集等于不分片时的全集,同一函数的参数化不跨片。

## 3. 用例开销

### 3.1 测试里的密码哈希:做

生产是 PBKDF2-HMAC-SHA256 20 万轮,本机单次 22.6 ms;unit 一轮要哈希 / 校验约 2400 次,占 worker 时间约 7%。测试只关心「能登录、口令对不对」,不关心轮数。

- `services/accounts.hash_password` 的缺省轮数从「默认参数」改为**调用期**读 `_PBKDF2_ITERATIONS`(默认参数在函数定义时就绑死,改常量不生效)。生产行为不变:常量仍是 20 万,校验按编码串里记的轮数算。
- `tests/conftest.py` 加会话级 autouse 夹具 `fast_password_hashing`:整个测试会话把缺省轮数调到 1000(单次 0.14 ms),会话结束还原;夹具顺带断言生产常量不低于 20 万——快哈希只该来自这个夹具。
- 需要真轮数的用例显式传 `iterations=`;`tests/test_accounts.py` 补一条钉住这三件事。

### 3.2 临时目录放 tmpfs:看 CI 实测

同一条 unit 命令本机 3.5 分钟、runner 上 10 分钟。一个假设是 SQLite 的 fsync:macOS 的 `fsync` 不真正落盘,runner 的 ext4 会,而几千个用例各自提交多次。把 pytest 的临时目录(`tmp_path` 与 conftest 沙箱都跟 `TMPDIR` 走)指到 `/dev/shm` 是一行的改动,但它只在 runner 上有意义,本机量不出来——所以在本 PR 里放一个**临时工作流**(`ci-exp-289.yml`,合入前删除),用与 `ci.yml` 第 1 片逐字相同的命令、只多一个 `TMPDIR=/dev/shm`,和同一次运行里 `ci.yml` 的第 1 片比耗时。

(待填:实测数字与取舍。)

### 3.3 建库模板:试验可行,这一波不做

`DatabaseStorage()` 初始化一次 152 ms,其中 `create_all` 71 ms(77 张表、253 个索引、78 个触发器,625 条语句),其余是 FTS、标题翻译初始化等;unit 一轮要建约 2200 个库,是 worker 时间里最大的一项。试验:建一个模板库,之后用 `sqlite3` 的 backup 拷进新连接只要 1.9 ms,直接拷文件 0.2 ms。

不在这一波做,原因是「什么时候模板算陈旧」说不干净:

- 触发器、FTS 等是挂在 `after_create` 上的监听器,分散在不同模块里按导入顺序注册;模板建得早于某个模块的导入,之后的库就悄悄少一批对象。要防这个,指纹得把监听器状态也算进去。
- 最大的调用点是 `DatabaseStorage.__init__`,`create_all` 之后还有四步初始化;只替换 `create_all` 拿不到一半收益,要拿全就得在生产类上开一个只给测试用的口子。
- 分片之后它对 PR 等待的边际收益不到 1 分钟(unit 片从约 4 分钟降到约 3 分钟,届时 deploy 片成为决定项)。

记入 backlog,触发条件:分片后 unit 片仍是 PR 等待的决定项、且用例数再涨一截。

## 4. 守卫与自测

- `tests/test_ci_workflow.py`:`_assert_shards`(矩阵只有 `shard` 一维、无 `include` / `exclude`、取值恰是 1..N;命令里 `--shard` 恰一次且值恰是 `${{matrix.shard}}/N`);端到端 `test_shards_cover_every_test_exactly_once`(按 `ci.yml` 的片数真跑 `--collect-only --shard I/N`,互不相交、并集等于全集、同一函数的参数化不跨片);畸形 `--shard` 七种写法都是用法错误;新增 13 条突变(矩阵缺片 / 从 0 起 / 重复 / 被 `exclude` / 多一维 / 整个删掉,分母漂移,分子写死,选项丢失或被后一个覆盖,轻量 job 被加矩阵)。#288 的结构白名单对分片的两个 job 多认一个 `strategy` 键。
- `tests/test_accounts.py`:缺省轮数在调用期读取、显式轮数照用、新旧轮数的哈希都能校验、夹具交回的生产缺省不低于 20 万。
- 自测(本机):(待填)。

## 5. 取舍与不做

- **片数**:unit 3 片、deploy 2 片。再多,每片的固定开销(检出、装依赖、装 ffmpeg、收集,约 45 秒)占比上升,收益递减;片数写在 `ci.yml` 一处,守卫跟着矩阵长度走,以后调整不用改测试。
- **不按耗时做装箱分片**(pytest-split 一类):要入库一份耗时表并持续更新,哈希分片实测已经够匀(本机三片 85 / 72 / 73 秒)。
- **不拆 `-n 4`**:每片仍是 4 个 worker,和不分片时一样吃满一台 runner。
- **不做建库模板**:§3.3。
- **不动 Alembic 迁移类用例**(75 次 `upgrade`,每次 631 ms):它们测的就是迁移本身。

## 6. 交叉检视

(待填。)
