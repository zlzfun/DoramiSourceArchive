import { useState, useEffect, useRef, useCallback } from 'react';
import { Plug2, Copy, Check, Download, Terminal, Globe, Newspaper, ChevronDown, ChevronRight, KeyRound, PowerOff } from 'lucide-react';
import { fetchMcpStatus } from '../api';
import { MCP_URL } from '../config';
import { copyText } from '../utils/clipboard';
import { runAction } from '../utils/runAction';
import FeedAccessSection from './FeedAccessSection';
import AccessTokenCard from './AccessTokenCard';
import DailyBriefPanel from './DailyBriefPanel';

const TOOL_CARDS = [
  {
    name: 'search_articles',
    params: 'query, top_k?, content_type?, source_id?, publish_date_gte?, distance_threshold?, subscription_token?',
    desc: '语义向量搜索，支持中英文，按相关性排序。适合主题查询，如「最近的具身智能资讯」。',
    requiresRag: true,
  },
  {
    name: 'browse_articles',
    params: 'source_id?, content_type?, publish_date_start?, publish_date_end?, has_content?, limit?, skip?, subscription_token?',
    desc: '条件过滤浏览，按来源/类型/日期区间筛选。传入 dfeed_ 后限定到你的订阅范围。',
  },
  {
    name: 'get_article',
    params: 'article_id: str, subscription_token?',
    desc: '按 ID 获取单篇文章完整内容（含正文和扩展元数据）。传入 token 后会校验订阅范围。',
  },
  {
    name: 'list_sources',
    params: '(无参数)',
    desc: '列出所有数据来源，获取可用的 source_id 和 content_type，建议首先调用。',
  },
  {
    name: 'get_rag_context',
    params: 'query, top_k?, max_chars?, distance_threshold?, content_type?, source_id?, publish_date_gte?, subscription_token?',
    desc: '组装格式化 RAG 上下文字符串，可直接拼入 LLM System Prompt。传入 token 后限定到订阅范围。',
    requiresRag: true,
  },
];

const LOCAL_TOOLS = ['Claude Code', 'Cursor', 'Codex', 'OpenCode'];
const ONLINE_TOOLS = ['Claude.ai Projects', 'Coze'];

/** 分组标题：沿用 app 内「色条 + 文字」(section-title) 的分区语汇，让接入页与其它页面同构、不自成一派。 */
function GroupHeader({ accent = 'bg-indigo-500', title, hint }) {
  return (
    <div className="flex items-center gap-2.5 px-0.5">
      <span className={`h-5 w-1 shrink-0 rounded-full ${accent}`} />
      <h3 className="section-title">{title}</h3>
      {hint && <span className="hidden text-xs font-medium text-slate-500 sm:inline">· {hint}</span>}
    </div>
  );
}

