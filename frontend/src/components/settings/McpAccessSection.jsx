import { useEffect, useRef, useState } from 'react';
import { Check, ChevronRight, Copy } from 'lucide-react';
import { fetchMcpStatus, fetchVectorStats, toggleMcp } from '../../api';
import { MCP_URL } from '../../config';
import { copyText } from '../../utils/clipboard';
import { runAction } from '../../utils/runAction';

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

/** 复制小钮：图标随复制状态在 Copy / Check 间切换。 */
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

/**
 * MCP 接入（设置柜·接入集成组）：端点 + 客户端配置 + 工具清单。
 * 前身是接入集成页签的 MCP 大卡（并入设置波）；原「服务」区随开关并入退役（用户拍板）——
 * canManage（admin）时头部渲染启停 switch（switch 即状态，替代状态章），
 * 底部追加向量索引只读统计行（RAG 工具的底层索引状态，构建管理仍归知识台账）。
 */
export default function McpAccessSection({ showToast, ragEnabled = false, canManage = false, onClose }) {
  const [status, setStatus] = useState(null);
  const [codeKind, setCodeKind] = useState('claude');   // claude | opencode | codex | url
  const [stoppedConfigOpen, setStoppedConfigOpen] = useState(false);  // 停止时接入配置默认折叠
  const [copiedKey, setCopiedKey] = useState('');
  const [toggling, setToggling] = useState(false);
  const [vectorStats, setVectorStats] = useState(null);

  const showToastRef = useRef(showToast);
  useEffect(() => { showToastRef.current = showToast; }, [showToast]);

  useEffect(() => {
    fetchMcpStatus()
      .then(setStatus)
      .catch(() => setStatus({ enabled: false, url: null }));
  }, []);

  // 其它入口启停 MCP 后广播该事件,本区状态跟随同步(事件通道保留,当前唯一开关就在本区)。
  useEffect(() => {
    const handleMcpChanged = (event) => {
      setStatus(prev => ({ ...(prev || {}), enabled: event.detail?.enabled ?? false }));
    };
    window.addEventListener('dorami-mcp-changed', handleMcpChanged);
    return () => window.removeEventListener('dorami-mcp-changed', handleMcpChanged);
  }, []);

  useEffect(() => {
    if (!(canManage && ragEnabled)) return undefined;
    let alive = true;
    fetchVectorStats().then(d => { if (alive) setVectorStats(d); }).catch(() => {});
    return () => { alive = false; };
  }, [canManage, ragEnabled]);

  const handleToggle = async () => {
    setToggling(true);
    try {
      const data = await toggleMcp();
      setStatus(prev => ({ ...(prev || {}), enabled: data.enabled }));
      window.dispatchEvent(new CustomEvent('dorami-mcp-changed', { detail: { enabled: data.enabled } }));
      showToastRef.current?.(data.enabled ? '已启动 MCP 服务' : '已停止 MCP 服务', data.enabled ? 'success' : 'info');
    } catch {
      showToastRef.current?.('切换失败,请重试', 'error');
    } finally {
      setToggling(false);
    }
  };

  // 台账跳转:关设置柜 + 直切知识台账(构建/重索引/自动向量化归总账条独管)
  const goLedger = () => {
    window.location.hash = '#/data';
    onClose?.();
  };

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
          <code className="font-mono">bearer_token_env_var</code> 填的是<b>环境变量名</b>：把「聚合接口」页的 dfeed_ 令牌 export 到该变量即可，令牌值不落配置文件。
        </p>
      ) : codeKind !== 'url' && (
        <p className="tiny-meta mt-2">
          把 <code className="font-mono">Authorization</code> 里的 <code className="font-mono">dfeed_你的令牌</code> 换成「聚合接口」页的 dfeed_ 令牌（或单订阅 <code className="font-mono">dsub_</code>）；连接后按你的订阅范围自动过滤。字段名随客户端而异，请以其文档为准。
        </p>
      )}
    </>
  );

  return (
    <div>
      <div className="card-head">
        <span className="sett-lbl">MCP 服务</span>
        {canManage ? (
          /* admin:启停 switch 即状态,不再叠状态章(原「服务」区并入,用户拍板) */
          <button
            type="button"
            role="switch"
            aria-checked={enabled}
            aria-label="MCP 服务开关"
            disabled={toggling || status === null}
            onClick={handleToggle}
            className={`ledger-switch ${enabled ? 'is-on' : ''}`}
          />
        ) : (
          <span className={`stamp ${mcpStamp.cls}`}>{mcpStamp.label}</span>
        )}
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

      {/* admin+RAG:语义工具的底层索引状态(只读);构建/重索引/自动向量化归知识台账总账条独管 */}
      {canManage && ragEnabled && (
        <div className="sett-row" style={{ marginTop: 10 }}>
          <span className="sett-id">
            <span className="sett-lbl">向量索引</span>
            <div className="sett-sub">
              <span className="sett-stat">
                {vectorStats === null ? '统计加载中…' : `${Number(vectorStats.total_vectors ?? 0).toLocaleString()} 块`}
                <span className="mx-1">·</span>BAAI/bge-m3
              </span>
              —— 构建、重索引与自动向量化在{' '}
              <button type="button" className="sett-link" onClick={goLedger}>知识台账 → 总账条</button>
              {' '}统一管理
            </div>
          </span>
        </div>
      )}
    </div>
  );
}
