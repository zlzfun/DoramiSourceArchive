// 公告横幅(v3.18 互通波,读者面):管理员的少量重要通知,悬浮玻璃卡形态——
// 不占布局、关闭/自动隐藏零位移(阅读器视图恒定)。三档语义 = 图标 + 左缘色条 +
// 半透明 tint(颜色不单独承载语义,§2);正文是受限 markdown 子集(仅 **加粗** 与
// [文字](http(s)链接)),由 utils/announcementText 白名单渲染——不走 ReaderMarkdown
// (katex/灯箱对一行公告过重,且公告场景要完全可控的行内排版)。
// AnnouncementCard 同时服务读者横幅与管理面「读者将看到」预览,保证所见即所得。
import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Info, Megaphone, X } from 'lucide-react';
import { fetchReaderAnnouncements, dismissAnnouncement } from '../api';
import { renderAnnouncementContent } from '../utils/announcementText';

const LEVEL_META = {
  info: { className: 'ann-info', Icon: Info },
  accent: { className: 'ann-accent', Icon: Megaphone },
  warning: { className: 'ann-warning', Icon: AlertTriangle },
};

// 单条公告卡:图标 + (可选)标题 + 正文同段流式 + (可选)关闭钮。
// onDismiss 缺省时不渲染关闭钮——管理面预览即此形态。
export function AnnouncementCard({ item, onDismiss }) {
  const { className, Icon } = LEVEL_META[item.level] || LEVEL_META.info;
  return (
    <div className={`ann-card ${className}`}>
      <Icon className="ann-icon" aria-hidden="true" />
      <p className="ann-text">
        {item.title ? <strong className="ann-title">{item.title}</strong> : null}
        {renderAnnouncementContent(item.content)}
      </p>
      {onDismiss ? (
        <button
          type="button"
          className="ann-close"
          aria-label={item.title ? `关闭公告「${item.title}」` : '关闭公告'}
          onClick={() => onDismiss(item.id)}
        >
          <X />
        </button>
      ) : null}
    </div>
  );
}

// 悬浮多久后自动收起(隐藏 ≠ 关闭:不写 dismiss,下次进入阅读器会再次出现;
// 悬停/键盘聚焦表示正在读,暂停计时,移开后按剩余时间续走)。
const AUTO_HIDE_MS = 8000;
const RESUME_MIN_MS = 1500; // 移开后至少再留一小段,避免「刚移开就消失」

// 读者面横幅:自管数据(挂载时拉取,失败静默——横幅不出现,阅读器照常);
// 悬浮不占布局(定位在 index.css),无公告或本次会话已自动收起时渲染 null。
export default function AnnouncementBanner() {
  const [items, setItems] = useState([]);
  // entering(入场动画中)→ shown(稳态,**无 animation**)→ leaving → hidden(本次会话)。
  // 稳态必须摘除动画类:fill-mode 残留会让容器持续成为合成层/backdrop root,
  // 子卡的 backdrop-filter 取样不到页面内容,玻璃在动画结束后失效(实测坑)。
  const [phase, setPhase] = useState('entering');
  const timerRef = useRef(null);
  const remainingRef = useRef(AUTO_HIDE_MS);
  const startedAtRef = useRef(0);

  const clearTimer = () => {
    if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
  };
  const startTimer = (ms) => {
    clearTimer();
    startedAtRef.current = Date.now();
    timerRef.current = setTimeout(() => setPhase('leaving'), ms);
  };

  useEffect(() => {
    let alive = true;
    fetchReaderAnnouncements()
      .then((res) => {
        if (!alive) return;
        const list = Array.isArray(res?.items) ? res.items : [];
        setItems(list);
        if (list.length) startTimer(AUTO_HIDE_MS);
      })
      .catch(() => {});
    return () => { alive = false; clearTimer(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 悬停/聚焦 = 正在读,暂停倒计时;离开按剩余时间续走。
  const pause = () => {
    if (!timerRef.current) return;
    remainingRef.current = Math.max(RESUME_MIN_MS, remainingRef.current - (Date.now() - startedAtRef.current));
    clearTimer();
  };
  const resume = () => {
    if ((phase === 'entering' || phase === 'shown') && !timerRef.current) startTimer(remainingRef.current);
  };

  // 关闭 = 本地立即移除 + fire-and-forget 上报(上报失败无害,下次会话再现)。
  const handleDismiss = (id) => {
    setItems((prev) => prev.filter((a) => a.id !== id));
    dismissAnnouncement(id);
  };

  // 相位推进用定时器驱动(动画时长 300ms + 余量),不依赖 animationend——
  // 动画挂在卡片(子元素)上,事件冒泡时机与条数相关,定时器行为确定。
  // entering 播完摘类进稳态(无动画残留);leaving 播完真正卸载。
  useEffect(() => {
    if (items.length === 0) return undefined;
    if (phase === 'entering') {
      const t = setTimeout(() => setPhase((c) => (c === 'entering' ? 'shown' : c)), 380);
      return () => clearTimeout(t);
    }
    if (phase === 'leaving') {
      const t = setTimeout(() => setPhase((c) => (c === 'leaving' ? 'hidden' : c)), 380);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [phase, items.length]);

  if (items.length === 0 || phase === 'hidden') return null;

  return (
    <div
      className={`reader-ann-band ${phase === 'entering' ? 'is-entering' : ''} ${phase === 'leaving' ? 'is-leaving' : ''}`}
      role="region"
      aria-label="公告"
      onMouseEnter={pause}
      onMouseLeave={resume}
      onFocusCapture={pause}
      onBlurCapture={resume}
    >
      {items.map((item) => (
        <AnnouncementCard key={item.id} item={item} onDismiss={handleDismiss} />
      ))}
    </div>
  );
}
