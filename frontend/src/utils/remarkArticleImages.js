// Resolve archived relative images at display time; never rewrite the archive.
export default function remarkArticleImages({ sourceUrl } = {}) {
  return (tree) => {
    let base;
    try { base = new URL(sourceUrl); } catch { return; }
    if (!['http:', 'https:'].includes(base.protocol)) return;
    const references = new Set();
    const walk = (node, fn) => {
      fn(node);
      node.children?.forEach((child) => walk(child, fn));
    };
    walk(tree, (node) => {
      if (node.type === 'imageReference') references.add(node.identifier);
    });
    walk(tree, (node) => {
      if (node.type !== 'image' && !(node.type === 'definition' && references.has(node.identifier))) return;
      if (!node.url || node.url.startsWith('#') || /^[a-z][a-z\d+.-]*:/i.test(node.url)) return;
      try {
        const url = new URL(node.url, base);
        if (['http:', 'https:'].includes(url.protocol)) node.url = url.href;
      } catch { /* Invalid URLs keep ReactMarkdown's default safety handling. */ }
    });
  };
}
