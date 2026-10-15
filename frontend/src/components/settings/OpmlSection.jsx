import { useEffect, useRef, useState } from 'react';
import { ChevronLeft, Download, FileText, Loader2 } from 'lucide-react';
import {
  confirmOpmlImport,
  downloadSubscriptionOpml,
  fetchOpmlSummary,
  previewOpmlImport,
} from '../../api';

const OPML_MAX_CHARS = 1_000_000;

const CHIP_META = [
  { key: 'new', label: '新建自定源', stamp: 'stamp-ok' },
  { key: 'matched_existing', label: '命中已收录 · 直接订阅', stamp: 'stamp-run' },
  { key: 'already_subscribed', label: '已订阅', stamp: 'stamp-idle' },
  { key: 'invalid', label: '无效', stamp: 'stamp-warn' },
  { key: 'rejected_ssrf', label: '已拒绝', stamp: 'stamp-bad' },
];

const ROW_STAMP = {
  new: { label: '新建', stamp: 'stamp-ok' },
  matched_existing: { label: '直接订阅', stamp: 'stamp-run' },
  already_subscribed: { label: '已订阅', stamp: 'stamp-idle' },
  invalid: { label: '无效', stamp: 'stamp-warn' },
  rejected_ssrf: { label: '已拒绝', stamp: 'stamp-bad' },
};

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function displayUrl(url) {
  return String(url || '').replace(/^https?:\/\//i, '');
}

function itemDetail(item) {
  if ((item.status === 'invalid' || item.status === 'rejected_ssrf') && item.reason) {
    return { kind: item.status === 'rejected_ssrf' ? 'bad' : 'warn', text: item.reason };
  }
  const url = displayUrl(item.canonical_url || item.xml_url);
  if (item.status === 'matched_existing' && item.matched_name) {
    return { kind: 'url', text: `${url} → 已收录「${item.matched_name}」` };
  }
  return { kind: 'url', text: url || item.reason || '' };
}

function confirmLabel(pickedNew, pickedMatch) {
  if (!pickedNew && !pickedMatch) return '没有可导入的条目';
  const parts = [];
  if (pickedNew) parts.push(`新建 ${pickedNew} 个`);
  if (pickedMatch) parts.push(`订阅 ${pickedMatch} 个`);
  return parts.join(' · ');
}

function importToast(result) {
  const created = result.created_count || 0;
  const subscribed = result.subscribed_count || 0;
  const parts = [];
  if (created) parts.push(`新建 ${created} 个自定源`);
  if (subscribed) parts.push(`订阅 ${subscribed} 个已收录来源`);
  let message = `已导入订阅：${parts.join('，')}`;
  if (created) message += '，内容随后自动抓取';
  return message;
}

function notifySubscriptionsChanged() {
  window.dispatchEvent(new CustomEvent('dorami-subscriptions-changed'));
}

export function OpmlPreview({
  filename,
  opml,
  plan,
  layout = 'desktop',
  showToast,
  onCancel,
  onDone,
}) {
  const items = plan?.items || [];
  const counts = plan?.counts || {};
  const [picked, setPicked] = useState(() => new Set(
    items.filter((item) => item.selectable && item.canonical_url).map((item) => item.canonical_url),
  ));
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    setPicked(new Set(
      items.filter((item) => item.selectable && item.canonical_url).map((item) => item.canonical_url),
    ));
  }, [plan]); // eslint-disable-line react-hooks/exhaustive-deps -- 预览结果整份替换时重置勾选

  const toggle = (url) => {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(url)) next.delete(url);
      else next.add(url);
      return next;
    });
  };

  const pickedItems = items.filter((item) => item.selectable && picked.has(item.canonical_url));
  const pickedNew = pickedItems.filter((item) => item.status === 'new').length;
  const pickedMatch = pickedItems.filter((item) => item.status === 'matched_existing').length;
  const label = confirmLabel(pickedNew, pickedMatch);
  const canConfirm = !confirming && pickedNew + pickedMatch > 0;

  const handleConfirm = async () => {
    if (!canConfirm) return;
    setConfirming(true);
    try {
      const result = await confirmOpmlImport(opml, [...picked]);
      const created = result.created_count || 0;
      const subscribed = result.subscribed_count || 0;
      if (created || subscribed) {
        notifySubscriptionsChanged();
        showToast(importToast(result), 'success');
        onDone?.(result);
      } else {
        showToast('没有写入新的订阅', 'info');
      }
    } catch (error) {
      showToast(error.message || '导入订阅失败', 'error');
    } finally {
      setConfirming(false);
    }
  };

  const chips = CHIP_META.filter((chip) => (counts[chip.key] || 0) > 0);
  const quota = plan?.quota;
  const quotaNote = quota && Number.isFinite(Number(quota.remaining)) && (
    <p className="opml-note">
      还可新建 {quota.remaining} 个自定源（已有 {quota.used}/{quota.max}，今日 {quota.daily_used}/{quota.daily_max}）
    </p>
  );
  const list = (
    <ul className="opml-list">
      {items.map((item) => {
        const detail = itemDetail(item);
        const stamp = ROW_STAMP[item.status] || ROW_STAMP.invalid;
        const checked = Boolean(item.selectable && picked.has(item.canonical_url));
        return (
          <li key={item.canonical_url || item.xml_url} className={`opml-item ${item.selectable ? '' : 'is-idle'}`}>
            <input
              type="checkbox"
              checked={checked}
              disabled={!item.selectable || confirming}
              aria-label={item.title || item.xml_url}
              onChange={() => item.canonical_url && toggle(item.canonical_url)}
            />
            <span className="opml-item-main">
              <span className="opml-item-title">{item.title || '未命名来源'}</span>
              {detail.text && (
                <span className={detail.kind === 'url' ? 'opml-item-url' : `opml-item-reason is-${detail.kind}`}>
                  {detail.text}
                </span>
              )}
            </span>
            <span className={`stamp opml-stamp ${stamp.stamp}`}>{stamp.label}</span>
          </li>
        );
      })}
    </ul>
  );
  const chipRow = chips.length > 0 && (
    <div className="opml-chips" aria-label="导入结果统计">
      {chips.map((chip) => (
        <span key={chip.key} className={`stamp opml-stamp ${chip.stamp}`}>
          {chip.label} {counts[chip.key]}
        </span>
      ))}
    </div>
  );
  const duplicate = (plan?.duplicate_count || 0) > 0 && (
    <p className="opml-note">文件里有 {plan.duplicate_count} 条重复地址，已按 feed 地址合并</p>
  );

  const confirmButton = (
    <button
      type="button"
      className={layout === 'mobile'
        ? 'action-button action-button-primary'
        : 'action-button action-button-primary min-h-[32px] px-3 text-xs'}
      disabled={!canConfirm}
      onClick={handleConfirm}
    >
      {confirming && <Loader2 className="h-4 w-4 animate-spin" />}
      {confirming ? '正在导入' : label}
    </button>
  );

  if (layout === 'mobile') {
    return (
      <>
        <div className="m-page-scroll opml-mobile-scroll">
          <div className="opml-back-row">
            <span className="sett-stat opml-filemeta">{filename} · {items.length} 条</span>
          </div>
          {chipRow}
          {quotaNote}
          {duplicate}
          {list}
        </div>
        <div className="opml-mobile-bar">
          <span className="opml-foot-note">尚未写入任何订阅</span>
          {confirmButton}
        </div>
      </>
    );
  }

  return (
    <div className="opml-preview">
      <div className="opml-back-row">
        <button type="button" className="opml-back" onClick={onCancel}>
          <ChevronLeft aria-hidden="true" /> 返回
        </button>
        <span className="opml-preview-title">导入预览</span>
        <span className="sett-stat opml-filemeta">{filename} · {items.length} 条</span>
      </div>
      {chipRow}
      {quotaNote}
      {duplicate}
      {list}
      <div className="sett-sync-foot opml-foot">
        <span className="opml-foot-note">尚未写入任何订阅</span>
        <span className="opml-foot-actions">
          <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" onClick={onCancel} disabled={confirming}>
            取消
          </button>
          {confirmButton}
        </span>
      </div>
    </div>
  );
}

