// 播放条与收听入口共用的小图形(issue #199)。▶ / ⏸ 的路径与 playerFx.js 的 PLAY_SVG / PAUSE_SVG 逐字一致:
// 形变层里飞过去的那颗图标与落地后的真实图标必须是同一个形状,交接那一帧才看不出差异。

const svgProps = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
};

export function PlayGlyph() {
  return (
    <svg {...svgProps}>
      <polygon points="6 3 20 12 6 21 6 3" fill="currentColor" />
    </svg>
  );
}

export function PauseGlyph() {
  return (
    <svg {...svgProps}>
      <rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor" />
      <rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor" />
    </svg>
  );
}

/** 停止播放(手机悬浮卡,issue #230):实心圆角方块,与播放 / 暂停同一套 24 栅格。 */
export function StopGlyph() {
  return (
    <svg {...svgProps}>
      <rect x="5" y="5" width="14" height="14" rx="3" fill="currentColor" stroke="none" />
    </svg>
  );
}

/** ±15 秒(手机悬浮卡,拍板 34):圆弧箭头里写秒数,弧心 = 图标中心。 */
export function SkipGlyph({ back = false, seconds = 15 }) {
  return (
    <span className="player-cskip-g" aria-hidden="true">
      <svg {...svgProps} strokeWidth={1.7}>
        {back ? (
          <>
            <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
            <path d="M3 3v5h5" />
          </>
        ) : (
          <>
            <path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" />
            <path d="M21 3v5h-5" />
          </>
        )}
      </svg>
      <b>{seconds}</b>
    </span>
  );
}

/** AI 身份 ✦(四角星蒙版 + --grad-ai):只上 AI 触点,不铺底。 */
export function Spark({ breathing = false, className = '' }) {
  return <span className={`player-spark${breathing ? ' is-breathing' : ''}${className ? ` ${className}` : ''}`} aria-hidden="true" />;
}

/** 律动条:播放态的「正在这里」。live = 正在出声(动起来),否则停在静止帧。AI 音轨走渐变两端色。 */
export function Eq({ live = false, ai = false }) {
  return (
    <span className={`player-eq${ai ? ' is-ai' : ''}${live ? ' is-live' : ''}`} aria-hidden="true">
      <i /><i /><i />
    </span>
  );
}
