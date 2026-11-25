import { useEffect, useState } from 'react';
import { Loader2, Plus } from 'lucide-react';
import { registerIngestSource } from '../../api';
import { registerPayload } from '../../utils/sourceAcquisition';
import { SyncDrawerShell } from './sync/syncParts';

const KINDS = [['social_post', '推文'], ['article', '文章'], ['podcast_episode', '播客']];
const SCOPES = {
  social_post: [['one', '一个账号'], ['open', '不限']],
  article: [['one', '一个站点'], ['open', '不限']],
};
const EMPTY = { kind: 'social_post', scope: 'open', name: '', identity: '', suffix: '', avatar: '', tokenId: '', listed: true };

function Segmented({ label, options, value, onChange }) {
  return (
    <div>
      <span className="form-label">{label}</span>
      <div className="segmented-control" role="group" aria-label={label}>
        {options.map(([key, text]) => (
          <button
            key={key}
            type="button"
            aria-pressed={key === value}
            className={`segmented-option ${key === value ? 'segmented-option-active' : ''}`}
            onClick={() => onChange(key)}
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * 登记来源(issue #344,源管理页头):建一个不带抓取器的源,内容由收录令牌推送进来。
 * 给了账号 / 站点 / feed 的只收那一处的内容;「不限」的是合集,源 id 固定以 push_ 开头。
 */
export default function RegisterSourceDrawer({ open, onClose, tokens = [], onRegistered, showToast }) {
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) setForm(EMPTY); }, [open]);

  const set = (patch) => setForm((prev) => ({ ...prev, ...patch }));
  const scopes = SCOPES[form.kind];
  const scope = scopes ? form.scope : 'one';
  const payload = registerPayload(form);
  const activeTokens = tokens.filter((token) => token.is_active);

  const submit = async () => {
    if (!payload || busy) return;
    setBusy(true);
    try {
      const res = await registerIngestSource(payload);
      showToast(`已登记 ${res.name}`, 'success');
      onRegistered?.(res);
      onClose();
    } catch (error) {
      showToast(error.message || '登记失败', 'error');
    } finally {
      setBusy(false);
    }
  };

  const identityField = form.kind === 'podcast_episode'
    ? ['feed', 'https://']
    : form.kind === 'social_post' ? ['账号', '@handle'] : ['站点或 feed', 'https://'];

  return (
    <SyncDrawerShell
      open={open}
      onClose={onClose}
      icon={<Plus />}
      title="登记来源"
      label="登记来源"
      foot={(
        <>
          <span className="flex-1" />
          <button
            type="button"
            className="action-button action-button-primary min-h-[32px] px-3 text-xs"
            disabled={!payload || busy}
            onClick={submit}
          >
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : '登记'}
          </button>
        </>
      )}
    >
      <div className="reg-form">
        <Segmented label="类型" options={KINDS} value={form.kind} onChange={(kind) => set({ kind, identity: '' })} />
        {scopes && <Segmented label="出处" options={scopes} value={form.scope} onChange={(next) => set({ scope: next })} />}
        <label>
          <span className="form-label">名称</span>
          <input
            className="form-input form-input-inline"
            value={form.name}
            maxLength={120}
            onChange={(e) => set({ name: e.target.value })}
            aria-label="名称"
          />
        </label>
        {scope === 'open' ? (
          <label>
            <span className="form-label">源 id</span>
            <span className="reg-id">
              <span>push_</span>
              <input
                className="form-input form-input-inline"
                value={form.suffix}
                maxLength={40}
                spellCheck={false}
                onChange={(e) => set({ suffix: e.target.value.toLowerCase() })}
                aria-label="源 id"
              />
            </span>
          </label>
        ) : (
          <label>
            <span className="form-label">{identityField[0]}</span>
            <input
              className="form-input form-input-inline"
              value={form.identity}
              placeholder={identityField[1]}
              spellCheck={false}
              onChange={(e) => set({ identity: e.target.value })}
              aria-label={identityField[0]}
            />
          </label>
        )}
        <label>
          <span className="form-label">头像</span>
          <input
            className="form-input form-input-inline"
            value={form.avatar}
            placeholder="https://"
            spellCheck={false}
            onChange={(e) => set({ avatar: e.target.value })}
            aria-label="头像"
          />
        </label>
        <label>
          <span className="form-label">令牌</span>
          <select
            className="form-input form-input-inline"
            value={form.tokenId}
            onChange={(e) => set({ tokenId: e.target.value })}
            aria-label="令牌"
          >
            <option value="">不绑定</option>
            {activeTokens.map((token) => (
              <option key={token.id} value={token.id}>{token.actor} · {token.token_preview}</option>
            ))}
          </select>
        </label>
        <div className="reg-switch">
          <span>进发现页</span>
          <button
            type="button"
            className="inspector-visibility-toggle"
            role="switch"
            aria-checked={form.listed}
            aria-label="进发现页"
            onClick={() => set({ listed: !form.listed })}
          >
            <span className={`ledger-switch ${form.listed ? 'is-on' : ''}`} aria-hidden="true" />
          </button>
        </div>
      </div>
    </SyncDrawerShell>
  );
}
