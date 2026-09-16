// 弹窗遮罩关闭判定(issue #104)的回归用例:项目没有前端测试框架,用 Node 内建 node:test 直跑。
// ① utils/overlayClose.js 是纯状态机,用假事件走各路径(面板内拖选、遮罩按下拖进面板、触屏滚动
//    接管、右键、连续序列);② 用仓库真实 eslint 配置跑 Linter,证明护栏 dorami/no-raw-modal-overlay
//    拦得住裸 .modal-overlay 元素、放过共用外壳本体与正当用法。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { Linter } from 'eslint';
import { createOverlayCloseGuard } from '../src/utils/overlayClose.js';
import eslintConfig from '../eslint.config.js';

// ── ① 判定状态机 ──
const overlay = { id: 'overlay' };
const panel = { id: 'panel' };
const evt = (target, extra = {}) => ({ target, currentTarget: overlay, button: 0, ...extra });

// 'onClick' 步骤问 shouldClose 并计数(Modal.jsx 的 onClick 就是这么用的),其余步骤喂 pointer handler。
function closesAfter(steps) {
  let closed = 0;
  const guard = createOverlayCloseGuard();
  for (const [handler, target, extra] of steps) {
    if (handler === 'onClick') { if (guard.shouldClose(evt(target, extra))) closed += 1; }
    else guard[handler](evt(target, extra));
  }
  return closed;
}

test('遮罩上按下并松开的真正点击才关闭', () => {
  assert.equal(closesAfter([['onPointerDown', overlay], ['onPointerUp', overlay], ['onClick', overlay]]), 1);
});

test('面板内按下、遮罩上松手(拖选文字)不关闭', () => {
  // click 派发到 mousedown / mouseup 目标的公共祖先 = 遮罩,裸 onClick 会在这里误关
  assert.equal(closesAfter([['onPointerDown', panel], ['onPointerUp', overlay], ['onClick', overlay]]), 0);
});

test('遮罩上按下、拖进面板松手不关闭', () => {
  assert.equal(closesAfter([['onPointerDown', overlay], ['onPointerUp', panel], ['onClick', overlay]]), 0);
});

test('面板内子元素吞掉 pointerup(stopPropagation)时同样不关闭', () => {
  // 遮罩按下、面板松开但 pointerup 未冒泡到遮罩,只剩 click 抵达
  assert.equal(closesAfter([['onPointerDown', overlay], ['onClick', overlay]]), 0);
});

test('触屏滚动接管(pointercancel)后不关闭', () => {
  assert.equal(closesAfter([['onPointerDown', overlay], ['onPointerCancel', overlay], ['onClick', overlay]]), 0);
});

test('右键 / 中键按下不算', () => {
  assert.equal(closesAfter([['onPointerDown', overlay, { button: 2 }], ['onPointerUp', overlay], ['onClick', overlay]]), 0);
  assert.equal(closesAfter([['onPointerDown', overlay, { button: 1 }], ['onPointerUp', overlay], ['onClick', overlay]]), 0);
});

test('没有 pointer 序列的 click(合成 / 键盘)不关闭', () => {
  assert.equal(closesAfter([['onClick', overlay]]), 0);
});

test('每次 click 后状态归零:关一次之后的面板内拖选不会被带成关闭,遮罩点击仍可再关', () => {
  assert.equal(closesAfter([
    ['onPointerDown', overlay], ['onPointerUp', overlay], ['onClick', overlay],
    ['onPointerDown', panel], ['onPointerUp', overlay], ['onClick', overlay],
    ['onPointerDown', overlay], ['onPointerUp', overlay], ['onClick', overlay],
  ]), 2);
});

// ── ② lint 护栏(真实配置) ──
const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const linter = new Linter({ cwd: frontendDir });
const RULE = 'dorami/no-raw-modal-overlay';
const lintRuleHits = (code, file = 'src/components/Foo.jsx') => linter
  .verify(code, eslintConfig, { filename: path.join(frontendDir, file) })
  .filter((m) => m.ruleId === RULE).length;

test('护栏:裸 .modal-overlay 元素被拦(字面量 / 模板字面量 / 三元 / 拼接)', () => {
  assert.equal(lintRuleHits('export const A = ({ close }) => <div className="modal-overlay" onClick={close} />;'), 1);
  assert.equal(lintRuleHits('export const A = ({ closing }) => <div className={`modal-overlay ${closing ? "is-closing" : ""}`} />;'), 1);
  assert.equal(lintRuleHits('export const A = ({ x }) => <div className={x ? "modal-overlay" : "other"} />;'), 1);
  assert.equal(lintRuleHits('export const A = ({ x }) => <div className={"modal-overlay " + x} />;'), 1);
});

test('护栏:共用外壳本体豁免,<Modal> 调用方与其它类名不报', () => {
  assert.equal(lintRuleHits('export const A = () => <div className="modal-overlay" />;', 'src/components/Modal.jsx'), 0);
  assert.equal(lintRuleHits('export const A = () => <div className="modal-panel form-sheet" />;'), 0);
  assert.equal(lintRuleHits('export const A = ({ close }) => <div className="m-dim" onClick={close} />;'), 0);
  assert.equal(lintRuleHits('export const A = () => <div overlayClassName="modal-overlay" />;'), 0);
});
