import { useState, useEffect, useRef } from 'react';
import { Plug2, Copy, Check, Bot, Download, Terminal, Globe } from 'lucide-react';
import { fetchMcpStatus, toggleMcp } from '../api';

const TOOL_CARDS = [
  {
    name: 'search_articles',
    params: 'query, top_k?, content_type?, source_id?, publish_date_gte?, distance_threshold?',
    desc: '语义向量搜索，支持中英文，按相关性排序。适合主题查询，如「最近的具身智能资讯」。',
  },
  {
    name: 'browse_articles',
    params: 'source_id?, content_type?, publish_date_start?, publish_date_end?, has_content?, limit?, skip?',
    desc: '条件过滤浏览，按来源/类型/日期区间筛选。适合「Anthropic最新动态」或生成日报。',
  },
  {
    name: 'get_article',
    params: 'article_id: str',
    desc: '按 ID 获取单篇文章完整内容（含正文和扩展元数据）。',
  },
  {
    name: 'list_sources',
    params: '(无参数)',
    desc: '列出所有数据来源，获取可用的 source_id 和 content_type，建议首先调用。',
  },
  {
    name: 'get_rag_context',
    params: 'query, top_k?, max_chars?, distance_threshold?, content_type?, source_id?, publish_date_gte?',
    desc: '组装格式化 RAG 上下文字符串，可直接拼入 LLM System Prompt。',
  },
];

const LOCAL_TOOLS = ['Claude Code', 'Cursor', 'Codex', 'OpenCode'];
const ONLINE_TOOLS = ['Claude.ai Projects', 'Dify', 'Coze'];

