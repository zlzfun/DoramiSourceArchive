// 统一的剪贴板写入：优先 Clipboard API，失败回退到隐藏 textarea + execCommand。
// 契约：成功 resolve，失败 throw（调用方 catch 后提示）。
export async function copyText(text) {
  if (!text) throw new Error('没有可复制的内容');
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      /* 浏览器拒绝 Clipboard API（非安全上下文等），回退到 textarea */
    }
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  document.body.appendChild(textarea);
  textarea.select();
  try {
    if (!document.execCommand('copy')) throw new Error('浏览器拒绝复制');
  } finally {
    document.body.removeChild(textarea);
  }
}
