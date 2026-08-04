import { useCallback, useEffect, useState } from 'react';
import { Bot, Check, ChevronRight, Loader2, X as XIcon, Zap } from 'lucide-react';
import {
  fetchCredentialsOverview,
  fetchRemoteSyncSchedule,
  getLLMConfig,
  getXApiConfig,
  saveLLMConfig,
  saveXApiConfig,
  testLLMConfig,
  testXApiConfig,
} from '../../api';
import SecretField from '../SecretField';

/**
 * 凭据区(v3.27 凭据整合台,设置柜 → 管理组):外部凭据的**单点编辑台**。
 * - LLM / X API 两张编辑卡:与后端 services/credentials 统一保管契约对应
 *   (来源徽记=field_sources、掩码占位=只写不回显、空 secret=保留);
 * - 「其它凭据」只读回指:定时同步凭据与 cron 同属一个工作流,编辑留在
 *   数据同步区(柜内一跳);GitHub Token 部署级 env-only,无面板编辑;
 * - 消费页(AI 日报页头 / 运维内容页 X 区头)的 chip 跳到本区;用量/配额等
 *   观测面留在运维管理——「设置=写入口,运维=观测面」(2026-07-31 拍板 B)。
 */

const SRC_LABELS = { runtime_kv: '运行时配置', env: '环境变量', ini: '配置文件', default: '默认值' };

function SrcBadge({ source }) {
  if (!source) return null;
  return (
    <span className={`src-badge ${source === 'env' ? 'is-env' : ''}`} title="有效值来源;环境变量/配置文件提供基线,此处保存的运行时值优先生效">
      {SRC_LABELS[source] || source}
    </span>
  );
}

function CredStamp({ ok }) {
  return <span className={`stamp ${ok ? 'stamp-ok' : 'stamp-idle'}`}>{ok ? '已配置' : '未配置'}</span>;
}

