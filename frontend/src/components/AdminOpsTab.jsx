import { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  Users,
  Database,
  Bookmark,
  Sparkles,
  Zap,
  KeyRound,
  Trash2,
  UserPlus,
  Loader2,
  Power,
  ServerCog,
  Brain,
  Check,
  X,
  BarChart3,
} from 'lucide-react';
import {
  fetchAdminOverview,
  fetchAdminAccounts,
  getAiBetaGlobal,
  setAiBetaGlobal,
  fetchAiUsage,
  getLLMConfig,
  saveLLMConfig,
  testLLMConfig,
  createAccount,
  updateAccount,
  resetAccountPassword,
  deleteAccount,
} from '../api';
import { useConfirm } from '../hooks/useConfirm';
import { useModalTransition } from '../hooks/useModalTransition';

const INPUT_CLS = 'w-full rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-[var(--dorami-soft)] px-4 py-2.5 text-sm';

// 用途标签：与后端 AiUsageRecord.purpose 对齐。
const PURPOSE_LABELS = {
  translate: '阅读器翻译',
  ask: '阅读器问答',
  daily_brief_map: '日报·概括',
  daily_brief_dedup: '日报·去重',
  daily_brief_reduce: '日报·汇编',
  source_config: '节点·配置',
  detail_profile: '节点·详情',
};

