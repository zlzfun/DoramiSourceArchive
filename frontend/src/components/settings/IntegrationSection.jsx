import { useState } from 'react';
import { Check, Copy, Loader2, Plug2 } from 'lucide-react';
import { MCP_URL } from '../../config';
import { toggleMcp } from '../../api';
import { copyText } from '../../utils/clipboard';
import { runAction } from '../../utils/runAction';
import { SectionHeading } from './SectionPrimitives';

// 接入集成（MCP + Skill）：MCP Server 启停与接入地址。完整配置见「接入集成」页。
export default function IntegrationSection({ showToast, mcpStatus, canToggle, onMcpToggled }) {
  const [toggling, setToggling] = useState(false);
  const [copied, setCopied] = useState(false);

  const mcpUrl = mcpStatus?.url ?? MCP_URL;
  const enabled = mcpStatus?.enabled ?? false;

  const handleCopy = () => runAction(() => copyText(mcpUrl), {
    showToast,
    error: '复制失败',
    onSuccess: () => { setCopied(true); setTimeout(() => setCopied(false), 1800); },
  });

  const handleToggle = async () => {
    setToggling(true);
    try {
      const data = await toggleMcp();
      onMcpToggled?.(data.enabled);
      window.dispatchEvent(new CustomEvent('dorami-mcp-changed', { detail: { enabled: data.enabled } }));
      showToast(data.enabled ? 'MCP Server 已启动' : 'MCP Server 已停止', data.enabled ? 'success' : 'info');
    } catch {
      showToast('切换失败，请重试', 'error');
    } finally {
      setToggling(false);
    }
  };

  return (
    <div>
      <SectionHeading title="接入集成" hint="管理 MCP Server 启停与接入地址。完整客户端配置、工具说明、Skill 安装指南见「接入集成」页。" />

      <div className="surface-card rounded-[var(--r-card)] p-4">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <Plug2 className="h-4 w-4 text-sky-500" />
            <span className="text-sm font-bold text-slate-700">MCP Server</span>
            <span className={`text-xs font-bold ${mcpStatus === null ? 'text-slate-500' : enabled ? 'text-emerald-500' : 'text-rose-500'}`}>
              {mcpStatus === null ? '…' : enabled ? '● 运行中' : '○ 已停止'}
            </span>
          </div>
          {canToggle ? (
            <button onClick={handleToggle} disabled={toggling || mcpStatus === null} className={`action-button text-xs ${enabled ? 'action-button-danger' : 'action-button-success'}`}>
              {toggling ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plug2 className="h-3.5 w-3.5" />}
              {enabled ? '停止 MCP' : '启动 MCP'}
            </button>
          ) : (
            <span className="tiny-meta">由管理员启停</span>
          )}
        </div>

        <p className="tiny-meta mb-1 mt-3">接入地址</p>
        <div className="flex items-center gap-2 rounded-[var(--r-control)] border border-[var(--dorami-border)] bg-[var(--dorami-soft)] px-3 py-2">
          <code className="min-w-0 flex-1 truncate text-xs font-bold text-slate-500" title={mcpUrl}>{mcpUrl}</code>
          <button onClick={handleCopy} className="shrink-0 text-slate-500 hover:text-slate-700" title="复制 MCP 地址" aria-label="复制 MCP 地址">
            {copied ? <Check className="h-4 w-4 text-emerald-500" /> : <Copy className="h-4 w-4" />}
          </button>
        </div>
      </div>
    </div>
  );
}
