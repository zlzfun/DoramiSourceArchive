import { useRef } from 'react';
import { useTitlePreference } from '../../hooks/useTitlePreference';
import { setEvenRows, useEvenRows } from '../../hooks/useEvenRows';

// 列表条目的两种排法(issue #279 第四批的偏好,issue #291 改了形制):两张小卡各画出那种排法的样子。
// 原先是一枚开关「列表条目一律等高」,单看名字不好懂,只好在下面补一句解释;现在样子画在卡上,不需要那一句。
const ROW_OPTIONS = [
  { id: 'fit', name: '按分值收放' },
  { id: 'even', name: '一律等高' },
];

// 阅读(issue #218):标题语言偏好。开 = 有中文译名的标题显示中文、阅读页保留原题小字;关 = 一律原题。
// 只管标题,正文语言仍由阅读页的「原语言 / 中文」切换决定。偏好自持于 context(账号存服务端,游客存本机),
// 桌面设置柜与移动设置栈两处渲染同一组件,无需穿 props。
// desktop(桌面设置柜才传):多一行「列表条目」——桌面列表缺省按分值收放,手机卡片不受影响,
// 所以移动设置栈不出这一行。只存本机。
export default function ReadingSection({ desktop = false }) {
  const { preferChinese, setPreferChinese } = useTitlePreference();
  const evenRows = useEvenRows();
  // 单选组:组内只有选中那张卡在 Tab 序列里,方向键 / Home / End 移动并选中(同外观里的中性色)
  const rowRefs = useRef([]);
  const onRowKey = (event, index) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    let next = null;
    if (step) next = (index + step + ROW_OPTIONS.length) % ROW_OPTIONS.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = ROW_OPTIONS.length - 1;
    if (next == null) return;
    event.preventDefault();
    setEvenRows(ROW_OPTIONS[next].id === 'even');
    rowRefs.current[next]?.focus();
  };
  return (
    <>
    <div className="sett-row">
      <span className="sett-id">
        <span className="sett-lbl">默认显示中文标题</span>
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
    {desktop && (
      <div className="sett-row is-pals">
        <span className="sett-id">
          <span className="sett-lbl">列表条目</span>
        </span>
        <span className="sett-ctl">
          <span className="sett-pals" role="radiogroup" aria-label="列表条目">
            {ROW_OPTIONS.map((opt, index) => {
              const on = (opt.id === 'even') === evenRows;
              return (
                <button
                  key={opt.id}
                  ref={(el) => { rowRefs.current[index] = el; }}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  tabIndex={on ? 0 : -1}
                  onClick={() => setEvenRows(opt.id === 'even')}
                  onKeyDown={(event) => onRowKey(event, index)}
                  className="sett-pal sett-rowopt"
                  data-rows={opt.id}
                >
                  <span className="sett-rowopt-sw" aria-hidden="true"><i /><i /><i /></span>
                  <span className="sett-pal-name">{opt.name}</span>
                </button>
              );
            })}
          </span>
        </span>
      </div>
    )}
    </>
  );
}
