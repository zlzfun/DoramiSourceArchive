import { useState, useEffect, useRef } from 'react';
import { Check, ChevronRight, Copy, Download } from 'lucide-react';
import { fetchMcpStatus, getLLMConfig } from '../api';
import { MCP_URL } from '../config';
import { copyText } from '../utils/clipboard';
import { runAction } from '../utils/runAction';
import FeedAccessSection from './FeedAccessSection';
import DailyBriefPanel from './DailyBriefPanel';

// 五个 MCP 工具，顺序照样页(list_sources 建议首先调用 → 语义检索工具垫后)。
// requiresRag 的两个语义工具在 RAG 关闭时整行灰显 + 右侧翻为「RAG 未启用」章。
const TOOLS = [
  {
    name: 'list_sources',
    desc: '列出全部数据来源与内容类型，建议首先调用。',
    params: '（无参数）',
  },
  {
    name: 'browse_articles',
    desc: '按来源 / 类型 / 日期区间条件过滤浏览，传入 dfeed_ 后限定到你的订阅范围。',
    params: 'source_id?, content_type?, publish_date_start?, publish_date_end?, has_content?, limit?, skip?, subscription_token?',
  },
  {
    name: 'get_article',
    desc: '按 ID 取单篇完整内容（含正文与扩展元数据）。',
    params: 'article_id, subscription_token?',
  },
  {
    name: 'search_articles',
    desc: '语义向量搜索，中英跨语，按相关性排序。',
    params: 'query, top_k?, content_type?, source_id?, publish_date_gte?, distance_threshold?, subscription_token?',
    requiresRag: true,
  },
  {
    name: 'get_rag_context',
    desc: '组装可直接拼入 System Prompt 的 RAG 上下文串。',
    params: 'query, top_k?, max_chars?, distance_threshold?, content_type?, source_id?, publish_date_gte?, subscription_token?',
    requiresRag: true,
  },
];

// 配置格式与适用客户端(按格式联动,2026-07 查证):
// - mcpServers JSON 是 Claude Code 系事实标准,Claude Desktop/Cursor/Windsurf/Cline 同 schema;
// - Codex CLI 用 TOML([mcp_servers.<name>],远程服务器 url + bearer_token_env_var),不吃 mcpServers JSON;
// - OpenCode 用 opencode.json 的 mcp 键,自成一格;
// - URL 直连适用任何支持 streamable-HTTP 的 MCP 客户端(在线接入 Claude.ai Projects/Coze 也走它)。
const FORMAT_TARGETS = {
  claude: ['Claude Code', 'Claude Desktop', 'Cursor', 'Windsurf', 'Cline'],
  opencode: ['OpenCode'],
  codex: ['Codex CLI'],
  url: ['Claude.ai Projects', 'Coze'],
};

// SKILL.md 已是跨家开放标准(Agent Skills):Claude Code/Codex/OpenCode/Cursor/Gemini CLI 等均支持。
const SKILL_STEPS = [
  <>下载技能包 <code>dorami-daily-brief.zip</code></>,
  <>解压到工具的 skills 目录（如 Claude Code：<code>~/.claude/skills/</code>）</>,
  <>对 Agent 说「看看今天的 AI 日报」即可对话取用</>,
];

/** 复制小钮：图标随复制状态在 Copy / Check 间切换，成功走 §1 文案 toast。 */
function CopyBtn({ text, label, copiedKey, itemKey, onCopy, title }) {
  return (
    <button
      type="button"
      className="copybtn"
      title={title || `复制${label}`}
      onClick={() => onCopy(text, itemKey, label)}
      disabled={!text}
    >
      {copiedKey === itemKey ? <Check /> : <Copy />}
    </button>
  );
}

