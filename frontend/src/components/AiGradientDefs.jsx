// AI 家族渐变身份色的文档级 SVG defs(v3.33 拍板):蓝→紫,全站唯一的渐变
// = AI 触点(FAB/面板字头/回答身份行/空态/速读卡/译钮激活态),见渐变即 AI;
// 全站主题色 solid 靛与收藏琥珀均不动。图标经 CSS `stroke: url(#ai-grad)` 引用,
// stop-color 取 CSS 变量,亮暗随 [data-theme] 自动翻转。
// #ai-grad     正向:浅色面用深端(亮色主题)/暗色面用亮端(暗色主题)——面板、速读卡等常规表面。
// #ai-grad-inv 反向端色:供「反色底」触点使用——墨底 FAB 在亮色主题下其实是深底,
//              需要亮端才可辨;暗色主题下 FAB 底翻浅,又需要深端。
// App.jsx 在桌面/移动两个渲染根各挂一份(同帧只存在其一,id 不重复)。
export default function AiGradientDefs() {
  return (
    <svg width="0" height="0" style={{ position: 'absolute' }} aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id="ai-grad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" style={{ stopColor: 'var(--ai-g1)' }} />
          <stop offset="100%" style={{ stopColor: 'var(--ai-g2)' }} />
        </linearGradient>
        <linearGradient id="ai-grad-inv" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" style={{ stopColor: 'var(--ai-g1-inv)' }} />
          <stop offset="100%" style={{ stopColor: 'var(--ai-g2-inv)' }} />
        </linearGradient>
      </defs>
    </svg>
  );
}