// 时间戳格式化：把 ISO 时间显示为「MM-DD HH:mm」，无值回落到占位符。
function formatStamp(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// 大数字缩写：1234 → 1.2k，1234567 → 1.2M。
function fmtNum(n) {
  const v = Number(n || 0);
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}k`;
  return String(v);
}

function StatCard({ icon: Icon, label, value, sub }) {
  return (
    <div className="rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-white dark:bg-[var(--dorami-surface)] p-4">
      <div className="flex items-center gap-2 text-slate-500">
        <Icon className="h-4 w-4" />
        <span className="micro-label">{label}</span>
      </div>
      <p className="stat-number mt-2 text-slate-800">{value}</p>
      {sub && <p className="tiny-meta mt-0.5">{sub}</p>}
    </div>
  );
}

// 用量明细：每行「标签 + 调用数/tokens + 占比条」（按 total_tokens 相对最大值）。
function UsageBars({ rows, labelKey, labelMap, total }) {
  if (!rows || rows.length === 0) return <p className="tiny-meta">暂无</p>;
  const max = Math.max(...rows.map((r) => r.total_tokens), 1);
  void total;
  return (
    <div className="space-y-2.5">
      {rows.map((r) => {
        const label = labelMap ? (labelMap[r[labelKey]] || r[labelKey]) : r[labelKey];
        const pct = Math.round((r.total_tokens / max) * 100);
        return (
          <div key={r[labelKey]}>
            <div className="flex items-center justify-between gap-2 tiny-meta">
              <span className="truncate font-bold text-slate-700">{label}</span>
              <span className="shrink-0 text-slate-500">{r.calls} 次 · {fmtNum(r.total_tokens)} tok</span>
            </div>
            <div className="mt-1 h-1.5 w-full rounded-full bg-[var(--dorami-well)]">
              <div className="h-full rounded-full bg-[var(--dorami-blue-2)]" style={{ width: `${Math.max(pct, 2)}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function AdminOpsTab({ showToast }) {
  const confirm = useConfirm();
  const [overview, setOverview] = useState(null);
  const [accounts, setAccounts] = useState(null);
  const [globalAi, setGlobalAi] = useState(null);
  const [busy, setBusy] = useState(false);
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newRole, setNewRole] = useState('user');

  // ── 模型配置（日报 + 阅读器 AI 共用的全局唯一配置）──
  const [llmStatus, setLlmStatus] = useState(null);
  const [llmForm, setLlmForm] = useState({ base_url: '', model: '', api_key: '', temperature: 0.3, max_tokens: 4096 });
  const [savingLlm, setSavingLlm] = useState(false);
  const [testingLlm, setTestingLlm] = useState(false);
  const [llmModalOpen, setLlmModalOpen] = useState(false);
  const llmModal = useModalTransition(llmModalOpen);

  // ── AI 用量看板 ──
  const [usage, setUsage] = useState(null);
  const [usageDays, setUsageDays] = useState(7);

  const loadLlm = useCallback(() => getLLMConfig().then((d) => {
    setLlmStatus(d);
    setLlmForm((f) => ({ ...f, base_url: d.base_url || '', model: d.model || '', temperature: d.temperature ?? 0.3, max_tokens: d.max_tokens ?? 4096, api_key: '' }));
  }).catch(() => {}), []);

  const loadUsage = useCallback((days) => fetchAiUsage(days).then(setUsage).catch(() => {}), []);

  const reload = useCallback(async () => {
    try {
      const [ov, accs, g] = await Promise.all([
        fetchAdminOverview(),
        fetchAdminAccounts(),
        getAiBetaGlobal(),
      ]);
      setOverview(ov);
      setAccounts(accs);
      setGlobalAi(g.enabled);
    } catch (error) {
      showToast(error.message || '加载运维数据失败', 'error');
      setAccounts((prev) => prev ?? []);
    }
  }, [showToast]);

  useEffect(() => { reload(); loadLlm(); }, [reload, loadLlm]);
  useEffect(() => { loadUsage(usageDays); }, [loadUsage, usageDays]);

  // 配置弹窗打开时锁定页面滚动。
  useEffect(() => {
    if (!llmModalOpen) return undefined;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, [llmModalOpen]);

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
      setLlmModalOpen(false);
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

  const reloadAccounts = useCallback(async () => {
    const [ov, accs] = await Promise.all([fetchAdminOverview(), fetchAdminAccounts()]);
    setOverview(ov);
    setAccounts(accs);
  }, []);

  const handleToggleGlobalAi = async () => {
    const next = !globalAi;
    try {
      const res = await setAiBetaGlobal(next);
      setGlobalAi(res.enabled);
      showToast(res.enabled ? '已合上 AI 总闸，恢复放行' : '已断开 AI 总闸，全员熔断', 'success');
      await reloadAccounts();
    } catch (error) {
      showToast(error.message || '更新 AI 全局开关失败', 'error');
    }
  };

  const handleCreate = async (event) => {
    event.preventDefault();
    if (!newUsername.trim() || !newPassword) {
      showToast('请填写用户名与密码', 'error');
      return;
    }
    if (newPassword.length < 6) {
      showToast('密码至少 6 位', 'error');
      return;
    }
    setBusy(true);
    try {
      await createAccount({ username: newUsername.trim(), password: newPassword, role: newRole });
      showToast(`已创建账户 ${newUsername.trim()}`, 'success');
      setNewUsername('');
      setNewPassword('');
      setNewRole('user');
      await reloadAccounts();
    } catch (error) {
      showToast(error.message || '创建账户失败', 'error');
    } finally {
      setBusy(false);
    }
  };

  const handleToggleRole = async (acc) => {
    const nextRole = acc.role === 'admin' ? 'user' : 'admin';
    try {
      await updateAccount(acc.username, { role: nextRole });
      showToast(`已将 ${acc.username} 设为${nextRole === 'admin' ? '管理员' : '读者'}`, 'success');
      await reloadAccounts();
    } catch (error) {
      showToast(error.message || '更新失败', 'error');
    }
  };

  const handleToggleActive = async (acc) => {
    try {
      await updateAccount(acc.username, { is_active: !acc.is_active });
      showToast(acc.is_active ? `已停用 ${acc.username}` : `已启用 ${acc.username}`, 'success');
      await reloadAccounts();
    } catch (error) {
      showToast(error.message || '更新失败', 'error');
    }
  };

  const handleToggleAiBeta = async (acc) => {
    try {
      await updateAccount(acc.username, { ai_beta_enabled: !acc.ai_beta_enabled });
      showToast(acc.ai_beta_enabled ? `已为 ${acc.username} 关闭 AI` : `已为 ${acc.username} 开启 AI`, 'success');
      await reloadAccounts();
    } catch (error) {
      showToast(error.message || '更新失败', 'error');
    }
  };

  const handleResetPassword = async (acc) => {
    const pwd = window.prompt(`为账户「${acc.username}」设置新密码（至少 6 位）：`);
    if (pwd === null) return;
    if (pwd.length < 6) {
      showToast('密码至少 6 位', 'error');
      return;
    }
    try {
      await resetAccountPassword(acc.username, pwd);
      showToast(`已重置 ${acc.username} 的密码`, 'success');
    } catch (error) {
      showToast(error.message || '重置密码失败', 'error');
    }
  };

  const handleDelete = async (acc) => {
    if (!(await confirm(`确认删除账户「${acc.username}」？其订阅与个人接口令牌会一并清除，且不可恢复。`))) return;
    try {
      await deleteAccount(acc.username);
      showToast(`已删除 ${acc.username}`, 'success');
      await reloadAccounts();
    } catch (error) {
      showToast(error.message || '删除账户失败', 'error');
    }
  };

  const acc = overview?.accounts;
  const archive = overview?.archive;
  const ai = overview?.ai;

  const recentLogins = useMemo(() => overview?.recent_logins ?? [], [overview]);

  return (
    <div>
      <div className="page-header">
        <div className="page-heading">
          <h1 className="page-title">运维管理</h1>
          <p className="page-subtitle">查看账户与用量概况、管理登录账号、控制 AI Beta 全局开关。</p>
        </div>
      </div>

      <div className="mt-6 space-y-6">
        {/* ── 概览统计 ── */}
        <div className="surface-card rounded-[var(--r-card)] overflow-hidden">
          <div className="flex items-center gap-3 px-6 py-4 border-b border-[var(--dorami-border)]">
            <div className="w-1 h-5 rounded-full bg-indigo-500" />
            <h3 className="section-title">概览统计</h3>
          </div>
          <div className="p-6">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <StatCard
                icon={Users}
                label="账户总数"
                value={acc ? acc.total : '—'}
                sub={acc ? `管理员 ${acc.admin} · 读者 ${acc.reader}` : null}
              />
              <StatCard
                icon={Power}
                label="启用 / 停用"
                value={acc ? `${acc.active} / ${acc.disabled}` : '—'}
                sub={acc ? `AI Beta 开启 ${acc.ai_beta_enabled} 人` : null}
              />
              <StatCard
                icon={Database}
                label="归档文章"
                value={archive ? archive.articles.toLocaleString() : '—'}
                sub={archive ? `订阅关系 ${archive.subscriptions} · 令牌 ${archive.feed_tokens}` : null}
              />
              <StatCard
                icon={Sparkles}
                label="AI 调用累计"
                value={ai ? ai.calls_total.toLocaleString() : '—'}
                sub={ai ? `翻译 ${ai.translate_total} · 问答 ${ai.ask_total}` : null}
              />
            </div>

            {recentLogins.length > 0 && (
              <div className="mt-5">
                <p className="micro-label text-slate-500">最近登录</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {recentLogins.map((u) => (
                    <span
                      key={u.username}
                      className="inline-flex items-center gap-1.5 rounded-[var(--r-pill)] border border-[var(--dorami-border)] bg-white dark:bg-[var(--dorami-surface)] px-2.5 py-1 tiny-meta"
                    >
                      <span className="font-bold text-slate-700">{u.username}</span>
                      <span className="text-slate-500">{formatStamp(u.last_login_at)}</span>
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* ── AI 总闸 + 模型配置 ── */}
        <div className="surface-card rounded-[var(--r-card)] overflow-hidden">
          <div className="flex items-center gap-3 px-6 py-4 border-b border-[var(--dorami-border)]">
            <div className="w-1 h-5 rounded-full bg-violet-500" />
            <h3 className="section-title">AI 总闸与模型</h3>
            <span className="tiny-meta">日报与阅读器 AI 共用一套模型</span>
          </div>
          <div className="p-6 divide-y divide-[var(--dorami-border)]">
            {/* 总闸行 */}
            <div className="flex items-center justify-between gap-4 pb-5">
              <div className="min-w-0">
                <p className="flex items-center gap-2 text-sm font-black text-slate-800">
                  <Zap className="h-4 w-4 text-indigo-500" /> 阅读器 AI 总闸
                  <span className={`micro-label rounded px-1.5 py-0.5 ${globalAi ? 'bg-emerald-50 text-emerald-600' : 'bg-amber-50 text-amber-600'}`}>
                    {globalAi ? '放行中' : '已熔断'}
                  </span>
                </p>
                <p className="tiny-meta mt-1">
                  合上＝放行全员、断开＝全员熔断；谁能用仍看下方各账户的 AI 开关
                  {globalAi && acc ? ` · 已授权 ${acc.ai_beta_enabled} 个账户` : ''}
                </p>
              </div>
              <button
                onClick={handleToggleGlobalAi}
                disabled={globalAi === null}
                aria-pressed={!!globalAi}
                className={`action-button shrink-0 ${globalAi ? 'action-button-secondary' : 'action-button-primary'}`}
              >
                <Power className="h-4 w-4" /> {globalAi ? '断开总闸' : '合上总闸'}
              </button>
            </div>
            {/* 模型概览行 */}
            <div className="flex items-center justify-between gap-4 pt-5">
              <div className="min-w-0">
                <p className="flex items-center gap-2 text-sm font-bold text-slate-700">
                  <Brain className="h-4 w-4 text-slate-500" /> 大模型
                  <span className={`h-1.5 w-1.5 rounded-full ${llmStatus?.api_key_set ? 'bg-emerald-500' : 'bg-amber-500'}`} />
                </p>
                <p className="tiny-meta mt-1 truncate font-mono">
                  {llmStatus?.api_key_set
                    ? `${llmStatus.base_url || '—'} · ${llmStatus.model || '—'}`
                    : '尚未配置 · 配置后日报与阅读器 AI 方可使用'}
                </p>
              </div>
              <button onClick={() => setLlmModalOpen(true)} className="action-button action-button-secondary shrink-0">
                <ServerCog className="h-4 w-4" /> 配置模型
              </button>
            </div>
          </div>
        </div>

        {/* ── AI 用量看板 ── */}
        <div className="surface-card rounded-[var(--r-card)] overflow-hidden">
          <div className="flex items-center gap-3 px-6 py-4 border-b border-[var(--dorami-border)]">
            <div className="w-1 h-5 rounded-full bg-emerald-500" />
            <h3 className="section-title">AI 用量</h3>
            <div className="ml-auto inline-flex rounded-[var(--r-control)] border border-[var(--dorami-border)] p-0.5">
              {[7, 30].map((d) => (
                <button
                  key={d}
                  onClick={() => setUsageDays(d)}
                  className={`rounded-[var(--r-sm)] px-2.5 py-1 micro-label transition-colors ${
                    usageDays === d ? 'bg-[var(--dorami-wash)] text-indigo-600' : 'text-slate-500 hover:text-slate-700'
                  }`}
                >
                  近 {d} 天
                </button>
              ))}
            </div>
          </div>
          <div className="p-6">
            {!usage || usage.totals.calls === 0 ? (
              <p className="rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] p-4 text-center tiny-meta">
                <BarChart3 className="mx-auto mb-1 h-4 w-4 text-slate-500" />
                近 {usageDays} 天还没有 AI 调用记录，触发一次翻译/问答或日报生成后这里会出现统计。
              </p>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                  <StatCard icon={Sparkles} label="总调用" value={fmtNum(usage.totals.calls)} />
                  <StatCard icon={BarChart3} label="总 tokens" value={fmtNum(usage.totals.total_tokens)} />
                  <StatCard icon={BarChart3} label="prompt tokens" value={fmtNum(usage.totals.prompt_tokens)} />
                  <StatCard icon={BarChart3} label="completion tokens" value={fmtNum(usage.totals.completion_tokens)} />
                </div>

                <div className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-2">
                  <div>
                    <p className="micro-label mb-2 text-slate-500">按用途</p>
                    <UsageBars rows={usage.by_purpose} labelKey="purpose" labelMap={PURPOSE_LABELS} total={usage.totals.total_tokens} />
                  </div>
                  <div>
                    <p className="micro-label mb-2 text-slate-500">按用户（Top）</p>
                    <UsageBars rows={usage.by_user.slice(0, 8)} labelKey="username" total={usage.totals.total_tokens} />
                  </div>
                </div>
              </>
            )}
          </div>
        </div>

        {/* ── 账户管理 ── */}
        <div className="surface-card rounded-[var(--r-card)] overflow-hidden">
          <div className="flex items-center gap-3 px-6 py-4 border-b border-[var(--dorami-border)]">
            <div className="w-1 h-5 rounded-full bg-sky-500" />
            <h3 className="section-title">账户管理</h3>
            <span className="tiny-meta">停用 / 删除会立即让对应账户的会话失效</span>
          </div>
          <div className="p-6 space-y-4">
            <form onSubmit={handleCreate} className="rounded-[var(--r-card)] border border-[var(--dorami-border)] bg-white dark:bg-[var(--dorami-surface)] p-4">
              <p className="flex items-center gap-1.5 text-sm font-bold text-slate-700">
                <ServerCog className="h-4 w-4 text-slate-500" /> 新建账户
              </p>
              <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                <input
                  value={newUsername}
                  onChange={(e) => setNewUsername(e.target.value)}
                  placeholder="用户名"
                  autoComplete="off"
                  className="form-input w-full"
                />
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  placeholder="初始密码（至少 6 位）"
                  autoComplete="new-password"
                  className="form-input w-full"
                />
                <label className="flex items-center gap-2 text-sm font-bold text-slate-500">
                  角色
                  <select value={newRole} onChange={(e) => setNewRole(e.target.value)} className="form-input flex-1">
                    <option value="user">读者</option>
                    <option value="admin">管理员</option>
                  </select>
                </label>
                <button type="submit" disabled={busy} className="action-button action-button-primary">
                  {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <UserPlus className="h-4 w-4" />} 创建账户
                </button>
              </div>
            </form>

            <div>
              <p className="mb-3 text-sm font-bold text-slate-700">现有账户</p>
              {accounts === null ? (
                <p className="tiny-meta">加载中…</p>
              ) : accounts.length === 0 ? (
                <p className="rounded-[var(--r-card)] border border-dashed border-[var(--dorami-border)] p-4 text-center tiny-meta">
                  还没有账户，用上方「新建账户」创建第一个。
                </p>
              ) : (
                <div className="space-y-2">
                  {accounts.map((account) => (
                    <div
                      key={account.username}
                      className="flex flex-wrap items-center justify-between gap-3 rounded-[var(--r-control)] border border-[var(--dorami-border)] bg-white dark:bg-[var(--dorami-surface)] px-3 py-2.5"
                    >
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="truncate text-sm font-bold text-slate-800">{account.username}</span>
                          <span className={`rounded px-1.5 py-0.5 micro-label ${account.role === 'admin' ? 'bg-amber-50 text-amber-600' : 'bg-slate-100 text-slate-500'}`}>
                            {account.role === 'admin' ? '管理员' : '读者'}
                          </span>
                          {!account.is_active && <span className="rounded bg-rose-50 px-1.5 py-0.5 micro-label text-rose-500">已停用</span>}
                          {account.ai_beta_enabled && (
                            <span className="inline-flex items-center gap-0.5 rounded bg-indigo-50 px-1.5 py-0.5 micro-label text-indigo-500">
                              <Zap className="h-3 w-3" /> AI Beta
                            </span>
                          )}
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 tiny-meta text-slate-500">
                          <span className="inline-flex items-center gap-1"><KeyRound className="h-3 w-3" /> 登录 {formatStamp(account.last_login_at)}</span>
                          <span className="inline-flex items-center gap-1"><Sparkles className="h-3 w-3" /> AI {(account.ai_translate_count || 0) + (account.ai_ask_count || 0)} 次</span>
                          <span className="inline-flex items-center gap-1"><Bookmark className="h-3 w-3" /> 订阅 {account.subscription_count ?? 0}</span>
                        </div>
                      </div>
                      <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                        <button onClick={() => handleToggleRole(account)} className="action-button action-button-secondary text-xs">
                          设为{account.role === 'admin' ? '读者' : '管理员'}
                        </button>
                        <button onClick={() => handleToggleActive(account)} className="action-button action-button-secondary text-xs">
                          {account.is_active ? '停用' : '启用'}
                        </button>
                        <button onClick={() => handleToggleAiBeta(account)} className="action-button action-button-secondary text-xs">
                          <Zap className="h-3.5 w-3.5" /> {account.ai_beta_enabled ? '关闭 AI' : '开启 AI'}
                        </button>
                        <button onClick={() => handleResetPassword(account)} className="action-button action-button-secondary text-xs">
                          <KeyRound className="h-3.5 w-3.5" /> 重置密码
                        </button>
                        <button onClick={() => handleDelete(account)} className="action-button action-button-danger text-xs">
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ── 模型配置弹窗（Portal 到 body，避开变换祖先造成的 fixed 错位） ── */}
      {llmModal.mounted && createPortal(
        <div className={`modal-overlay ${llmModal.closing ? 'is-closing' : ''}`} onClick={() => setLlmModalOpen(false)}>
          <div className="modal-panel max-w-xl" onClick={(e) => e.stopPropagation()}>
            <div className="px-6 py-4 border-b border-[var(--dorami-border)] flex items-center justify-between bg-[var(--dorami-well)]">
              <h3 className="card-title flex items-center gap-2">
                <Brain className="w-5 h-5 text-indigo-500" /> 模型配置
              </h3>
              <button onClick={() => setLlmModalOpen(false)} className="text-slate-500 hover:text-slate-700"><X className="w-5 h-5" /></button>
            </div>
            <div className="p-6">
              <p className="tiny-meta mb-4">日报与阅读器 AI 共用 · OpenAI 兼容协议（/chat/completions）· API Key 不回显</p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <p className="form-label">Base URL</p>
                  <input value={llmForm.base_url} onChange={(e) => updateLlm('base_url', e.target.value)} placeholder="https://api.deepseek.com/v1" className={`${INPUT_CLS} font-mono`} />
                </div>
                <div>
                  <p className="form-label">模型</p>
                  <input value={llmForm.model} onChange={(e) => updateLlm('model', e.target.value)} placeholder="deepseek-chat" className={`${INPUT_CLS} font-mono`} />
                </div>
              </div>
              <div className="mt-3">
                <p className="form-label">
                  API Key
                  {llmStatus?.api_key_set ? <span className="ml-1 text-emerald-500 normal-case">已配置（{llmStatus.api_key_preview}）</span> : <span className="ml-1 text-amber-500 normal-case">未配置</span>}
                </p>
                <input type="password" value={llmForm.api_key} onChange={(e) => updateLlm('api_key', e.target.value)} placeholder={llmStatus?.api_key_set ? '留空表示不修改' : 'sk-...'} className={`${INPUT_CLS} font-mono`} />
              </div>
              <div className="mt-3 grid grid-cols-2 gap-3">
                <div>
                  <p className="form-label">Temperature</p>
                  <input type="number" step="0.1" min="0" max="2" value={llmForm.temperature} onChange={(e) => updateLlm('temperature', e.target.value)} className={INPUT_CLS} />
                </div>
                <div>
                  <p className="form-label">Max Tokens</p>
                  <input type="number" step="256" min="256" value={llmForm.max_tokens} onChange={(e) => updateLlm('max_tokens', e.target.value)} className={INPUT_CLS} />
                </div>
              </div>
            </div>
            <div className="px-6 py-4 bg-[var(--dorami-soft)] border-t border-[var(--dorami-border)] flex items-center gap-3">
              <button onClick={handleSaveLlm} disabled={savingLlm} className="action-button action-button-primary text-xs">
                {savingLlm ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />} 保存
              </button>
              <button onClick={handleTestLlm} disabled={testingLlm || savingLlm || !canTestLlm} className="action-button action-button-secondary text-xs">
                {testingLlm ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Zap className="h-3.5 w-3.5 text-amber-500" />} 测试连接
              </button>
              <span className="tiny-meta">{canTestLlm ? '测试会自动保存当前配置' : '填写 Base URL / 模型 / API Key 后可测试'}</span>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
