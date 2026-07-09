import { useEffect, useState } from 'react';
import { Brain, Check, ChevronRight, Copy, Database, Filter, Loader2, Send, Sparkles, Workflow } from 'lucide-react';
import { getDailyBriefPipeline } from '../api';
import { copyText } from '../utils/clipboard';

/* 日报生成管线的可视化流程图。节点可点击展开详情；Map / Reduce 节点
   展示后端真实提示词（经 /api/daily-brief/pipeline 拉取，与代码同步）。 */

const NODES = [
  {
    id: 'collect', step: '01', icon: Database, title: '预处理', subtitle: '取候选',
    io: ['归档库新文章', '候选 + 游标'],
    desc: '从「增量游标」之后新入库的文章里取候选，按每来源配额与总量裁剪，避免重复处理与全量入模。',
    points: [
      '游标 = fetched_date 水位线，写库成功后才推进（确定性去重·第一层）',
      '按 fetched_date 倒序保留较新；单来源至多 per_source_cap 篇，总量不超过 max_total',
    ],
    paramKeys: ['max_total', 'per_source_cap'],
  },
  {
    id: 'map', step: '02', icon: Brain, llm: true, title: 'Map', subtitle: '概括打分',
    io: ['每篇候选正文', '结构化条目 + 分数'],
    desc: '对每篇有正文的候选并发调用大模型，提炼 标题 / 分类 / 来源 / 领域 / 看点 / 点评 / 标签，并打 0–10 重要性分。无正文候选不进 Map，留作附录。',
    points: [
      '并发度 = map_concurrency；单篇正文截断到 map_max_body_chars 控 token',
      '单篇失败自动降级（score=3、保留原标题），不中断整体',
    ],
    paramKeys: ['map_concurrency', 'map_max_body_chars'],
    promptKey: 'map_system_prompt',
  },
  {
    id: 'select', step: '03', icon: Filter, title: 'Select', subtitle: '择优排序',
    io: ['全部打分条目', 'Top N 精选'],
    desc: '按 score 降序，叠加来源 / 领域多样性配额择优；多样性只决定“谁入选”，最终统一按重要性（分数）降序排列。',
    points: [
      '同来源 / 同领域超配额的高分条目转入候补，不足时再回填',
      '最终顺序即日报正文与导出 JSON 的条目顺序',
    ],
    paramKeys: ['top_n'],
  },
  {
    id: 'reduce', step: '04', icon: Sparkles, llm: true, title: 'Reduce', subtitle: '汇编日报',
    io: ['精选条目 + 近期日报', 'Markdown 日报'],
    desc: '把精选条目汇编成按分类组织的中文 Markdown 日报，每条含 标题 / 来源 / 总结 / 点评；注入近期几天的日报正文做事件级去重（去重·第二层）。',
    points: [
      '近期日报作为去重参考：纯重复省略，有进展只写增量并标「（接前报）」',
      '仅标题（无正文）条目汇入「📎 其它收录」',
    ],
    paramKeys: ['recent_brief_days'],
    promptKey: 'reduce_system_prompt',
  },
  {
    id: 'persist', step: '05', icon: Send, title: '写库', subtitle: '分发',
    io: ['Markdown + items', '可订阅日报源'],
    desc: '写入归档库为 dorami_daily_brief 源并推进游标；读者订阅后经个人聚合接口 / MCP 获取，外部脚本可把结构化 items 导出为 shendeng 上传 JSON。',
    points: [
      '同日重跑幂等覆盖；删除最新一期会自动回退游标',
      'Markdown 给人读、结构化 items 给下游脚本，二者同源',
    ],
    paramKeys: [],
  },
];

const PARAM_LABELS = {
  top_n: '精选条数',
  max_total: '候选总量上限',
  per_source_cap: '每来源候选上限',
  map_concurrency: 'Map 并发度',
  map_max_body_chars: 'Map 正文截断',
  recent_brief_days: '去重参考天数',
};

