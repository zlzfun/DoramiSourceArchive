import { useState } from 'react';
import { createPortal } from 'react-dom';
import { ChevronRight, Download, X } from 'lucide-react';
import Modal from './Modal';
import { installGuide, promptInstall, usePwa } from '../pwa';

const GUIDES = {
  ios: { steps: ['在 Safari 中打开哆啦美。', '打开分享菜单，选择「添加到主屏幕」。', '若有「作为 Web App 打开」开关，请开启，再点「添加」。'], note: '从主屏幕图标打开后，可能需要重新登录。' },
  chromium: { steps: ['打开浏览器菜单。', '选择「安装应用」或「添加到主屏幕」，按提示确认。'], note: '菜单名称和安装能力因设备而异；没有这个选项时，可以继续在浏览器中阅读。' },
  generic: { steps: ['在浏览器菜单中查找「安装应用」「添加至桌面」或「添加到主屏幕」。'], note: '如果没有这些选项，当前浏览器可能不支持安装，可以收藏网址继续阅读。' },
  embedded: { steps: ['先通过右上角菜单，在系统浏览器中打开哆啦美。', '再从浏览器菜单添加到主屏幕。'], note: '应用内置浏览器通常不提供完整安装能力。' },
  insecure: { steps: ['请使用本站的 HTTPS 安全地址打开。'], note: '当前连接不满足完整 PWA 安装条件，仍可继续在浏览器中阅读。' },
};

function InstallDetails({ error }) {
  const [platform, setPlatform] = useState(installGuide);
  const guide = GUIDES[platform];
  return (
    <div className="pwa-guide">
      <p className="pwa-intro">把哆啦美放到桌面，下次从图标开始阅读。</p>
      {error && <p role="status" className="pwa-note">暂未完成安装，可以按下面的步骤手动添加。</p>}
      <ol className="pwa-steps">{guide.steps.map((step) => <li key={step}>{step}</li>)}</ol>
      <p className="pwa-note">{guide.note}</p>
      {!['embedded', 'insecure'].includes(platform) && (
        <label className="pwa-platform">其他设备的指引
          <select value={platform} onChange={(event) => setPlatform(event.target.value)} aria-label="安装指引平台">
            <option value="ios">iPhone / iPad</option>
            <option value="chromium">Chrome / Edge</option>
            <option value="generic">其他浏览器</option>
          </select>
        </label>
      )}
    </div>
  );
}

// Settings uses an inline disclosure, avoiding nested modal focus traps on desktop/tablet.
export default function InstallApp({ settings = false }) {
  const { installed, canPrompt, installEnabled } = usePwa();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  if (installed || !installEnabled) return null;
  const activate = async () => {
    if (!canPrompt) { setError(false); setOpen(!open); return; }
    setBusy(true);
    const outcome = await promptInstall();
    setBusy(false);
    if (outcome === 'error' || outcome === 'unavailable') { setError(true); setOpen(true); }
    // Neither dismissal nor acceptance needs another prompt or nag.
  };
  return (
    <>
      {settings ? (
        <div className="sett-row">
          <span className="sett-id"><span className="sett-lbl">添加到主屏幕</span><span className="sett-sub block">从桌面图标开始阅读</span></span>
          <span className="sett-ctl"><button type="button" className="action-button action-button-secondary min-h-[44px] px-3 text-xs" disabled={busy} onClick={activate} aria-expanded={open}>{busy ? '正在打开…' : canPrompt ? '添加应用' : open ? '收起步骤' : '查看步骤'}</button></span>
        </div>
      ) : (
        <button type="button" className="m-row" disabled={busy} onClick={activate}>
          <Download aria-hidden="true" /><span className="m-row-label">添加到主屏幕</span>
          <span className="m-row-meta">{busy ? '正在打开…' : canPrompt ? '安装应用' : '查看步骤'}</span>
          <span className="m-row-chev" aria-hidden="true"><ChevronRight /></span>
        </button>
      )}
      {settings ? open && <InstallDetails error={error} /> : createPortal(
        <Modal open={open} onClose={() => setOpen(false)} size="md" centered closeOnOverlay overlayClassName="pwa-overlay" panelClassName="pwa-panel" ariaLabel="添加到主屏幕">
          <header className="pwa-heading">
            <img src="/brand/dorami-logo-48.png" alt="" width="40" height="40" />
            <h2>添加到主屏幕</h2>
            <button type="button" className="icon-button" onClick={() => setOpen(false)} aria-label="关闭安装指引"><X size={20} /></button>
          </header>
          <InstallDetails error={error} />
        </Modal>, document.body,
      )}
    </>
  );
}
