import { useEffect, useState } from 'react';
import { fetchVectorStats, toggleMcp } from '../../api';

// 服务(弹窗波,仅管理员):MCP 启停一行(switch 即状态,无冗余指示灯;地址与接入配置
// 见「接入集成」页)+ 向量索引只读统计行(构建/重索引/自动向量化归台账总账条独管,
// 消除 L4 遗留的双入口)。原 IntegrationSection + VectorSection 随此退役。
export default function ServiceSection({ showToast, mcpStatus, onMcpToggled, ragEnabled, onClose }) {
  const [toggling, setToggling] = useState(false);
  const [stats, setStats] = useState(null);

  const enabled = mcpStatus?.enabled ?? false;

  useEffect(() => {
    if (!ragEnabled) return undefined;
    let alive = true;
    fetchVectorStats().then(d => { if (alive) setStats(d); }).catch(() => {});
    return () => { alive = false; };
  }, [ragEnabled]);

  const handleToggle = async () => {
    setToggling(true);
    try {
      const data = await toggleMcp();
      onMcpToggled?.(data.enabled);
      window.dispatchEvent(new CustomEvent('dorami-mcp-changed', { detail: { enabled: data.enabled } }));
      showToast(data.enabled ? '已启动 MCP 服务' : '已停止 MCP 服务', data.enabled ? 'success' : 'info');
    } catch {
      showToast('切换失败,请重试', 'error');
    } finally {
      setToggling(false);
    }
  };

  const goLedger = () => {
    window.location.hash = '#/data';
    onClose?.();
  };

  return (
    <div>
      <div className="sett-row">
        <span className="sett-id">
          <span className="sett-lbl">MCP 服务</span>
          <div className="sett-sub">停止后 Agent 将无法调用检索与浏览工具;接入配置与地址见「接入集成」页</div>
        </span>
        <span className="sett-ctl">
          <button
            type="button"
            role="switch"
            aria-checked={enabled}
            aria-label="MCP 服务开关"
            disabled={toggling || mcpStatus === null}
            onClick={handleToggle}
            className={`ledger-switch ${enabled ? 'is-on' : ''}`}
          />
        </span>
      </div>

      {ragEnabled && (
        <div className="sett-row">
          <span className="sett-id">
            <span className="sett-lbl">向量索引</span>
            <div className="sett-sub">
              <span className="sett-stat">
                {stats === null ? '统计加载中…' : `${Number(stats.total_vectors ?? 0).toLocaleString()} 块`}
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