export default function MCPTab({ showToast, ragEnabled = false, collectorEnabled = false, isAdmin = false, onOpenModelConfig }) {
  const canManage = collectorEnabled && isAdmin;   // 管理员才见「AI 资讯日报」区与只读模型 chip

  const [status, setStatus] = useState(null);
  const [llmStatus, setLlmStatus] = useState(null);
  const [codeKind, setCodeKind] = useState('claude');   // MCP 代码块：claude | opencode | codex | url
  const [stoppedConfigOpen, setStoppedConfigOpen] = useState(false);  // 停止时接入配置默认折叠
  const [copiedKey, setCopiedKey] = useState('');

  const showToastRef = useRef(showToast);
  useEffect(() => { showToastRef.current = showToast; }, [showToast]);

  useEffect(() => {
    fetchMcpStatus()
      .then(setStatus)
      .catch(() => setStatus({ enabled: false, url: null }));
  }, []);

  useEffect(() => {
    if (!canManage) return;
    getLLMConfig().then(setLlmStatus).catch(() => {});
  }, [canManage]);

  // 设置面板启停 MCP 后广播该事件，同步已挂载页面的运行状态。
  useEffect(() => {
    const handleMcpChanged = (event) => {
      setStatus(prev => ({ ...(prev || {}), enabled: event.detail?.enabled ?? false }));
    };
    window.addEventListener('dorami-mcp-changed', handleMcpChanged);
    return () => window.removeEventListener('dorami-mcp-changed', handleMcpChanged);
  }, []);

  const mcpUrl = status?.url ?? MCP_URL;
  const mcpJson = JSON.stringify({
    mcpServers: {
      'dorami-archive': {
        type: 'http',
        url: mcpUrl,
        headers: { Authorization: 'Bearer dfeed_你的令牌' },
      },
    },
  }, null, 2);
  // OpenCode 用 mcp 字段组织远程 MCP,与 mcpServers 系不同构(字段随客户端版本可能有差异)
  const opencodeJson = JSON.stringify({
    mcp: {
      'dorami-archive': {
        type: 'remote',
        url: mcpUrl,
        enabled: true,
        headers: { Authorization: 'Bearer dfeed_你的令牌' },
      },
    },
  }, null, 2);
  // Codex CLI 用 TOML(~/.codex/config.toml):远程服务器 = url + bearer_token_env_var
  // (指向环境变量名,令牌值不落配置文件)
  const codexToml = [
    '# ~/.codex/config.toml',
    '[mcp_servers.dorami-archive]',
    `url = "${mcpUrl}"`,
    'bearer_token_env_var = "DORAMI_TOKEN"',
    '# 令牌放环境变量:export DORAMI_TOKEN=dfeed_你的令牌',
  ].join('\n');

  const handleCopy = (text, key, label) => runAction(() => copyText(text), {
    showToast: (m, t) => showToastRef.current?.(m, t),
    success: `已复制 ${label}`,
    error: '复制失败，请手动选择文本复制',
    onSuccess: () => { setCopiedKey(key); setTimeout(() => setCopiedKey(''), 1600); },
  });

  const handleDownloadSkill = () => {
    const a = document.createElement('a');
    a.href = '/api/skill/daily-brief';
    a.download = 'dorami-daily-brief.zip';
    a.click();
  };

  const enabled = status?.enabled ?? false;
  const mcpStamp = status === null
    ? { cls: 'stamp-idle', label: '检测中' }
    : enabled
      ? { cls: 'stamp-ok', label: '运行中' }
      : { cls: 'stamp-bad', label: '已停止' };
  const showConfig = enabled || stoppedConfigOpen;
  const codeText = codeKind === 'url' ? mcpUrl
    : codeKind === 'opencode' ? opencodeJson
    : codeKind === 'codex' ? codexToml
    : mcpJson;
  const llmOk = Boolean(llmStatus?.api_key_set);

  const copyProps = { copiedKey, onCopy: handleCopy };

  // 接入配置块：端点行 + 格式 mini-seg + 代码块。运行时直接展开，停止时折叠到展开器后。
  const configBlock = (
    <>
      <div className="endpoint">
        <span className="endpoint-method">POST</span>
        <span className="endpoint-url" title={mcpUrl}>{mcpUrl}</span>
        <CopyBtn text={mcpUrl} label="MCP 端点" itemKey="mcp-endpoint" {...copyProps} />
      </div>
      <div className="codeblock">
        {codeText}
        <CopyBtn
          text={codeText}
          label={codeKind === 'url' ? 'MCP 端点' : '客户端配置'}
          itemKey="mcp-code"
          {...copyProps}
        />
      </div>
      {codeKind === 'codex' ? (
        <p className="tiny-meta mt-2">
          <code className="font-mono">bearer_token_env_var</code> 填的是<b>环境变量名</b>：把右侧「个人聚合接口」的 dfeed_ 令牌 export 到该变量即可，令牌值不落配置文件。
        </p>
      ) : codeKind !== 'url' && (
        <p className="tiny-meta mt-2">
          把 <code className="font-mono">Authorization</code> 里的 <code className="font-mono">dfeed_你的令牌</code> 换成右侧「个人聚合接口」的 dfeed_ 令牌（或单订阅 <code className="font-mono">dsub_</code>）；连接后按你的订阅范围自动过滤。字段名随客户端而异，请以其文档为准。
        </p>
      )}
    </>
  );

  return (
    <div className="integ-page">
      <header className="page-head">
        <h1 className="page-title">接入集成</h1>
        {canManage && (
          <div className="page-head-actions">
            <button
              type="button"
              className="model-chip"
              title="前往运维管理配置模型"
              onClick={() => onOpenModelConfig?.()}
            >
              <i className={llmOk ? '' : 'is-off'} />模型 <b>{llmOk ? (llmStatus.model || '已配置') : '未配置'}</b>
            </button>
          </div>
        )}
      </header>

      {/* ════ 区 1：AI 资讯日报（仅管理员，置顶） ════ */}
      {canManage && (
        <>
          <div className="zone-head zone-head-first">
            <span className="zone-title">AI 资讯日报</span>
          </div>
          <DailyBriefPanel showToast={showToast} collectorEnabled={collectorEnabled} isAdmin={isAdmin} />
        </>
      )}

      {/* ════ 区 2：交付通道（全角色） ════ */}
      <div className={`zone-head ${canManage ? '' : 'zone-head-first'}`}>
        <span className="zone-title">交付通道</span>
        <span className="zone-hint">把内容接进你的 Agent、RSS 阅读器或工作流</span>
      </div>

      <div className="channels">
        {/* MCP 大卡：独占整行 */}
        <section className="surface-card card-pad">
          <div className="card-head">
            <span className="card-title">MCP 服务</span>
            <span className={`stamp ${mcpStamp.cls}`}>{mcpStamp.label}</span>
            {showConfig && (
              <span className="mini-seg" style={{ marginLeft: 'auto' }} role="group" aria-label="配置格式">
                {[['claude', 'Claude Code'], ['opencode', 'OpenCode'], ['codex', 'Codex'], ['url', 'URL']].map(([kind, label]) => (
                  <button
                    key={kind}
                    type="button"
                    className={`mini-seg-btn ${codeKind === kind ? 'is-on' : ''}`}
                    onClick={() => setCodeKind(kind)}
                  >
                    {label}
                  </button>
                ))}
              </span>
            )}
          </div>
          <p className="card-desc">标准 Model Context Protocol 端点，Agent 直接调用内容的检索与浏览工具。</p>

          {enabled ? configBlock : (
            <>
              <button
                type="button"
                className="integ-collapse-btn"
                onClick={() => setStoppedConfigOpen(o => !o)}
                aria-expanded={stoppedConfigOpen}
              >
                <ChevronRight
                  style={{ transform: stoppedConfigOpen ? 'rotate(90deg)' : 'none', transition: 'transform var(--motion-fast) var(--motion-ease)' }}
                />
                {status === null ? '正在检测服务状态…' : 'MCP 已停止，查看接入配置（地址与示例）'}
              </button>
              {stoppedConfigOpen && configBlock}
            </>
          )}

          {/* 适用面随所选格式联动(mcpServers JSON ≠ 万能:Codex 走 TOML、OpenCode 自成一格) */}
          <div className="targets">
            {codeKind === 'url' ? '在线接入' : '本格式适用'}
            {(FORMAT_TARGETS[codeKind] || FORMAT_TARGETS.claude).map(t => <span key={t} className="target-chip">{t}</span>)}
            {codeKind === 'url' && <>· 及任何支持 streamable-HTTP 的 MCP 客户端</>}
          </div>

          <div className="tools-head">
            <span className="tools-title">可用工具</span>
            <span className="tools-n">{TOOLS.length}</span>
            <span className="zone-hint" style={{ marginLeft: 'auto' }}>传入 dfeed_ 令牌即限定到订阅范围</span>
          </div>
          <div className="tools">
            {TOOLS.map(tool => {
              const off = tool.requiresRag && !ragEnabled;
              return (
                <div key={tool.name} className={`tool-row ${off ? 'is-off' : ''}`}>
                  <span className="tool-name">{tool.name}</span>
                  <span className="tool-desc">
                    {tool.desc}
                    <span className="p" title={tool.params}>{tool.params}</span>
                  </span>
                  {tool.requiresRag
                    ? <span className={`stamp ${ragEnabled ? 'stamp-ok' : 'stamp-idle'}`}>{ragEnabled ? 'RAG' : 'RAG 未启用'}</span>
                    : <span />}
                </div>
              );
            })}
          </div>

          <details className="scope-note">
            <summary>启停与取数范围</summary>
            <p>
              MCP 服务随后端进程启停，无需单独部署，启停由管理员统一控制。管理员会话检索全库；携带 dfeed_ / dsub_ 令牌的调用硬限定到该令牌的订阅范围（仅 <code className="font-mono">list_sources</code> 例外，可直接列目录）。管理员的 dfeed_ 令牌不受此限，检索全库。RAG 关闭时，两个语义工具返回结构化的「RAG disabled」而非报错。
            </p>
          </details>
        </section>

        {/* 次行：个人聚合接口 + Agent 技能包 并排等高 */}
        <div className="channels-pair">
          <FeedAccessSection showToast={showToast} isAdmin={isAdmin} />

          <section className="surface-card card-pad">
            <div className="card-head">
              <span className="card-title">Agent 技能包</span>
              <span className="target-chip">SKILL.md 开放标准</span>
            </div>
            <p className="card-desc">按本站地址模板化的日报技能，装进 Claude Code、Codex、OpenCode 等支持 Agent Skills 的工具即可对话取用每日资讯。</p>
            <div className="steps">
              {SKILL_STEPS.map((step, i) => <div key={i} className="step">{step}</div>)}
            </div>
            <div className="mt-4">
              <button type="button" onClick={handleDownloadSkill} className="action-button action-button-secondary min-h-[32px] px-3 text-xs">
                <Download className="w-3.5 h-3.5" />
                下载技能包
              </button>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
