import { Monitor, Moon, Sun } from 'lucide-react';

// 外观(弹窗波,设置行范式):主题三态,白拇指分段(复用 .segmented-control)。
export default function AppearanceSection({ theme, onThemeChange }) {
  const options = [
    { id: 'light', label: '亮色', icon: Sun },
    { id: 'dark', label: '暗色', icon: Moon },
    { id: 'system', label: '跟随系统', icon: Monitor },
  ];
  return (
    <div className="sett-row">
      <span className="sett-id">
        <span className="sett-lbl">主题</span>
        <div className="sett-sub">跟随系统时,随操作系统亮暗自动切换</div>
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
  );
}