export default function MCPTab({ showToast, ragEnabled = false, collectorEnabled = false, isAdmin = false }) {
  const canManage = collectorEnabled && isAdmin;        // 管理员才有「日报生成」管理页
  const [sub, setSub] = useState(canManage ? 'brief' : 'access');  // 默认聚焦「日报生成」；读者无此页则落到 access
  const subTouched = useRef(false);                     // 用户是否手动切过页（避免覆盖其选择）
  const goSub = useCallback((next) => { subTouched.current = true; setSub(next); }, []);

  // runtime 能力是异步加载的，初始渲染时 canManage 可能尚为 false（account_role 未就绪）。
  // 待其就绪后，若用户未手动切页，则把默认页校正为「日报生成」（管理员）/「Agent 接入」（读者）。
  useEffect(() => {
    if (!subTouched.current) setSub(canManage ? 'brief' : 'access');
  }, [canManage]);
  const [toolsOpen, setToolsOpen] = useState(false);    // MCP 可用工具列表折叠
  const [scopeOpen, setScopeOpen] = useState(false);    // 「启停与取数范围」说明默认收缩
  const [stoppedConfigOpen, setStoppedConfigOpen] = useState(false);  // MCP 停止时接入配置默认折叠
  const [status, setStatus] = useState(null);
  const [configKind, setConfigKind] = useState('mcpServers');
  const [copied, setCopied] = useState(false);
  const [copiedJson, setCopiedJson] = useState(false);
  const showToastRef = useRef(showToast);
  useEffect(() => { showToastRef.current = showToast; }, [showToast]);

  useEffect(() => {
    fetchMcpStatus()
      .then(setStatus)
      .catch(() => setStatus({ enabled: false, url: null }));
  }, []);

  // 设置面板里启停 MCP 后会广播该事件，同步已挂载页面的运行状态。
  useEffect(() => {
    const handleMcpChanged = (event) => {
      setStatus(prev => ({ ...(prev || {}), enabled: event.detail?.enabled ?? false }));
    };
    window.addEventListener('dorami-mcp-changed', handleMcpChanged);
    return () => window.removeEventListener('dorami-mcp-changed', handleMcpChanged);
  }, []);

  const handleCopy = () => runAction(() => copyText(mcpUrl), {
    showToast: (m, t) => showToastRef.current?.(m, t),
    success: '接入地址已复制',
    error: '复制失败，请手动选择文本复制',
    onSuccess: () => { setCopied(true); setTimeout(() => setCopied(false), 2000); },
  });

  const mcpUrl = status?.url ?? MCP_URL;
  const mcpConfigExamples = {
    mcpServers: {
      label: 'Claude Code / Cursor',
      note: '常见 MCP HTTP 配置格式，适用于使用 mcpServers 字段的客户端。',
      value: {
        mcpServers: {
          'dorami-archive': {
            type: 'http',
            url: mcpUrl,
            headers: { Authorization: 'Bearer dfeed_你的令牌' },
          },
        },
      },
    },
    opencode: {
      label: 'OpenCode',
      note: 'OpenCode 使用 mcp 字段组织远程 MCP。不同版本字段可能有差异，请以客户端文档为准。',
      value: {
        mcp: {
          'dorami-archive': {
            type: 'remote',
            url: mcpUrl,
            enabled: true,
            headers: { Authorization: 'Bearer dfeed_你的令牌' },
          },
        },
      },
    },
  };
  const activeConfig = mcpConfigExamples[configKind] ?? mcpConfigExamples.mcpServers;
  const mcpJson = JSON.stringify(activeConfig.value, null, 2);

  const handleCopyJson = () => runAction(() => copyText(mcpJson), {
    showToast: (m, t) => showToastRef.current?.(m, t),
    success: '客户端配置已复制',
    error: '复制失败，请手动选择文本复制',
    onSuccess: () => { setCopiedJson(true); setTimeout(() => setCopiedJson(false), 2000); },
  });

  const handleDownload = (url, filename) => {
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
  };

  const enabled = status?.enabled ?? false;

  // 接入地址 + JSON 配置示例。MCP 运行时直接展开；停止时折叠到展开器后面（见下方渲染）。
  const configBlocks = (
    <div className="space-y-5">
      {/* URL：只读的接入地址，按「可复制的代码值」呈现，而非输入框 */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <p className="form-label mb-0">接入地址</p>
          <button
            onClick={handleCopy}
            className="action-button action-button-quiet min-h-[28px] px-2 text-xs"
          >
            {copied ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
            {copied ? '已复制' : '复制'}
          </button>
        </div>
        <code className="block rounded-[var(--r-card)] bg-slate-950 px-4 py-3 text-sm font-mono text-slate-300 break-all select-all">
          {mcpUrl}
        </code>
      </div>

      {/* JSON config */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <p className="form-label mb-0">客户端配置示例（JSON）</p>
          <button
            onClick={handleCopyJson}
            className="action-button action-button-quiet min-h-[28px] px-2 text-xs"
          >
            {copiedJson ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
            {copiedJson ? '已复制' : '复制'}
          </button>
        </div>
        <div className="overflow-hidden rounded-[var(--r-card)] bg-slate-950">
          <div className="flex items-center gap-1 border-b border-white/10 px-2 pt-2">
            {Object.entries(mcpConfigExamples).map(([key, item]) => (
              <button
                key={key}
                type="button"
                onClick={() => setConfigKind(key)}
                className={`rounded-t-md px-3 py-1.5 text-xs font-semibold transition-colors ${configKind === key ? 'bg-white/10 text-white' : 'text-slate-500 hover:text-slate-300'}`}
              >
                {item.label}
              </button>
            ))}
          </div>
          <pre className="text-xs font-mono text-slate-300 px-4 py-4 overflow-x-auto leading-relaxed select-all">{mcpJson}</pre>
        </div>
        <p className="tiny-meta mt-1.5">
          把 <code className="font-mono">headers</code> 里的 <code className="font-mono">dfeed_你的令牌</code> 换成上方「访问令牌」复制的令牌（或单订阅 <code className="font-mono">dsub_</code>）；连接后每次调用都会自动按你的订阅范围过滤，无需逐次传参。
        </p>
        <p className="tiny-meta mt-1">
          {activeConfig.note} 配置文件位置和字段名会随客户端变化，请以对应客户端当前文档为准。
        </p>
      </div>
    </div>
  );

  return (
    <div className="space-y-6">
      <div className="page-head">
        <h1 className="page-title">接入集成</h1>
        {canManage && (
          <div className="page-head-actions">
            <div className="segmented-control">
              <button onClick={() => goSub('brief')} className={`segmented-option ${sub === 'brief' ? 'segmented-option-active' : ''}`}><Newspaper /> 日报生成</button>
              <button onClick={() => goSub('access')} className={`segmented-option ${sub === 'access' ? 'segmented-option-active' : ''}`}><Plug2 /> Agent 接入</button>
            </div>
          </div>
        )}
      </div>

      {/* ══ 日报生成页（仅管理员） ══════════════════════════════════ */}
      {canManage && sub === 'brief' && (
        <div>
          <DailyBriefPanel showToast={showToast} collectorEnabled={collectorEnabled} isAdmin={isAdmin} />
        </div>
      )}

      {/* ══ Agent 接入页 ═══════════════════════════════════════════ */}
      {sub === 'access' && (
        <div className="space-y-6">
      {/* ── HERO（克制的渐变标识卡，纹理交给 .integration-hero 自带的渐变光晕）── */}
      <div className="integration-hero relative overflow-hidden rounded-[var(--r-card)] p-7 shadow-[var(--sh-2)]">
        <div className="relative">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="integration-kicker mb-2">
                Integration Hub · 接入集成
              </p>
              <h2 className="text-xl font-bold text-white mb-2 tracking-tight">把哆啦美接入你的 Agent 与脚本</h2>
              <p className="integration-lede max-w-2xl">
                {status !== null && !enabled
                  ? 'MCP 当前已停止，接入暂不可用；可先生成下方访问令牌，待管理员启动后即可接入。'
                  : '生成下方访问令牌，即可开始接入。'}
              </p>
            </div>
            {/* MCP 运行状态：右上角一枚状态徽标（停止时整枚转红，与全站红=停止一致） */}
            <span className={`inline-flex shrink-0 items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-bold backdrop-blur ${status !== null && !enabled ? 'border-rose-300/40 bg-rose-500/25 text-rose-50' : 'border-white/15 bg-white/10 text-white/90'}`}>
              {status === null ? (
                <span className="h-2 w-2 rounded-full bg-white/40" />
              ) : enabled ? (
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-300 opacity-75" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-300" />
                </span>
              ) : (
                <span className="h-2 w-2 rounded-full bg-rose-300" />
              )}
              MCP {status === null ? '检测中' : enabled ? '运行中' : '已停止'}
            </span>
          </div>

          {/* 访问令牌：直接内嵌在 Hero 里——lede 已点题，这里当场取令牌，避免重复成块 */}
          <AccessTokenCard showToast={showToast} variant="hero" />
        </div>
      </div>

      {/* ── 分区 ①：面向 Agent（MCP + Skill）──────────────────────── */}
      <section className="space-y-3">
        <GroupHeader accent="bg-sky-500" title="面向 Agent" hint="MCP 实时接入 · Skill 自动生成日报" />
        <div className="space-y-5">
      {/* ── MCP DETAILS ──────────────────────────────────────────── */}
      <div className="surface-card rounded-[var(--r-card)] overflow-hidden">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-6 py-4 border-b border-[var(--dorami-border)]">
          <div className="w-1 h-5 rounded-full bg-sky-500" />
          <h3 className="section-title">MCP 配置详情</h3>
          {/* 语义检索（RAG）能力状态；MCP 运行状态已由页顶 Hero 徽标统一呈现，此处不重复 */}
          <div className="ml-auto flex items-center gap-2">
            <span
              title={ragEnabled ? undefined : '语义检索（向量搜索）需由部署方在配置中开启'}
              className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 micro-label ${ragEnabled ? 'bg-emerald-50 text-emerald-600' : 'bg-slate-100 text-slate-500'}`}
            >
              <span className={`h-1.5 w-1.5 rounded-full ${ragEnabled ? 'bg-emerald-500' : 'bg-slate-400'}`} />
              语义检索 {ragEnabled ? '已启用' : '未启用'}
            </span>
          </div>
        </div>

        <div className="p-6 space-y-5">
          <div className="rounded-[var(--r-control)] border border-sky-100 bg-sky-50 px-4 py-3">
            <button
              type="button"
              onClick={() => setScopeOpen(o => !o)}
              className="flex w-full items-center gap-2 text-left"
            >
              <KeyRound className="h-4 w-4 shrink-0 text-sky-500" />
              <span className="text-sm font-bold text-sky-900">接入需带访问令牌，仅返回你订阅的来源</span>
              <span className="ml-auto shrink-0 text-sky-500">
                {scopeOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
              </span>
            </button>
            {scopeOpen && (
              <ul className="tiny-meta mt-2 space-y-1 text-sky-800 list-disc pl-4 marker:text-sky-400">
                <li>在上方「访问令牌」复制 <code className="font-mono">dfeed_</code>（或单订阅 <code className="font-mono">dsub_</code>）填入即可；不带令牌的调用会被拒绝（仅 <code className="font-mono">list_sources</code> 例外，可直接列目录）。</li>
                <li>MCP 的启停由管理员统一控制。</li>
              </ul>
            )}
          </div>

          {/* MCP 未启动：醒目横幅，避免被下方大段配置淹没 */}
          {status !== null && !enabled && (
            <div className="flex items-start gap-3 rounded-[var(--r-control)] border border-rose-200 bg-rose-50 px-4 py-3">
              <PowerOff className="mt-0.5 h-5 w-5 shrink-0 text-rose-500" />
              <div>
                <p className="text-sm font-bold text-rose-900">MCP Server 未启动</p>
                <p className="tiny-meta mt-0.5 text-rose-700">当前无法接入，请联系管理员启动后再使用下方配置。</p>
              </div>
            </div>
          )}

          {/* 接入地址 + JSON 配置：运行时直接展示；停止时折叠到展开器后面（不置灰，避免发灰不自然） */}
          {enabled ? (
            configBlocks
          ) : (
            <div className="rounded-[var(--r-control)] border border-[var(--dorami-border)] bg-[var(--dorami-soft)]">
              <button
                type="button"
                onClick={() => setStoppedConfigOpen(o => !o)}
                className="flex w-full items-center gap-2 px-4 py-2.5 text-sm font-bold text-slate-500 hover:text-slate-700"
              >
                {stoppedConfigOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                查看接入配置（地址与示例）
              </button>
              {stoppedConfigOpen && <div className="px-4 pb-4">{configBlocks}</div>}
            </div>
          )}

          {/* Tools（默认折叠：参考信息，按需展开） */}
          <div>
            <button
              type="button"
              onClick={() => setToolsOpen(o => !o)}
              className="flex w-full items-center gap-2 text-sm font-bold text-slate-500 hover:text-slate-700"
            >
              {toolsOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
              可用工具 <span className="font-normal text-slate-500">({TOOL_CARDS.length} 个)</span>
            </button>
            {toolsOpen && (
              <div className="mt-2 divide-y divide-[var(--dorami-border)] rounded-[var(--r-card)] border border-[var(--dorami-border)] overflow-hidden">
                {TOOL_CARDS.map(tool => {
                  const disabled = tool.requiresRag && !ragEnabled;
                  return (
                    <div key={tool.name} className={`flex gap-4 px-4 py-3 transition-colors ${disabled ? 'bg-slate-100/60 opacity-60' : 'bg-[var(--dorami-soft)] hover:bg-slate-100/80'}`}>
                      <div className={`shrink-0 mt-[3px] w-1.5 h-1.5 rounded-full ${disabled ? 'bg-slate-300' : 'bg-sky-400'}`} />
                      <div className="min-w-0">
                        <div className="flex items-baseline gap-2 flex-wrap mb-0.5">
                          <code className={`text-xs font-bold ${disabled ? 'text-slate-500 line-through' : 'text-sky-700'}`}>{tool.name}</code>
                          <span className="tiny-meta font-mono">{tool.params}</span>
                          {disabled && (
                            <span className="rounded-full bg-slate-200 px-1.5 py-0.5 micro-label text-slate-500">需启用 RAG</span>
                          )}
                        </div>
                        <p className="text-xs text-slate-500 leading-relaxed">{tool.desc}</p>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── SKILL INSTALLATION（常驻展开，与其它板块一致） ───────────────── */}
      <div className="surface-card rounded-[var(--r-card)] overflow-hidden">
        <div className="flex items-center gap-3 px-6 py-4 border-b border-[var(--dorami-border)]">
          <div className="w-1 h-5 rounded-full bg-violet-500" />
          <h3 className="section-title">Skill 安装指南</h3>
          <button
            onClick={() => handleDownload('/api/skill/daily-brief', 'dorami-daily-brief.zip')}
            className="action-button action-button-secondary shrink-0 min-h-[34px] px-3 text-xs ml-auto"
          >
            <Download className="w-3.5 h-3.5" />
            下载 Skill 包
          </button>
        </div>

        <div className="p-6">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {/* Local tools */}
            <div className="rounded-[var(--r-card)] border border-[var(--dorami-border)] p-4 hover:border-[var(--dorami-border-strong)] transition-colors">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-7 h-7 rounded-[var(--r-control)] bg-sky-50 flex items-center justify-center">
                  <Terminal className="w-3.5 h-3.5 text-sky-600" />
                </div>
                <p className="text-sm font-bold text-slate-700">本地 AI 工具</p>
              </div>
              <div className="flex flex-wrap gap-1 mb-4">
                {LOCAL_TOOLS.map(t => (
                  <span key={t} className="status-badge min-h-[22px] bg-sky-50 text-sky-600 border-sky-100">{t}</span>
                ))}
              </div>
              <ol className="space-y-2 mb-4">
                {[
                  '下载并解压 dorami-daily-brief.zip',
                  '将 dorami-daily-brief/ 文件夹放入工具的 skills 目录',
                  '重启工具后 Skill 即可使用',
                ].map((step, i) => (
                  <li key={i} className="flex gap-2.5 text-xs text-slate-500">
                    <span className="shrink-0 font-bold text-sky-400 tabular-nums">{i + 1}</span>
                    <span>{step}</span>
                  </li>
                ))}
              </ol>
              <div className="pt-3 border-t border-[var(--dorami-border)]">
                <p className="form-label">Skills 目录参考</p>
                <div className="space-y-1">
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-500 font-medium">Claude Code</span>
                    <code className="text-slate-500 font-mono">~/.claude/skills/</code>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-500 font-medium">其他工具</span>
                    <span className="text-slate-500">参考工具文档</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Online platforms */}
            <div className="rounded-[var(--r-card)] border border-[var(--dorami-border)] p-4 hover:border-[var(--dorami-border-strong)] transition-colors">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-7 h-7 rounded-[var(--r-control)] bg-violet-50 flex items-center justify-center">
                  <Globe className="w-3.5 h-3.5 text-violet-600" />
                </div>
                <p className="text-sm font-bold text-slate-700">在线 Agent 平台</p>
              </div>
              <div className="flex flex-wrap gap-1 mb-4">
                {ONLINE_TOOLS.map(t => (
                  <span key={t} className="status-badge min-h-[22px] bg-violet-50 text-violet-600 border-violet-100">{t}</span>
                ))}
              </div>
              <ol className="space-y-2 mb-4">
                {[
                  '下载并解压，用文本编辑器打开 SKILL.md',
                  '复制文件全部内容',
                  '粘贴到平台的 System Prompt 或项目指令配置中',
                ].map((step, i) => (
                  <li key={i} className="flex gap-2.5 text-xs text-slate-500">
                    <span className="shrink-0 font-bold text-violet-400 tabular-nums">{i + 1}</span>
                    <span>{step}</span>
                  </li>
                ))}
              </ol>
              <div className="pt-3 border-t border-[var(--dorami-border)]">
                <p className="form-label">配置位置参考</p>
                <div className="space-y-1">
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-500 font-medium">Claude.ai</span>
                    <span className="text-slate-500">项目设置 → Instructions</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-500 font-medium">Coze</span>
                    <span className="text-slate-500">Bot → Personality</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
        </div>
      </section>

      {/* ── 分区 ②：面向脚本与自动化（个人订阅接口）─────────────────── */}
      <section className="space-y-3">
        <GroupHeader accent="bg-amber-500" title="面向脚本与自动化" hint="HTTP 拉取接口，供非 Agent 客户端使用" />
        <FeedAccessSection showToast={showToast} />
      </section>

      {/* 非管理员（读者）：仅给一个日报订阅指引（无管理控件） */}
      {!canManage && (
        <section className="space-y-3">
          <GroupHeader accent="bg-emerald-500" title="AI 资讯日报" hint="订阅哆啦美自动生成的每日精选" />
          <DailyBriefPanel showToast={showToast} collectorEnabled={collectorEnabled} isAdmin={isAdmin} />
        </section>
      )}
        </div>
      )}
    </div>
  );
}
