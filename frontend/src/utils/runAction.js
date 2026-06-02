// 统一异步动作包装器：收敛遍布各组件的 try/catch + showToast + loading 样板。
//
// 用法：
//   await runAction(() => deleteArticle(id), {
//     showToast,
//     success: '删除成功',                 // string 或 (result) => string
//     onSuccess: loadArticles,             // 成功后副作用（刷新、关闭弹窗等）
//     setLoading,                          // 可选，(bool) => void
//   });
//
// 返回：成功返回原始 result；失败返回 undefined（已弹出错误 toast）。
export async function runAction(fn, {
  showToast,
  success,
  error = '网络异常',
  setLoading,
  onSuccess,
} = {}) {
  setLoading?.(true);
  try {
    const result = await fn();
    if (success != null) {
      showToast?.(typeof success === 'function' ? success(result) : success, 'success');
    }
    onSuccess?.(result);
    return result;
  } catch (e) {
    showToast?.(e.message || error, 'error');
    return undefined;
  } finally {
    setLoading?.(false);
  }
}
