import { useTitlePreference } from '../../hooks/useTitlePreference';

// 阅读(issue #218):标题语言偏好。开 = 有中文译名的标题显示中文、阅读页保留原题小字;关 = 一律原题。
// 只管标题,正文语言仍由阅读页的「原语言 / 中文」切换决定。偏好自持于 context(账号存服务端,游客存本机),
// 桌面设置柜与移动设置栈两处渲染同一组件,无需穿 props。
export default function ReadingSection() {
  const { preferChinese, setPreferChinese } = useTitlePreference();
  return (
    <div className="sett-row">
      <span className="sett-id">
        <span className="sett-lbl">默认显示中文标题</span>
        <div className="sett-sub">有中文译名的标题显示中文，阅读页保留原标题；正文语言不受影响</div>
      </span>
      <span className="sett-ctl">
        <button
          type="button"
          role="switch"
          aria-checked={preferChinese}
          aria-label="默认显示中文标题"
          onClick={() => setPreferChinese(!preferChinese)}
          className={`ledger-switch ${preferChinese ? 'is-on' : ''}`}
        />
      </span>
    </div>
  );
}
