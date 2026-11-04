import { BookOpenText, Headphones } from 'lucide-react';

// 与桌面/手机搜索共用；可用文字和音频的并集是「有中文导读」。
export default function PodcastGuideFilter({ value, onChange, mobile = false }) {
  return (
    <div className={`reader-kind-band${mobile ? ' m-kind-band' : ''}`} role="group" aria-label="按导读筛选">
      {[
        ['', '全部', null], ['any', '有中文导读', null],
        ['listenable', '可听', Headphones], ['readable', '可看', BookOpenText],
      ].map(([key, label, Icon]) => (
        <button key={key} type="button" aria-pressed={value === key}
          onClick={() => onChange(key)} className={`reader-kind-chip inline-flex items-center gap-1 ${value === key ? 'is-on' : ''}`}>
          {Icon && <Icon className="h-3 w-3" aria-hidden="true" />}{label}
        </button>
      ))}
    </div>
  );
}
