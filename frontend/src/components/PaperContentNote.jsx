// Reading provenance, separate from AI scores (which use only the abstract).
export default function PaperContentNote({ article }) {
  let extensions;
  try {
    extensions = JSON.parse(article?.extensions_json || '{}');
  } catch {
    return null;
  }
  const paper = article?.paper || extensions?.paper;
  const isPaper = paper || article?.source_id === 'web_huggingface_daily_papers';
  if (!isPaper) return null;
  const full = paper?.content_level === 'fulltext';
  const partial = paper?.content_level === 'partial';
  const sourceUrl = /^https:\/\/arxiv\.org\/html\//.test(paper?.source_url || '') ? paper.source_url : null;
  const version = String(paper?.arxiv_id || '').match(/v\d+$/)?.[0];
  return (
    <p className="paper-content-note tiny-meta" data-ai-translation-excluded="true" lang="zh-CN">
      <span>{full ? '论文全文' : partial ? '论文正文 · 部分内容请见原文' : '论文摘要'}</span>
      <span>自动分析基于摘要</span>
      {sourceUrl && <a href={sourceUrl} target="_blank" rel="noreferrer">arXiv 原文{version ? ` · ${version}` : ''} ↗</a>}
    </p>
  );
}
