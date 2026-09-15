import { Monitor, Moon, Sun } from 'lucide-react';
import { useMotionPref } from '../../motion';

// 外观(弹窗波,设置行范式):主题三态,白拇指分段(复用 .segmented-control)。
// 减少动效(issue #73):应用内开关取代 OS 的 prefers-reduced-motion(见 motion.js);
// 偏好自持于 hook,桌面设置柜与移动设置栈两处渲染同一组件,无需穿 props。
export default function AppearanceSection({ theme, onThemeChange }) {
  const { reduced, setMotion } = useMotionPref();
  const options = [
    { id: 'light', label: '亮色', icon: Sun },
    { id: 'dark', label: '暗色', icon: Moon },
    { id: 'system', label: '跟随系统', icon: Monitor },
  ];
  return (
    <>
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
      <div className="sett-row">
        <span className="sett-id">
          <span className="sett-lbl">减少动效</span>
          <div className="sett-sub">开启后界面过渡与循环提示即时完成,不播放动画</div>
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