export default function CredentialsSection({ showToast, onNavigate }) {
  // ── 大模型 ──
  const [llmStatus, setLlmStatus] = useState(null);
  const [llmForm, setLlmForm] = useState({ base_url: '', model: '', api_key: '', temperature: 0.3, max_tokens: 4096 });
  const [savingLlm, setSavingLlm] = useState(false);
  const [testingLlm, setTestingLlm] = useState(false);

  // ── X API ──
  const [xStatus, setXStatus] = useState(null);
  const [xForm, setXForm] = useState({ bearer_token: '', base_url: '', max_results: 25, monthly_budget_usd: 5 });
  const [savingX, setSavingX] = useState(false);
  const [testingX, setTestingX] = useState(false);

  // ── 只读回指:定时同步凭据 + 部署级 env 机密 ──
  const [schedule, setSchedule] = useState(null);
  const [overview, setOverview] = useState(null);

  const loadLlm = useCallback(() => getLLMConfig().then((d) => {
    setLlmStatus(d);
    setLlmForm((f) => ({
      ...f,
      base_url: d.base_url || '',
      model: d.model || '',
      temperature: d.temperature ?? 0.3,
      max_tokens: d.max_tokens ?? 4096,
      api_key: '', // 后端永不回显明文,输入框始终空值起步 = 不改
    }));
  }).catch(() => {}), []);

  const loadX = useCallback(() => getXApiConfig().then((d) => {
    setXStatus(d);
    setXForm((f) => ({
      ...f,
      base_url: d.base_url || '',
      max_results: d.max_results ?? 25,
      monthly_budget_usd: d.monthly_budget_usd ?? 5,
      bearer_token: '',
    }));
  }).catch(() => {}), []);

  useEffect(() => {
    loadLlm();
    loadX();
    fetchRemoteSyncSchedule().then(setSchedule).catch(() => {});
    fetchCredentialsOverview().then(setOverview).catch(() => {});
  }, [loadLlm, loadX]);

  // ── LLM 保存/测试(空 api_key 不入 payload = 保留既有机密) ──
  const updateLlm = (key, value) => setLlmForm((f) => ({ ...f, [key]: value }));
  const canTestLlm = Boolean(llmForm.base_url.trim() && llmForm.model.trim() && (llmForm.api_key.trim() || llmStatus?.api_key_set));

  const persistLlm = async () => {
    const payload = {
      base_url: llmForm.base_url.trim(),
      model: llmForm.model.trim(),
      temperature: Number(llmForm.temperature),
      max_tokens: Number(llmForm.max_tokens),
    };
    if (llmForm.api_key.trim()) payload.api_key = llmForm.api_key.trim();
    await saveLLMConfig(payload);
  };

  const handleSaveLlm = async () => {
    setSavingLlm(true);
    try {
      await persistLlm();
      showToast('已保存模型配置', 'success');
      await loadLlm();
    } catch (error) {
      showToast(error.message || '保存失败', 'error');
    } finally {
      setSavingLlm(false);
    }
  };

  const handleTestLlm = async () => {
    setTestingLlm(true);
    try {
      await persistLlm();
      const r = await testLLMConfig();
      await loadLlm();
      showToast(`已连接 · ${r.model} · ${r.latency_ms}ms`, 'success');
    } catch (error) {
      showToast(error.message || '连接失败', 'error');
    } finally {
      setTestingLlm(false);
    }
  };

  // ── X 保存/测试 ──
  const updateX = (key, value) => setXForm((f) => ({ ...f, [key]: value }));
  const xTokenReady = Boolean(xForm.bearer_token.trim() || xStatus?.bearer_token_set);

  const persistX = async () => {
    const payload = {
      base_url: xForm.base_url.trim(),
      max_results: Number(xForm.max_results),
      monthly_budget_usd: Number(xForm.monthly_budget_usd),
    };
    if (xForm.bearer_token.trim()) payload.bearer_token = xForm.bearer_token.trim();
    await saveXApiConfig(payload);
  };

  const handleSaveX = async () => {
    setSavingX(true);
    try {
      await persistX();
      showToast('已保存 X API 配置', 'success');
      await loadX();
    } catch (error) {
      showToast(error.message || '保存失败', 'error');
    } finally {
      setSavingX(false);
    }
  };

  const handleTestX = async () => {
    setTestingX(true);
    try {
      await persistX();
      const r = await testXApiConfig();
      await loadX();
      // 探针花费如实转述:命中当日去重为 $0,否则约 $0.005~0.010
      const cost = r.deduplicated_today
        ? '本次未产生费用'
        : `本次探针 $${Number(r.estimated_cost_usd || 0).toFixed(3)}`;
      showToast(`连接正常 · ${cost}`, 'success');
    } catch (error) {
      showToast(error.message || '连接失败', 'error');
    } finally {
      setTestingX(false);
    }
  };

  const scheduleConfigured = Boolean(schedule?.password_set);

  return (
    <div>
      {/* ── 大模型 ── */}
      <section className="cred-card">
        <div className="cred-head">
          <Bot />
          <span className="cred-title">大模型</span>
          <div className="cred-head-right">
            <SrcBadge source={llmStatus?.source} />
            <CredStamp ok={Boolean(llmStatus?.configured)} />
          </div>
        </div>
        <p className="cred-sub">日报生成、读者翻译/问答共用的 OpenAI 兼容接口</p>
        <div className="cred-fields">
          <label className="sett-field cred-field-wide">
            <span className="sett-field-lbl">base_url</span>
            <input className="form-input font-mono" value={llmForm.base_url} onChange={(e) => updateLlm('base_url', e.target.value)} placeholder="https://api.deepseek.com/v1" />
          </label>
          <label className="sett-field">
            <span className="sett-field-lbl">model</span>
            <input className="form-input font-mono" value={llmForm.model} onChange={(e) => updateLlm('model', e.target.value)} placeholder="deepseek-chat" />
          </label>
          <label className="sett-field">
            <span className="sett-field-lbl">api_key</span>
            <SecretField
              className="form-input font-mono"
              value={llmForm.api_key}
              onChange={(v) => updateLlm('api_key', v)}
              isSet={Boolean(llmStatus?.api_key_set)}
              preview={llmStatus?.api_key_preview}
              hint="sk-..."
            />
          </label>
          <label className="sett-field">
            <span className="sett-field-lbl">temperature</span>
            <input className="form-input font-mono" type="number" step="0.1" min="0" max="2" value={llmForm.temperature} onChange={(e) => updateLlm('temperature', e.target.value)} />
          </label>
          <label className="sett-field">
            <span className="sett-field-lbl">max_tokens</span>
            <input className="form-input font-mono" type="number" step="256" min="256" value={llmForm.max_tokens} onChange={(e) => updateLlm('max_tokens', e.target.value)} />
          </label>
        </div>
        <div className="sett-sync-foot">
          <span className="cred-ref-meta">用量与总闸见 运维管理 → AI</span>
          <div className="ml-auto flex items-center gap-2">
            <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" onClick={handleTestLlm} disabled={testingLlm || savingLlm || !canTestLlm}>
              {testingLlm ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5" />} 测试连通
            </button>
            <button type="button" className="action-button action-button-primary min-h-[32px] px-3 text-xs" onClick={handleSaveLlm} disabled={savingLlm}>
              {savingLlm ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />} 保存
            </button>
          </div>
        </div>
      </section>

      {/* ── X API ── */}
      <section className="cred-card">
        <div className="cred-head">
          <XIcon />
          <span className="cred-title">X API</span>
          <div className="cred-head-right">
            <SrcBadge source={xStatus?.source} />
            <CredStamp ok={Boolean(xStatus?.configured)} />
          </div>
        </div>
        <p className="cred-sub">
          社交源采集的应用级 Bearer Token。计费按<b>实际返回的资源</b>而非请求次数——「单次上限」调大不增开销,只降低积压漏抓风险;达到月度预算后发出请求前即停抓。
        </p>
        <div className="cred-fields">
          <label className="sett-field cred-field-wide">
            <span className="sett-field-lbl">
              bearer_token
              {xStatus?.field_sources?.bearer_token === 'env' && <span className="src-badge is-env" style={{ marginLeft: 6 }}>env</span>}
            </span>
            <SecretField
              className="form-input font-mono"
              value={xForm.bearer_token}
              onChange={(v) => updateX('bearer_token', v)}
              isSet={Boolean(xStatus?.bearer_token_set)}
              preview={xStatus?.bearer_token_preview}
              hint="AAAA…"
            />
          </label>
          <label className="sett-field">
            <span className="sett-field-lbl">base_url</span>
            <input className="form-input font-mono" value={xForm.base_url} onChange={(e) => updateX('base_url', e.target.value)} placeholder="https://api.x.com/2" />
          </label>
          <label className="sett-field">
            <span className="sett-field-lbl">单次上限</span>
            <input className="form-input font-mono" type="number" step="5" min="5" max="100" value={xForm.max_results} onChange={(e) => updateX('max_results', e.target.value)} />
          </label>
          <label className="sett-field">
            <span className="sett-field-lbl">月度预算 $</span>
            <input className="form-input font-mono" type="number" step="0.5" min="0" value={xForm.monthly_budget_usd} onChange={(e) => updateX('monthly_budget_usd', e.target.value)} />
          </label>
        </div>
        <div className="sett-sync-foot">
          <span className="cred-ref-meta">配额与本月开销见 运维管理 → 内容 · 测试约 $0.005,当日已见资源为 $0</span>
          <div className="ml-auto flex items-center gap-2">
            <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" onClick={handleTestX} disabled={testingX || savingX || !xTokenReady}>
              {testingX ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5" />} 测试连通
            </button>
            <button type="button" className="action-button action-button-primary min-h-[32px] px-3 text-xs" onClick={handleSaveX} disabled={savingX}>
              {savingX ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />} 保存
            </button>
          </div>
        </div>
      </section>

      {/* ── 其它凭据(只读回指:编辑留在所属工作流原处,此处保证一屏点清所有对外凭据) ── */}
      <div className="cred-sec">
        <div className="cred-sec-title">其它凭据</div>
        <div className="cred-refs">
          <div className="cred-ref-row">
            <CredStamp ok={scheduleConfigured} />
            <span className="cred-ref-name">定时同步 · 远端凭据</span>
            <span className="cred-ref-meta">
              {scheduleConfigured
                ? <>远端 <b>{schedule?.base_url || '—'}</b> · 账号 <b>{schedule?.username || '—'}</b></>
                : '尚未保存远端凭据'}
            </span>
            <span className="cred-ref-act">
              <button type="button" className="sett-link inline-flex items-center gap-0.5" onClick={() => onNavigate?.('sync')}>
                前往 数据同步 <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </span>
          </div>
          <div className="cred-ref-row">
            <CredStamp ok={Boolean(overview?.github_token_set)} />
            <span className="cred-ref-name">GitHub Token</span>
            <span className="cred-ref-meta">仓库抓取限速放宽 · 仅环境变量 <b>GITHUB_TOKEN</b>,无面板编辑</span>
          </div>
        </div>
      </div>
    </div>
  );
}
