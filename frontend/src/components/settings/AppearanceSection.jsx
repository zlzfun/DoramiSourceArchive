import { Monitor, Moon, Sun } from 'lucide-react';
import { SectionHeading } from './SectionPrimitives';

// 外观 / 主题：亮 / 暗 / 跟随系统三态。
export default function AppearanceSection({ theme, onThemeChange }) {
  const options = [
    { id: 'light', label: '亮色', icon: Sun },
    { id: 'dark', label: '暗色', icon: Moon },
    { id: 'system', label: '跟随系统', icon: Monitor },
  ];
  return (
    <div>
      <SectionHeading title="外观" hint="选择界面主题。「跟随系统」会随设备的浅色/深色外观自动切换。" />
      <div className="surface-card rounded-[var(--r-card)] p-4">
        <p className="text-sm font-bold text-slate-700">主题</p>
        <div className="segmented-control mt-3">
          {options.map(opt => (
            <button
              key={opt.id}
              type="button"
              onClick={() => onThemeChange(opt.id)}
              className={`segmented-option ${theme === opt.id ? 'segmented-option-active' : ''}`}
            >
              <opt.icon /> {opt.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