// 设置 → 通用 → 订阅源 OPML。桌面预览留在本分区;手机由 onPushPreview 推到独立一屏。
export default function OpmlSection({ showToast, onPushPreview }) {
  const [summary, setSummary] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState(null);
  const inputRef = useRef(null);
  const abortRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    fetchOpmlSummary()
      .then((data) => { if (!cancelled) setSummary(data); })
      .catch(() => { if (!cancelled) setSummary(null); });
    return () => { cancelled = true; };
  }, [reloadKey]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const handleExport = async () => {
    setExporting(true);
    try {
      const result = await downloadSubscriptionOpml();
      downloadBlob(result.blob, result.filename);
      const skipped = result.skipped
        ? `，另有 ${result.skipped} 个非 RSS 来源未写入`
        : '';
      showToast(`已导出 ${result.exportable} 个 RSS 订阅${skipped}`, 'success');
      setSummary({ exportable_count: result.exportable, skipped_count: result.skipped });
    } catch (error) {
      showToast(error.message || '导出订阅失败', 'error');
    } finally {
      setExporting(false);
    }
  };

  const handleFile = async (file) => {
    if (!file) return;
    let text;
    try {
      text = await file.text();
    } catch {
      showToast('无法读取该文件', 'error');
      return;
    }
    if (text.length > OPML_MAX_CHARS) {
      showToast('文件超过 100 万字符，请拆分后再导入', 'error');
      return;
    }
    if (!text.trim()) {
      showToast('这份文件是空的', 'error');
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setPreviewing(true);
    try {
      const plan = await previewOpmlImport(text, controller.signal);
      const payload = { opml: text, filename: file.name, plan };
      if (onPushPreview) onPushPreview(payload);
      else setPreview(payload);
    } catch (error) {
      if (error?.name === 'AbortError') return;
      showToast(error.message || '预览导入失败', 'error');
    } finally {
      if (abortRef.current === controller) setPreviewing(false);
    }
  };

  if (preview && !onPushPreview) {
    return (
      <OpmlPreview
        filename={preview.filename}
        opml={preview.opml}
        plan={preview.plan}
        showToast={showToast}
        onCancel={() => setPreview(null)}
        onDone={() => {
          setPreview(null);
          setReloadKey((key) => key + 1);
        }}
      />
    );
  }

  const exportable = summary?.exportable_count;
  const skipped = summary?.skipped_count;

  return (
    <div className="opml-home">
      <div className="sett-row">
        <span className="sett-id">
          <span className="sett-lbl">导出我的订阅</span>
          <div className="sett-sub">
            生成 .opml 文件，可导入 Feedly、Reeder、Folo 等阅读器。文件含订阅地址，请妥善保管。
            {summary && (
              <>
                <br />
                <span className="sett-stat">{exportable} 个 RSS 源</span>
                {' '}将导出
                {skipped > 0 && (
                  <>；X / 网页抓取等 {skipped} 个非 RSS 来源无法写入 OPML，已跳过</>
                )}
              </>
            )}
            {!summary && <><br />正在统计可导出的订阅</>}
          </div>
        </span>
        <span className="sett-ctl">
          <button
            type="button"
            className="action-button action-button-secondary min-h-[32px] px-3 text-xs"
            onClick={handleExport}
            disabled={exporting}
          >
            {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            导出 .opml
          </button>
        </span>
      </div>
      <div className="sett-row is-block">
        <div className="sett-lbl">从 OPML 导入</div>
        <div className="sett-sub opml-import-sub">
          先预览每一条的处理结果，确认后才会订阅。已收录的来源直接订阅，新地址添加为只有你可见的自定源，内容随后自动抓取
        </div>
        <button
          type="button"
          className="sett-file"
          disabled={previewing}
          aria-busy={previewing}
          onClick={() => inputRef.current?.click()}
        >
          {previewing ? <Loader2 className="animate-spin" /> : <FileText />}
          <span className="min-w-0">
            <span className="sett-file-name">{previewing ? '正在核对每条地址' : '选择 OPML 文件'}</span>
            <span className="sett-file-hint">
              {previewing ? '确认前不会写入' : '支持 .opml / .xml，单次最多 200 条'}
            </span>
          </span>
        </button>
        <input
          ref={inputRef}
          type="file"
          accept=".opml,.xml,text/xml,application/xml"
          className="opml-file-input"
          aria-hidden="true"
          tabIndex={-1}
          onChange={(event) => {
            const file = event.target.files?.[0] || null;
            event.target.value = '';
            if (file) handleFile(file);
          }}
        />
      </div>
    </div>
  );
}
