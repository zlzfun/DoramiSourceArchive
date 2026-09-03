// 纯文本工具：日期格式化 + 卡片摘要提取（阅读器与知识台账共用）。

export function formatDate(value) {
  if (!value) return '';
  return String(value).replace('T', ' ').substring(0, 10);
}

// 剥离裸 markdown 标记（图片/链接/标题/列表/引用/强调/代码），折叠空白。
// 展示层清洗，不改动存储；台账摘要与阅读器卡片摘要共用同一套规则。
export function stripMarkdown(text) {
  if (!text) return '';
  return String(text)
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')         // 图片 ![alt](url)
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')      // 链接 [文本](url) → 文本
    .replace(/^#{1,6}\s+/gm, '')                  // 标题 #
    .replace(/^\s*[-*+]\s+/gm, '')                // 无序列表项
    .replace(/^\s*\d+\.\s+/gm, '')                // 有序列表项
    .replace(/^\s*>\s?/gm, '')                    // 引用 >
    .replace(/[*_`~]/g, '')                       // 强调/代码/删除线标记
    .replace(/\s+/g, ' ')
    .trim();
}

// 粗判文本是否以中文为主(v3.45):CJK 字符数 > 拉丁字母数 × 0.2——后端
// services/reader_ai.looks_chinese 的镜像,两侧阈值须一致。中文源不画「译为中文」二段。
export function looksChinese(text) {
  const sample = String(text || '').slice(0, 2000);
  const cjk = (sample.match(/[\u3400-\u9fff]/g) || []).length;
  const latin = (sample.match(/[A-Za-z]/g) || []).length;
  return cjk > latin * 0.2;
}

// 链接的展示用域名(v3.45 正文尾部「查看原文 ↗ · 域名」):剥 www.,解析失败返回 ''。
export function hostOf(url) {
  try {
    return new URL(String(url || '')).hostname.replace(/^www\./, '');
  } catch {
    return '';
  }
}

// 卡片摘要：剥离 markdown 后截断到指定长度（默认 140 字）。
export function excerptOf(content, maxLen = 140) {
  return stripMarkdown(content).slice(0, maxLen);
}