export default function DailyBriefFlow({ showToast, canManage = false }) {
  const [pipeline, setPipeline] = useState(null);
  const [loadError, setLoadError] = useState(false);
  const [openId, setOpenId] = useState(null);
  const [copied, setCopied] = useState(false);

  // /api/daily-brief/pipeline 是 collector(管理员) 端点。自我守卫：无管理权限不拉取，
  // 避免日后这个流程图被挪到管理员分支之外时，读者会话挂载即打 collector 端点报 403。
  useEffect(() => {
    if (!canManage) return;
    getDailyBriefPipeline()
      .then(setPipeline)
      .catch(() => setLoadError(true));
  }, [canManage]);

  const active = NODES.find(n => n.id === openId) || null;
  const promptText = active?.promptKey ? pipeline?.[active.promptKey] : '';

  const handleCopyPrompt = async () => {
    try {
      await copyText(promptText || '');
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      showToast?.('复制失败，请手动选择文本复制', 'error');
    }
  };

  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <Workflow className="w-4 h-4 text-indigo-500" />
        <p className="text-sm font-bold text-slate-700">生成原理</p>
        <span className="tiny-meta">map → 择优 → reduce 五步管线 · 点击节点查看详情与提示词</span>
      </div>

      {/* 流程图：大屏一行五步，小屏自动换行 */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
        {NODES.map((node, i) => {
          const Icon = node.icon;
          const isOpen = openId === node.id;
          return (
            <div key={node.id} className="flex items-stretch">
              <button
                type="button"
                onClick={() => setOpenId(isOpen ? null : node.id)}
                className={`group relative flex w-full flex-col gap-1.5 rounded-[var(--r-card)] border px-3 py-3 text-left transition-all ${
                  isOpen
                    ? 'border-[var(--dorami-border-strong)] bg-[var(--dorami-wash)]'
                    : 'border-[var(--dorami-border)] bg-[var(--dorami-soft)] hover:border-[var(--dorami-border-strong)] hover:bg-[var(--dorami-surface)]'
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--r-control)] ${isOpen ? 'bg-indigo-100 text-indigo-600' : 'bg-[var(--dorami-surface)] text-slate-500 group-hover:text-slate-700'}`}>
                    <Icon className="h-4 w-4" />
                  </span>
                  <span className="micro-label font-bold tabular-nums text-slate-300">{node.step}</span>
                  {node.llm && (
                    <span className="ml-auto rounded-full bg-amber-100 px-1.5 py-0.5 micro-label text-amber-600">大模型</span>
                  )}
                </div>
                <div>
                  <p className="text-sm font-bold text-slate-700">{node.title}</p>
                  <p className="text-xs text-slate-500">{node.subtitle}</p>
                </div>
              </button>
              {/* 步骤间箭头（仅大屏、非末位显示） */}
              {i < NODES.length - 1 && (
                <ChevronRight className="hidden lg:block h-4 w-4 self-center shrink-0 -mx-1 text-slate-300" />
              )}
            </div>
          );
        })}
      </div>

      {/* 节点详情 */}
      {active && (
        <div className="mt-3 rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-[var(--dorami-surface)] p-4 animate-in fade-in">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <p className="text-sm font-bold text-slate-700">{active.title} · {active.subtitle}</p>
            {active.llm && <span className="rounded-full bg-amber-100 px-2 py-0.5 micro-label text-amber-600">大模型节点</span>}
            <span className="flex items-center gap-1.5 text-xs text-slate-500">
              {active.io[0]} <ChevronRight className="h-3 w-3" /> <span className="font-medium text-slate-500">{active.io[1]}</span>
            </span>
          </div>

          <p className="text-xs leading-relaxed text-slate-500">{active.desc}</p>

          {active.points?.length > 0 && (
            <ul className="mt-2 space-y-1">
              {active.points.map((p, idx) => (
                <li key={idx} className="flex gap-2 text-xs text-slate-500">
                  <span className="mt-[6px] h-1 w-1 shrink-0 rounded-full bg-indigo-400" />
                  <span className="leading-relaxed">{p}</span>
                </li>
              ))}
            </ul>
          )}

          {/* 参数 chips */}
          {active.paramKeys?.length > 0 && pipeline?.params && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {active.paramKeys.map(key => (
                <span key={key} className="rounded-[var(--r-control)] border border-[var(--dorami-border)] bg-[var(--dorami-soft)] px-2 py-1 text-xs text-slate-500">
                  {PARAM_LABELS[key] || key}
                  <span className="ml-1 font-mono font-bold text-slate-700">{pipeline.params[key]}</span>
                </span>
              ))}
            </div>
          )}

          {/* 提示词（Map / Reduce） */}
          {active.promptKey && (
            <div className="mt-3">
              <div className="mb-1.5 flex items-center justify-between">
                <p className="form-label mb-0">系统提示词{pipeline?.model ? <span className="ml-1 normal-case text-slate-500">· 模型 {pipeline.model}</span> : null}</p>
                <button
                  onClick={handleCopyPrompt}
                  disabled={!promptText}
                  className="action-button action-button-quiet min-h-[28px] px-2 text-xs"
                >
                  {copied ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
                  {copied ? '已复制' : '复制'}
                </button>
              </div>
              {pipeline ? (
                <pre className="max-h-[320px] overflow-auto whitespace-pre-wrap break-words rounded-[var(--r-card)] bg-slate-950 px-4 py-3 text-xs leading-relaxed text-slate-300">{promptText || '（未获取到提示词）'}</pre>
              ) : loadError ? (
                <p className="tiny-meta text-rose-500">提示词加载失败，请刷新重试。</p>
              ) : (
                <p className="flex items-center gap-2 tiny-meta"><Loader2 className="h-3.5 w-3.5 animate-spin" /> 正在加载提示词…</p>
              )}
              {active.id === 'map' && pipeline?.allowed_classifications?.length > 0 && (
                <p className="tiny-meta mt-1.5">可选分类：{pipeline.allowed_classifications.join(' / ')}</p>
              )}
            </div>
          )}
        </div>
      )}

      <p className="tiny-meta mt-3">双层去重：① 游标水位线（确定性，不重复处理已入库内容）+ ② Reduce 注入近期日报（语义 / 事件级）。</p>
    </div>
  );
}
