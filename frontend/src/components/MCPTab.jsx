import { useState, useEffect, useCallback } from 'react';
import { Plug2, Copy, Check, Circle, Bot, Download, FileCode, FileText } from 'lucide-react';
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

const DAILY_BRIEF_STEPS = [
  {
    step: '1',
    title: '下载 Skill.md',
    desc: '点击下方「下载 Skill 文件」，获取 daily_brief_skill.md。',
  },
  {
    step: '2',
    title: '配置到 Agent / LLM 平台',
    desc: '将 Skill.md 内容粘贴到 Claude、Dify、Coze 等平台的 System Prompt 或 Skill 配置中。',
  },
  {
    step: '3',
    title: '对话生成日报',
    desc: '直接对 Agent 说「生成今天的 AI 资讯日报」，或指定日期，如「生成最近3天的日报，只要论文和开源」。',
  },
  {
    step: '4',
    title: '或直接运行 Python 脚本',
    desc: '下载 daily_brief.py，安装 httpx 后执行：python daily_brief.py --days 3，无需 LLM 即可生成 Markdown 日报。',
  },
];

export default function MCPTab({ showToast }) {
  const [status, setStatus] = useState(null);
  const [toggling, setToggling] = useState(false);
  const [copied, setCopied] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await fetchMcpStatus());
    } catch {
      showToast('无法获取 MCP 状态', 'error');
    }
  }, [showToast]);

  useEffect(() => { loadStatus(); }, [loadStatus]);

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

  const handleDownload = (url, filename) => {
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
  };

  const enabled = status?.enabled ?? false;

  return (
    <div className="space-y-6">
      {/* Status & Control */}
      <div className="bg-white rounded-2xl border border-slate-200/60 shadow-sm p-6">
        <h2 className="text-lg font-bold text-slate-800 mb-5 flex items-center gap-2">
          <Plug2 className="w-5 h-5 text-blue-600" />
          MCP Server 状态
        </h2>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            {enabled ? (
              <span className="relative flex h-3 w-3">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-3 w-3 bg-emerald-500" />
              </span>
            ) : (
              <Circle className="w-3 h-3 text-red-400 fill-red-400" />
            )}
            <span className="font-semibold text-slate-700">
              {status === null
                ? '加载中...'
                : enabled
                ? 'MCP Server 运行中'
                : 'MCP Server 已停止'}
            </span>
          </div>
          <button
            onClick={handleToggle}
            disabled={toggling || status === null}
            className={`px-4 py-2 rounded-lg text-sm font-bold transition-all border disabled:opacity-50 ${
              enabled
                ? 'bg-red-50 text-red-600 hover:bg-red-100 border-red-200'
                : 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100 border-emerald-200'
            }`}
          >
            {toggling ? '处理中...' : enabled ? '停止 MCP' : '启动 MCP'}
          </button>
        </div>
      </div>

      {/* MCP URL */}
      <div className="bg-white rounded-2xl border border-slate-200/60 shadow-sm p-6">
        <h2 className="text-lg font-bold text-slate-800 mb-4">接入地址</h2>
        <div
          className={`flex items-center gap-3 p-3 rounded-xl border transition-opacity ${
            enabled ? 'bg-slate-50 border-slate-200' : 'bg-slate-100 border-slate-200 opacity-50'
          }`}
        >
          <code className="flex-1 text-sm font-mono text-slate-700 select-all break-all">
            {status?.url ?? 'http://127.0.0.1:8088/mcp'}
          </code>
          <button
            onClick={handleCopy}
            disabled={!enabled}
            title={enabled ? '复制 URL' : 'MCP 当前未运行'}
            className="p-1.5 rounded-lg hover:bg-slate-200 transition-colors disabled:cursor-not-allowed shrink-0"
          >
            {copied
              ? <Check className="w-4 h-4 text-emerald-600" />
              : <Copy className="w-4 h-4 text-slate-500" />}
          </button>
        </div>
        {!enabled && (
          <p className="text-xs text-slate-400 mt-2">启动 MCP Server 后方可复制接入地址</p>
        )}
        <p className="text-xs text-slate-400 mt-2">
          在 Agent 或 Dify 中配置 MCP URL 后，即可调用以下工具查询归档内容。
        </p>
      </div>

      {/* Tools */}
      <div className="bg-white rounded-2xl border border-slate-200/60 shadow-sm p-6">
        <h2 className="text-lg font-bold text-slate-800 mb-4">
          可用工具
          <span className="ml-2 text-sm font-normal text-slate-400">({TOOL_CARDS.length} 个)</span>
        </h2>
        <div className="space-y-3">
          {TOOL_CARDS.map(tool => (
            <div key={tool.name} className="p-4 rounded-xl bg-slate-50 border border-slate-100">
              <div className="flex items-start justify-between gap-3 mb-1">
                <code className="text-sm font-bold text-blue-700">{tool.name}</code>
                <span className="text-[11px] text-slate-400 font-mono text-right leading-relaxed">
                  {tool.params}
                </span>
              </div>
              <p className="text-sm text-slate-600">{tool.desc}</p>
            </div>
          ))}
        </div>
      </div>

      {/* AI Daily Brief Skill */}
      <div className="bg-white rounded-2xl border border-slate-200/60 shadow-sm p-6">
        <h2 className="text-lg font-bold text-slate-800 mb-2 flex items-center gap-2">
          <Bot className="w-5 h-5 text-violet-600" />
          AI日报 Skill
        </h2>
        <p className="text-sm text-slate-500 mb-5">
          将归档内容一键生成结构化 Markdown 日报。提供两种使用方式：配置到 Claude / Dify / Coze 等 Agent 平台的 Skill 文件，或直接运行独立 Python 脚本（无需 LLM）。
        </p>

        {/* Download cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
          {/* Skill.md */}
          <div className="flex flex-col gap-3 p-4 rounded-xl bg-violet-50 border border-violet-100">
            <div className="flex items-center gap-2">
              <FileText className="w-5 h-5 text-violet-600 shrink-0" />
              <div>
                <p className="text-sm font-bold text-violet-800">daily_brief_skill.md</p>
                <p className="text-xs text-violet-500">Agent / LLM 平台 Skill 文件</p>
              </div>
            </div>
            <p className="text-xs text-slate-500 leading-relaxed">
              System Prompt 格式，包含完整的日报生成指令、分类规则和输出格式。适配 Claude、Dify、Coze 等主流平台。
            </p>
            <button
              onClick={() => handleDownload('/api/skill/daily-brief.md', 'daily_brief_skill.md')}
              className="mt-auto flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-sm font-bold transition-colors"
            >
              <Download className="w-4 h-4" />
              下载 Skill 文件
            </button>
          </div>

          {/* Python script */}
          <div className="flex flex-col gap-3 p-4 rounded-xl bg-blue-50 border border-blue-100">
            <div className="flex items-center gap-2">
              <FileCode className="w-5 h-5 text-blue-600 shrink-0" />
              <div>
                <p className="text-sm font-bold text-blue-800">daily_brief.py</p>
                <p className="text-xs text-blue-500">独立 Python 脚本</p>
              </div>
            </div>
            <p className="text-xs text-slate-500 leading-relaxed">
              无需 LLM，直接调用 REST API 生成 Markdown 日报。支持日期范围、内容类型过滤和多语言输出。
            </p>
            <button
              onClick={() => handleDownload('/api/skill/daily-brief.py', 'daily_brief.py')}
              className="mt-auto flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-bold transition-colors"
            >
              <Download className="w-4 h-4" />
              下载 Python 脚本
            </button>
          </div>
        </div>

        {/* Usage steps */}
        <div>
          <p className="text-sm font-semibold text-slate-600 mb-3">使用说明</p>
          <div className="space-y-2">
            {DAILY_BRIEF_STEPS.map(item => (
              <div key={item.step} className="flex items-start gap-3 p-3 rounded-xl bg-slate-50 border border-slate-100">
                <span className="flex-shrink-0 w-6 h-6 rounded-full bg-slate-200 text-slate-600 text-xs font-bold flex items-center justify-center">
                  {item.step}
                </span>
                <div>
                  <p className="text-sm font-semibold text-slate-700">{item.title}</p>
                  <p className="text-xs text-slate-500 mt-0.5 leading-relaxed">{item.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
