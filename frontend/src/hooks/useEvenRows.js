import { useSyncExternalStore } from 'react';

// 「列表条目一律等高」偏好(issue #279 第四批)。桌面条目行缺省按分值收放(8 分以上两行摘要、6–8 分一行、
// 6 分以下只留标题);开了这个偏好就回到每行两行摘要。只存本机(localStorage),设置柜与列表同时挂着,
// 所以用一个模块级的订阅表让两边同步——storage 事件在同一个标签页里不触发。
const KEY = 'dorami-reader-rows-even';
const listeners = new Set();
let cached = null;

function read() {
  if (cached === null) {
    try { cached = window.localStorage.getItem(KEY) === '1'; } catch { cached = false; }
  }
  return cached;
}

export function setEvenRows(on) {
  cached = Boolean(on);
  try { window.localStorage.setItem(KEY, cached ? '1' : ''); } catch { /* 隐私模式:本次会话内有效 */ }
  listeners.forEach((notify) => notify());
}

function subscribe(notify) {
  listeners.add(notify);
  return () => listeners.delete(notify);
}

export function useEvenRows() {
  return useSyncExternalStore(subscribe, read, () => false);
}
