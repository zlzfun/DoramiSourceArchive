import { useRef } from 'react';
import { ChevronLeft, UserRound } from 'lucide-react';
import { avatarHue, avatarInitial } from '../../utils/avatarColor';
import { dialCharCount, dialIndex, dialKeyTarget, dialReserve } from '../../utils/titleDial';

// ── 手机壳页头(issue #230 阶段 3 第 2 批;样页拍板 31 B「标题即切换」,细节页 details/tab-header.html)──
// 四个 Tab 与设置页都只有一行 52px:领位(头像 / 返回钮,按钮左 8px、顶栏中线)· 标题位 · 弹性空白 · 动作(右沿 6px,命中 44)。
// 标题位就是本 Tab 的主切换(TitleDial,左锚定转盘);只有一项时是 HeadTitle。标题字形左沿固定 62px(8 + 44 + 10)。
// 页头浮在内容之上(内容从它下面滚过,第一块从 sat + 56 开始);滚过 4px 换镀层底 .m-veil
// (Tab 页看壳上的 data-scrolled,设置页看自己的 is-scrolled)。切 Tab 时页头瞬切,不带位移动画。
export function MobileHeader({ lead = null, title = null, actions = null, className = '' }) {
  return (
    <>
      <div className="m-veil" aria-hidden="true" />
      <div className={`m-head ${className}`.trim()}>
        {lead}
        {title}
        <span className="m-head-sp" />
        {actions}
      </div>
    </>
  );
}

// 页头左上的头像:四个 Tab 同一位置,点了推入设置页(拍板 27);游客是人形图标;头像上不挂任何点(拍板 29)。
export function HeadAvatar({ account, onClick, buttonRef = null }) {
  const guest = account?.role === 'guest';
  const name = guest ? '游客' : (account?.username || '我');
  let face;
  if (guest) {
    face = <span className="m-me-av is-guest"><UserRound aria-hidden="true" /></span>;
  } else if (account?.avatar) {
    face = <img className="m-me-av" src={account.avatar} alt="" draggable={false} />;
  } else {
    face = (
      <span className="m-me-av avatar-letter" style={{ '--avatar-h': avatarHue(account?.username) }}>
        {avatarInitial(account?.username)}
      </span>
    );
  }
  return (
    <button
      ref={buttonRef}
      type="button"
      className="m-me-btn"
      onClick={onClick}
      aria-haspopup="dialog"
      aria-label={`${name}，打开设置`}
    >
      {face}
    </button>
  );
}

// 返回钮:落在头像原位(设置页、设置分区页;后续来源流同)。
export function HeadBack({ label = '返回', onClick }) {
  return (
    <button type="button" className="m-gbtn m-head-lead" onClick={onClick} aria-label={label}>
      <span className="m-gbtn-in"><ChevronLeft aria-hidden="true" /></span>
    </button>
  );
}

// 只有一项的标题位(早报 / 设置 / 设置分区):字样与转盘当前项相同,放大溢出的部分用右外距让出来。
export function HeadTitle({ children }) {
  const count = Array.from(String(children ?? '')).length;
  return (
    <h1 className="m-htitle" style={{ '--tch': count }}>
      <span className="m-hname">{children}</span>
    </h1>
  );
}

// 标题位转盘:role="tablist" + 漫游 tabindex(← → 循环、Home / End);点任一项或方向键切换。
// 各项固定槽位,第一槽紧贴领位;当前项以左沿为原点放大 1.3 倍、转墨色,其余 faint;选中不改任何尺寸,别的项不被挤动。
// actions = 右侧动作钮的个数,只用来算槽宽预留(utils/titleDial.dialReserve)。
export function TitleDial({ items, value, onChange, label, actions = 0 }) {
  const ref = useRef(null);
  const index = dialIndex(items, value);
  const onKeyDown = (event) => {
    const to = dialKeyTarget(event.key, index, items.length);
    if (to == null) return;
    event.preventDefault();
    if (to !== index) onChange?.(items[to][0]);
    ref.current?.querySelectorAll('[role="tab"]')[to]?.focus({ preventScroll: true });
  };
  return (
    <div
      ref={ref}
      className="m-dial"
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
      style={{
        '--n': items.length,
        '--i': index,
        '--ch': dialCharCount(items),
        '--reserve': `${dialReserve(actions)}px`,
      }}
    >
      {items.map(([value_, text], k) => {
        const on = k === index;
        return (
          <button
            key={value_}
            type="button"
            role="tab"
            className={`m-dial-tab ${on ? 'is-on' : ''}`}
            aria-selected={on}
            tabIndex={on ? 0 : -1}
            onClick={() => { if (!on) onChange?.(value_); }}
          >
            <span className="m-hname">{text}</span>
          </button>
        );
      })}
    </div>
  );
}

// 页头 / 设置里的磨砂圆钮:命中 44,看得见的是 36 的镀层圆。
export function GlassBtn({ label, onClick, pressed, disabled = false, className = '', children, ...rest }) {
  return (
    <button
      type="button"
      className={`m-gbtn ${className}`.trim()}
      aria-label={label}
      aria-pressed={pressed}
      disabled={disabled}
      onClick={onClick}
      {...rest}
    >
      <span className="m-gbtn-in">{children}</span>
    </button>
  );
}
