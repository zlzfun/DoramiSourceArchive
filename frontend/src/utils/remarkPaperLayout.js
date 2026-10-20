// Reserved Markdown reference definitions carry only one block's presentation.
// Ordinary Markdown readers hide these definitions. No raw HTML or source CSS
// is enabled; all rendered properties come from this fixed whitelist.
const STYLE_CLASSES = {
  zq1: 'paper-abstract-title',
  zq2: 'paper-caption',
  zq3: 'paper-align-left',
  zq4: 'paper-align-center',
  zq5: 'paper-align-right',
};
const BLOCK_TYPES = new Set(['heading', 'paragraph', 'list', 'table']);

export default function remarkPaperLayout() {
  return (tree) => {
    const visit = (parent) => {
      if (!Array.isArray(parent.children)) return;
      let pending = null;
      parent.children = parent.children.filter((node) => {
        if (node.type === 'definition' && node.identifier === 'zqpl') {
          const styles = node.url.startsWith('#') ? node.url.slice(1).split('.') : [];
          pending = styles.length > 0 && styles.every((style) => Object.hasOwn(STYLE_CLASSES, style))
            ? [...new Set(styles.map((style) => STYLE_CLASSES[style]))]
            : null;
          return false;
        }
        if (pending && BLOCK_TYPES.has(node.type)) {
          node.data = { ...node.data, hProperties: { ...node.data?.hProperties, className: pending } };
        }
        // Never let an annotation skip intervening blocks or leak into siblings.
        pending = null;
        visit(node);
        return true;
      });
    };
    visit(tree);
  };
}
