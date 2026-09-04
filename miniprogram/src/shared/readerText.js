// 自 frontend/src/utils/readerText.js 复制(DOM 无关纯函数);改动须两侧同步。
export function formatDate(value) {
  if (!value) return '';
  return String(value).replace('T', ' ').substring(0, 10);
}

export function stripMarkdown(text) {
  if (!text) return '';
  return String(text)
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+\.\s+/gm, '')
    .replace(/^\s*>\s?/gm, '')
    .replace(/[*_`~]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

// 与后端 services/reader_ai.looks_chinese 同启发式:CJK > 拉丁 × 0.2
export function looksChinese(text) {
  const sample = String(text || '').slice(0, 2000);
  const cjk = (sample.match(/[\u3400-\u9fff]/g) || []).length;
  const latin = (sample.match(/[A-Za-z]/g) || []).length;
  return cjk > latin * 0.2;
}

// 小程序无 URL 构造器保证(部分基础库缺失),正则取 host
export function hostOf(url) {
  const m = /^https?:\/\/([^/?#]+)/i.exec(String(url || ''));
  return m ? m[1].replace(/^www\./, '') : '';
}

export function excerptOf(content, maxLen = 140) {
  return stripMarkdown(content).slice(0, maxLen);
}

export function formatPodcastDuration(seconds) {
  const n = Number(seconds);
  if (!Number.isFinite(n) || n <= 0) return '';
  const h = Math.floor(n / 3600);
  const m = Math.floor((n % 3600) / 60);
  if (h > 0) return `${h} 小时 ${m} 分`;
  return `${m} 分钟`;
}
