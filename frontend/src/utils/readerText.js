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

// 卡片摘要：剥离 markdown 后截断到指定长度（默认 140 字）。
export function excerptOf(content, maxLen = 140) {
  return stripMarkdown(content).slice(0, maxLen);
}
