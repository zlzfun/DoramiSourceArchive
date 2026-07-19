import { useEffect, useState } from 'react';
import { getLLMConfig } from '../api';
import DailyBriefPanel from './DailyBriefPanel';

/**
 * AI 日报页签（原「接入集成」页签瘦身改名,并入设置波）：
 * 交付通道三卡（MCP / 聚合接口 / 技能包）已并入设置柜的「接入集成」组,
 * 本页只承载运营性内容——日报配置/手动生成/运行历史（DailyBriefPanel）+ 只读模型状态 chip。
 * 页签 id 仍为 'mcp'（hash 书签兼容,与 PM2 app 名同理的纪元遗痕）。
 */
export default function DailyBriefTab({ showToast, collectorEnabled = false, isAdmin = false, onOpenModelConfig }) {
  const canManage = collectorEnabled && isAdmin;
  const [llmStatus, setLlmStatus] = useState(null);

  useEffect(() => {
    if (!canManage) return;
    getLLMConfig().then(setLlmStatus).catch(() => {});
  }, [canManage]);

  const llmOk = Boolean(llmStatus?.api_key_set);

  return (
    <div className="integ-page">
      <header className="page-head">
        <h1 className="page-title">AI 日报</h1>
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
      <DailyBriefPanel showToast={showToast} collectorEnabled={collectorEnabled} isAdmin={isAdmin} />
    </div>
  );
}
