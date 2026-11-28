import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, Play } from 'lucide-react';
import { fetchGenerationFlows, getLLMConfig, runGenerationFlow, saveGenerationFlow } from '../api';
import { usePolling } from '../hooks/usePolling';
import {
  LAST_MARK_LABEL,
  countdown,
  lastMark,
  localDate,
  outputLine,
  phaseClass,
  progressNote,
} from '../utils/generationFlows';
import GenerationFlowDrawer from './GenerationFlowDrawer';

/**
 * 「生成流程」页(issue #72;原「AI 日报」页签):平台自己定时生成的东西,一行一个流程。
 * 列头是 取材 → 加工 → 产出 | 定时 | 生成;行上能直接动的只有开关与立即生成,
 * 点一行从右侧打开抽屉(GenerationFlowDrawer:配置、往期)。
 * 页签 id 仍是 'brief'(#/brief 与历史书签 #/mcp 照旧可用)。版面取自 docs/design/issue-72-product-registry/。
 */
export default function GenerationFlowsTab({ showToast, collectorEnabled = false, isAdmin = false, isActive = true, onOpenModelConfig, onViewSource }) {
  const canManage = collectorEnabled && isAdmin;
  const [flows, setFlows] = useState(null);
  const [llmStatus, setLlmStatus] = useState(null);
  const [openId, setOpenId] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [startedIds, setStartedIds] = useState(() => new Set());     // 本页发起、还没结束的生成
  const [periodsVersion, setPeriodsVersion] = useState(0);            // 生成 / 删除之后让抽屉里的往期重取
  const [now, setNow] = useState(() => Date.now());

  // 列表请求带序号:后发的先回来、或者中间存过一次配置时,先发的那个旧响应不许盖掉新的值
  const listRequest = useRef(0);
  const load = useCallback(() => {
    const token = ++listRequest.current;
    return fetchGenerationFlows()
      .then((data) => {
        if (token === listRequest.current) setFlows(data.flows || []);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!canManage) return;
    load();
    getLLMConfig().then(setLlmStatus).catch(() => {});
  }, [canManage, load]);

  // 有流程在跑:每 1.2 秒重取(阶段条走字,离开本页也继续);平时每分钟重取一次(下次运行的时刻、定时触发的结果)
  const anyRunning = startedIds.size > 0 || (flows || []).some((flow) => flow.running);
  usePolling(load, 1200, { immediate: false, pauseWhenHidden: false, enabled: canManage && anyRunning });
  usePolling(load, 60000, { immediate: false, enabled: canManage && !anyRunning });
  // 倒计时每秒走一格(只在本页签当前时)
  useEffect(() => {
    if (!isActive) return undefined;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [isActive]);

  const replaceFlow = (updated) => {
    listRequest.current += 1;       // 在途的列表请求早于这次写入:它回来时别盖掉刚存的值
    setFlows((prev) => (prev || []).map((flow) => (flow.id === updated.id ? updated : flow)));
  };

  const handleToggle = async (flow) => {
    const next = !flow.enabled;
    replaceFlow({ ...flow, enabled: next });
    try {
      replaceFlow(await saveGenerationFlow(flow.id, { enabled: next }));
      showToast(`${next ? '已开启' : '已关闭'} ${flow.name} 的定时生成`, 'success');
    } catch (error) {
      replaceFlow(flow);
      showToast(error.message || '设置失败', 'error');
    }
  };

  const handleGenerate = async (flow) => {
    setStartedIds((prev) => new Set(prev).add(flow.id));
    try {
      const result = await runGenerationFlow(flow.id);
      if (result.status === 'skipped' || result.status === 'empty') {
        showToast(`${flow.name}：${result.reason || '暂无新增内容'}`, 'info');
      } else {
        showToast(`已生成 ${flow.name}${result.report_date ? ` ${result.report_date}` : ''}`, 'success');
      }
    } catch (error) {
      showToast(error.message || '生成失败', 'error');
    } finally {
      setStartedIds((prev) => {
        const next = new Set(prev);
        next.delete(flow.id);
        return next;
      });
      setPeriodsVersion((version) => version + 1);
      load();
    }
  };

  const openDrawer = (flow) => {
    setOpenId(flow.id);
    setDrawerOpen(true);
  };

  const llmOk = Boolean(llmStatus?.api_key_set);
  const today = localDate(new Date(now));
  const isRunning = (flow) => flow.running || startedIds.has(flow.id);
  const openFlow = (flows || []).find((flow) => flow.id === openId) || null;

  return (
    <div className="runs-shell">
      <header className="page-head">
        <h1 className="page-title">生成流程</h1>
        {canManage && (
          <div className="page-head-actions">
            <button
              type="button"
              className="model-chip"
              title="前往设置编辑模型凭据"
              onClick={() => onOpenModelConfig?.()}
            >
              <i className={llmOk ? '' : 'is-off'} />模型 <b>{llmOk ? (llmStatus.model || '已配置') : '未配置'}</b>
            </button>
          </div>
        )}
      </header>
      {canManage && (
        <div className="runs-work">
          <div className="runs-paper gf-paper">
            <div className="gf-scroll">
              <div className="gf-band gf-grid">
                <span>流程</span>
                <span className="is-flow">取材</span>
                <span className="is-flow">加工</span>
                <span>产出</span>
                <span>定时</span>
                <span>生成</span>
              </div>
              {(flows || []).map((flow) => {
                const running = isRunning(flow);
                const shown = { ...flow, running };
                const mark = lastMark(shown);
                const [tone, line] = outputLine(shown, today);
                const note = progressNote(shown);
                const inputSub = flow.input?.warn || flow.input?.sub;
                const failedToday = flow.last_run?.status === 'failed' && (flow.last_run.ended_at || '').slice(0, 10) === today;
                return (
                  <div
                    key={flow.id}
                    className={`gf-row gf-grid${drawerOpen && openId === flow.id ? ' is-on' : ''}`}
                    role="button"
                    tabIndex={0}
                    aria-haspopup="dialog"
                    aria-label={`${flow.name}：配置与往期`}
                    onClick={() => openDrawer(flow)}
                    onKeyDown={(event) => {
                      if (event.target !== event.currentTarget) return;
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        openDrawer(flow);
                      }
                    }}
                  >
                    <div className="gf-cell">
                      <div className="gf-id">
                        <span className={`tt-last ${mark}`} title={`上次结果:${LAST_MARK_LABEL[mark]}`} />
                        <div className="run-obj-txt">
                          <div className="run-obj-name">{flow.name}</div>
                          <span className="run-obj-sub">{flow.source_id}</span>
                        </div>
                      </div>
                    </div>
                    <div className="gf-cell">
                      <div className="gf-main">{flow.input?.label}</div>
                      {inputSub && (
                        <div className="gf-sub">
                          <span className={`run-obj-sub${flow.input?.warn ? ' is-warn' : ''}`}>{inputSub}</span>
                        </div>
                      )}
                    </div>
                    <div className="gf-cell">
                      <div className="ledger-pipeline" aria-label="生成阶段">
                        {flow.phases.map((phase, index) => (
                          <span key={phase} className={`ledger-pipeline-step${phaseClass(shown, index)}`}>{phase}</span>
                        ))}
                      </div>
                      {running && note && <div className="pipeline-note">{note}</div>}
                    </div>
                    <div className="gf-cell">
                      <div className={`gf-main${tone ? ` is-${tone}` : ''}`}>{line}</div>
                      <div className="gf-sub">
                        <span className="tt-dots" title="近 7 日结果">
                          {flow.recent_days.map((day) => (
                            <i key={day.date} className={day.produced ? 'ok' : (failedToday && day.date === today ? 'bad' : '')} />
                          ))}
                        </span>
                        <span className="run-obj-sub">{flow.total} 期</span>
                      </div>
                    </div>
                    <div className="gf-cell">
                      <div className={`gf-when${flow.enabled ? '' : ' is-off'}`}>
                        <div>
                          <div className="gf-cron">{flow.cron}</div>
                          <div className="gf-sub">
                            <span className="run-obj-sub">
                              {flow.enabled ? `距下次 ${countdown(flow.next_run_at, now) || '—'}` : '未开启'}
                            </span>
                          </div>
                        </div>
                        <button
                          type="button"
                          role="switch"
                          aria-checked={flow.enabled}
                          aria-label={`定时生成 ${flow.name}`}
                          className={`ledger-switch ${flow.enabled ? 'is-on' : ''}`}
                          onClick={(event) => {
                            event.stopPropagation();
                            handleToggle(flow);
                          }}
                        />
                      </div>
                    </div>
                    <div className="gf-cell">
                      <button
                        type="button"
                        className="icon-button gf-icon"
                        title={running ? '生成中' : '立即生成'}
                        aria-label={`立即生成 ${flow.name}`}
                        disabled={running}
                        onClick={(event) => {
                          event.stopPropagation();
                          handleGenerate(flow);
                        }}
                      >
                        {running ? <Loader2 className="animate-spin" /> : <Play />}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
      {canManage && (
        <GenerationFlowDrawer
          open={drawerOpen && isActive}
          flow={openFlow}
          running={openFlow ? isRunning(openFlow) : false}
          systemSourceIds={(flows || []).map((flow) => flow.source_id)}
          periodsVersion={periodsVersion}
          showToast={showToast}
          onClose={() => setDrawerOpen(false)}
          onSaved={replaceFlow}
          onGenerate={handleGenerate}
          onPeriodsChanged={load}
          onViewSource={onViewSource}
        />
      )}
    </div>
  );
}