export default function MCPTab({ showToast }) {
  const [status, setStatus] = useState(null);
  const [toggling, setToggling] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copiedJson, setCopiedJson] = useState(false);
  const showToastRef = useRef(showToast);
  useEffect(() => { showToastRef.current = showToast; }, [showToast]);

  useEffect(() => {
    fetchMcpStatus()
      .then(setStatus)
      .catch(() => setStatus({ enabled: false, url: null }));
  }, []);

  const handleToggle = async () => {
    setToggling(true);
    try {
      const data = await toggleMcp();
      setStatus(prev => ({ ...prev, enabled: data.enabled }));
      showToast(data.enabled ? 'MCP Server 已启动' : 'MCP Server 已停止',
                data.enabled ? 'success' : 'info');
    } catch {
      showToast('切换失败，请重试', 'error');
    } finally {
      setToggling(false);
    }
  };

  const handleCopy = () => {
    if (!status?.enabled || !status?.url) return;
    navigator.clipboard.writeText(status.url).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const mcpUrl = status?.url ?? 'http://127.0.0.1:8088/mcp';
  const mcpJson = JSON.stringify({
    mcpServers: {
      'dorami-archive': { type: 'http', url: mcpUrl },
    },
  }, null, 2);

  const handleCopyJson = () => {
    navigator.clipboard.writeText(mcpJson).then(() => {
      setCopiedJson(true);
      setTimeout(() => setCopiedJson(false), 2000);
    });
  };

  const handleDownload = (url, filename) => {
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
  };

  const enabled = status?.enabled ?? false;

  return (
    <div className="space-y-6">
      <div className="page-header">
        <div className="page-heading">
          <h2 className="page-title">接入集成</h2>
          <p className="page-subtitle mt-3 max-w-3xl">通过 MCP 与 Skill 把归档中枢接入本地工具和在线 Agent 平台，让检索、浏览与日报生成成为可复用能力。</p>
        </div>
      </div>
      {/* ── HERO ─────────────────────────────────────────────────── */}
      <div className="integration-hero relative overflow-hidden rounded-[14px] p-7 shadow-lg shadow-blue-500/10">
        {/* Dot-grid texture */}
        <div
          className="absolute inset-0 opacity-[0.07]"
          style={{
            backgroundImage: 'radial-gradient(circle, #ffffff 1px, transparent 1px)',
            backgroundSize: '24px 24px',
          }}
        />
        {/* Top-right glow */}
        <div className="absolute -top-16 -right-16 w-64 h-64 rounded-full opacity-20"
          style={{ background: 'radial-gradient(circle, #818cf8, transparent 70%)' }} />

        <div className="relative">
          <p className="integration-kicker mb-1">
            Integration Hub · 接入集成
          </p>
          <h2 className="text-xl font-bold text-white mb-1">扩展你的 Agent 能力</h2>
          <p className="integration-lede mb-6">
            通过 MCP 实时访问归档数据，或下载 Skill 让 Agent 自动生成 AI 资讯日报。
          </p>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {/* MCP card */}
            <div className="integration-card">
              <div className="flex items-start justify-between mb-4">
                <div className="flex items-center gap-3">
                  <div className="integration-icon integration-icon-sky">
                    <Plug2 className="w-4 h-4" />
                  </div>
                  <div>
                    <p className="integration-card-title">MCP Server</p>
                    <p className="integration-card-meta">实时数据接入</p>
                  </div>
                </div>
                {status === null ? (
                  <div className="h-2 w-2 rounded-full bg-slate-600 mt-1" />
                ) : enabled ? (
                  <span className="relative flex h-2.5 w-2.5 mt-1">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                    <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-400" />
                  </span>
                ) : (
                  <div className="h-2.5 w-2.5 rounded-full bg-red-400/70 mt-1" />
                )}
              </div>
              <p className="integration-card-copy mb-4">
                {TOOL_CARDS.length} 个工具，支持语义搜索、条件浏览和 RAG 上下文组装。
              </p>
              <button
                onClick={handleToggle}
                disabled={toggling || status === null}
                className={`integration-button w-full ${enabled ? 'integration-button-danger' : 'integration-button-primary'}`}
              >
                {toggling ? '处理中…' : enabled ? '停止 MCP' : '启动 MCP'}
              </button>
            </div>

            {/* Skill card */}
            <div className="integration-card">
              <div className="flex items-start justify-between mb-4">
                <div className="flex items-center gap-3">
                  <div className="integration-icon integration-icon-violet">
                    <Bot className="w-4 h-4" />
                  </div>
                  <div>
                    <p className="integration-card-title">AI 日报 Skill</p>
                    <p className="integration-card-meta">智能日报生成</p>
                  </div>
                </div>
                <span className="integration-version">
                  v1
                </span>
              </div>
              <p className="integration-card-copy mb-4">
                一句话生成结构化日报，支持 Claude Code、Cursor、Dify 等主流 Agent 平台。
              </p>
              <button
                onClick={() => handleDownload('/api/skill/daily-brief', 'dorami-daily-brief.zip')}
                className="integration-button integration-button-secondary w-full"
              >
                <Download className="w-3.5 h-3.5" />
                下载 Skill 包
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* ── MCP DETAILS ──────────────────────────────────────────── */}
      <div className="surface-card rounded-[14px] overflow-hidden">
        <div className="flex items-center gap-3 px-6 py-4 border-b border-slate-100">
          <div className="w-1 h-5 rounded-full bg-sky-500" />
          <h3 className="section-title">MCP 配置详情</h3>
          <span className="ml-auto text-xs text-slate-400 font-medium">
            {status === null ? '…' : enabled ? '● 运行中' : '○ 已停止'}
          </span>
        </div>

        <div className="p-6 space-y-5">
          {/* URL */}
          <div>
            <p className="form-label">接入地址</p>
            <div className={`flex items-center gap-3 px-4 py-3 rounded-xl border bg-slate-50 transition-opacity ${!enabled && 'opacity-50'}`}>
              <code className="flex-1 text-sm font-mono text-slate-700 break-all select-all">
                {status?.url ?? 'http://127.0.0.1:8088/mcp'}
              </code>
              <button
                onClick={handleCopy}
                disabled={!enabled}
                title={enabled ? '复制 URL' : 'MCP 未运行'}
                className="shrink-0 p-1.5 rounded-lg hover:bg-slate-200 transition-colors disabled:cursor-not-allowed"
              >
                {copied
                  ? <Check className="w-4 h-4 text-emerald-500" />
                  : <Copy className="w-4 h-4 text-slate-400" />}
              </button>
            </div>
            {!enabled && (
              <p className="tiny-meta mt-1.5">启动 MCP Server 后方可复制接入地址</p>
            )}
          </div>

          {/* JSON config */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <p className="form-label mb-0">客户端配置（JSON）</p>
              <button
                onClick={handleCopyJson}
                className="action-button action-button-quiet min-h-[28px] px-2 text-xs"
              >
                {copiedJson ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
                {copiedJson ? '已复制' : '复制'}
              </button>
            </div>
            <pre className="text-xs font-mono bg-slate-950 text-slate-300 rounded-xl p-4 overflow-x-auto leading-relaxed select-all">{mcpJson}</pre>
            <p className="tiny-meta mt-1.5">
              将以上配置合并到 Claude Code 的 <code className="bg-slate-100 px-1 rounded">~/.claude/settings.json</code>、Claude Desktop 的 <code className="bg-slate-100 px-1 rounded">claude_desktop_config.json</code> 或其他支持 MCP HTTP 协议的客户端配置文件中。
            </p>
          </div>

          {/* Tools */}
          <div>
            <p className="form-label">
              可用工具 <span className="font-normal normal-case text-slate-400">({TOOL_CARDS.length} 个)</span>
            </p>
            <div className="divide-y divide-slate-100 rounded-xl border border-slate-100 overflow-hidden">
              {TOOL_CARDS.map(tool => (
                <div key={tool.name} className="flex gap-4 px-4 py-3 bg-slate-50 hover:bg-slate-100/80 transition-colors">
                  <div className="shrink-0 mt-[3px] w-1.5 h-1.5 rounded-full bg-sky-400" />
                  <div className="min-w-0">
                    <div className="flex items-baseline gap-2 flex-wrap mb-0.5">
                      <code className="text-xs font-bold text-sky-700">{tool.name}</code>
                      <span className="tiny-meta font-mono">{tool.params}</span>
                    </div>
                    <p className="text-xs text-slate-500 leading-relaxed">{tool.desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* ── SKILL INSTALLATION ───────────────────────────────────── */}
      <div className="surface-card rounded-[14px] overflow-hidden">
        <div className="flex items-center gap-3 px-6 py-4 border-b border-slate-100">
          <div className="w-1 h-5 rounded-full bg-violet-500" />
          <h3 className="section-title">Skill 安装指南</h3>
          <button
            onClick={() => handleDownload('/api/skill/daily-brief', 'dorami-daily-brief.zip')}
            className="action-button action-button-secondary ml-auto min-h-[34px] px-3 text-xs text-violet-700"
          >
            <Download className="w-3.5 h-3.5" />
            下载 Skill 包
          </button>
        </div>

        <div className="p-6">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {/* Local tools */}
            <div className="rounded-xl border border-slate-200 p-4 hover:border-slate-300 transition-colors">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-7 h-7 rounded-lg bg-slate-100 flex items-center justify-center">
                  <Terminal className="w-3.5 h-3.5 text-slate-600" />
                </div>
                <p className="text-sm font-bold text-slate-700">本地 AI 工具</p>
              </div>
              <div className="flex flex-wrap gap-1 mb-4">
                {LOCAL_TOOLS.map(t => (
                  <span key={t} className="status-badge min-h-[22px] bg-slate-100 text-slate-500 border-slate-200">{t}</span>
                ))}
              </div>
              <ol className="space-y-2 mb-4">
                {[
                  '下载并解压 dorami-daily-brief.zip',
                  '将 dorami-daily-brief/ 文件夹放入工具的 skills 目录',
                  '重启工具后 Skill 即可使用',
                ].map((step, i) => (
                  <li key={i} className="flex gap-2.5 text-xs text-slate-600">
                    <span className="shrink-0 font-bold text-slate-300 tabular-nums">{i + 1}</span>
                    <span>{step}</span>
                  </li>
                ))}
              </ol>
              <div className="pt-3 border-t border-slate-100">
                <p className="form-label">Skills 目录参考</p>
                <div className="space-y-1">
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-400 font-medium">Claude Code</span>
                    <code className="text-slate-500 font-mono">~/.claude/skills/</code>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-400 font-medium">其他工具</span>
                    <span className="text-slate-400">参考工具文档</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Online platforms */}
            <div className="rounded-xl border border-slate-200 p-4 hover:border-slate-300 transition-colors">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-7 h-7 rounded-lg bg-slate-100 flex items-center justify-center">
                  <Globe className="w-3.5 h-3.5 text-slate-600" />
                </div>
                <p className="text-sm font-bold text-slate-700">在线 Agent 平台</p>
              </div>
              <div className="flex flex-wrap gap-1 mb-4">
                {ONLINE_TOOLS.map(t => (
                  <span key={t} className="status-badge min-h-[22px] bg-slate-100 text-slate-500 border-slate-200">{t}</span>
                ))}
              </div>
              <ol className="space-y-2 mb-4">
                {[
                  '下载并解压，用文本编辑器打开 SKILL.md',
                  '复制文件全部内容',
                  '粘贴到平台的 System Prompt 或项目指令配置中',
                ].map((step, i) => (
                  <li key={i} className="flex gap-2.5 text-xs text-slate-600">
                    <span className="shrink-0 font-bold text-slate-300 tabular-nums">{i + 1}</span>
                    <span>{step}</span>
                  </li>
                ))}
              </ol>
              <div className="pt-3 border-t border-slate-100">
                <p className="form-label">配置位置参考</p>
                <div className="space-y-1">
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-400 font-medium">Claude.ai</span>
                    <span className="text-slate-500">项目设置 → Instructions</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-400 font-medium">Dify</span>
                    <span className="text-slate-500">Chatbot → 系统提示</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-slate-400 font-medium">Coze</span>
                    <span className="text-slate-500">Bot → Personality</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
