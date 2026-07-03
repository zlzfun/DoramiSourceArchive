// 阅读器纯文本工具：日期格式化 + 卡片摘要提取。

export function formatDate(value) {
  if (!value) return '';
  return String(value).replace('T', ' ').substring(0, 10);
}

// 卡片摘要：去掉裸 markdown 标记（图片/标题/列表/强调），避免摘要里出现 ![](url) 等
export function excerptOf(content) {
  if (!content) return '';
  const plain = content
    .replace(/!\[[^\]]*\]\([^)]+\)/g, '')        // 图片
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')      // 链接 → 文本
    .replace(/^#{1,6}\s+/gm, '')                  // 标题
    .replace(/^\s*[-*+]\s+/gm, '')                // 列表项
    .replace(/^\s*>\s?/gm, '')                    // 引用
    .replace(/[*_`~]/g, '');                      // 强调/代码标记
  return plain.replace(/\s+/g, ' ').trim().slice(0, 140);
}
