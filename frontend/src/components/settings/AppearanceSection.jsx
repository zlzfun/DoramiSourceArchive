import { useRef } from 'react';
import { Monitor, Moon, Sun } from 'lucide-react';
import { useMotionPref } from '../../motion';
import { usePalettePref } from '../../palette';
import InstallApp from '../InstallApp';

// 中性色三选一(issue #184):小卡本身就是该套配色的缩影——外框色 + 内容色 + 墨色线条,
// 颜色取自当前主题下这一套的值(.sett-pal[data-pal] 在 index.css 里按亮暗给),点了立即生效。
const PALETTE_OPTIONS = [
  { id: 'porcelain', name: '瓷', tone: '冷调' },
  { id: 'graphite', name: '墨', tone: '中性' },
  { id: 'paper', name: '宣', tone: '暖调' },
];
// tone 只进读屏名与悬停提示:卡上只写一个名字,冷暖由色卡本身呈现(issue #291)。

// 外观(弹窗波,设置行范式):主题三态,白拇指分段(复用 .segmented-control)。
// 减少动效(issue #73)与中性色(issue #184)都是应用内偏好,自持于 hook(motion.js / palette.js),
// 桌面设置柜与移动设置栈两处渲染同一组件,无需穿 props。
export default function AppearanceSection({ theme, onThemeChange }) {
  const { reduced, setMotion } = useMotionPref();
  const { palette, setPalette } = usePalettePref();
  // 中性色是单选组(codex R1-P2-06):组内只有选中那张卡在 Tab 序列里,方向键 / Home / End 移动并选中
  const palRefs = useRef([]);
  const onPaletteKey = (event, index) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    let next = null;
    if (step) next = (index + step + PALETTE_OPTIONS.length) % PALETTE_OPTIONS.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = PALETTE_OPTIONS.length - 1;
    if (next == null) return;
    event.preventDefault();
    setPalette(PALETTE_OPTIONS[next].id);
    palRefs.current[next]?.focus();
  };
  const options = [
    { id: 'light', label: '亮色', icon: Sun },
    { id: 'dark', label: '暗色', icon: Moon },
    { id: 'system', label: '跟随系统', icon: Monitor },
  ];
  return (
    <>
      <InstallApp settings />
      <div className="sett-row">
        <span className="sett-id">
          <span className="sett-lbl">主题</span>
        </span>
        <span className="sett-ctl">
          <span className="sett-seg" role="group" aria-label="主题">
            {options.map(opt => (
              <button
                key={opt.id}
                type="button"
                onClick={() => onThemeChange(opt.id)}
                className={`sett-seg-btn ${theme === opt.id ? 'is-on' : ''}`}
              >
                <opt.icon /> {opt.label}
              </button>
            ))}
          </span>
        </span>
      </div>
      <div className="sett-row is-pals">
        <span className="sett-id">
          <span className="sett-lbl">中性色</span>
        </span>
        <span className="sett-ctl">
          <span className="sett-pals" role="radiogroup" aria-label="中性色">
            {PALETTE_OPTIONS.map((opt, index) => (
              <button
                key={opt.id}
                ref={(el) => { palRefs.current[index] = el; }}
                type="button"
                role="radio"
                aria-checked={palette === opt.id}
                aria-label={`${opt.name} · ${opt.tone}`}
                title={opt.tone}
                tabIndex={palette === opt.id ? 0 : -1}
                onClick={() => setPalette(opt.id)}
                onKeyDown={(event) => onPaletteKey(event, index)}
                className="sett-pal"
                data-pal={opt.id}
              >
                <span className="sett-pal-sw" aria-hidden="true"><i /><i /></span>
                <span className="sett-pal-name">{opt.name}</span>
              </button>
            ))}
          </span>
        </span>
      </div>
      <div className="sett-row">
        <span className="sett-id">
          <span className="sett-lbl">减少动效</span>
        </span>
        <span className="sett-ctl">
          <button
            type="button"
            role="switch"
            aria-checked={reduced}
            aria-label="减少动效"
            onClick={() => setMotion(reduced ? 'full' : 'reduce')}
            className={`ledger-switch ${reduced ? 'is-on' : ''}`}
          />
        </span>
      </div>
    </>
  );
}
