import { useOverlayClose } from '../../hooks/useOverlayClose';

// 层的遮罩(抽屉 / 底页;面板的兄弟节点,盖住下层):点按关闭只认「按下与松开同在遮罩」(hooks/useOverlayClose)。
// 长按弹出的动作单(审计 M-01):手指在条目上按下、450ms 后动作单盖上来,松手的合成 click 落到遮罩上——
// 按下不在遮罩,所以不关。读屏不进遮罩(aria-hidden),读屏出口是面板里的抓手 / 关闭钮。
// 业务代码不自写 .m-dim(lint dorami/no-raw-modal-overlay),一律经层原语。
// 遮罩上的 mousedown 不改焦点(preventDefault):长按松手时浏览器补发的 mousedown 落在遮罩上,
// 默认动作会把刚移进动作单的焦点打回 body;遮罩本身从来不是焦点落点,拦掉它不影响点按判定(判定只看 pointer / click)。
const keepFocus = (event) => event.preventDefault();

export default function LayerScrim({ onClose, className = '' }) {
  const handlers = useOverlayClose(onClose);
  return <div className={`m-dim ${className}`.trim()} aria-hidden="true" onMouseDown={keepFocus} {...handlers} />;
}
