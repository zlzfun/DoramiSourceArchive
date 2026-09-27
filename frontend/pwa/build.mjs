import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

// No precache manifest or runtime caches: the only offline resource is inline in sw.js.
export function pwaBuild() {
  return {
    name: 'dorami-pwa',
    apply: 'build',
    generateBundle(_options, bundle) {
      const revision = createHash('sha256');
      for (const key of Object.keys(bundle).sort()) {
        revision.update(key).update(bundle[key].code ?? bundle[key].source);
      }
      const offline = readFileSync(new URL('./offline.html', import.meta.url), 'utf8');
      const template = readFileSync(new URL('./sw.js', import.meta.url), 'utf8');
      this.emitFile({
        type: 'asset', fileName: 'sw.js',
        source: `// Build ${revision.digest('hex')}\n` + template.replace('"__OFFLINE_HTML__"', JSON.stringify(offline)),
      });
    },
  };
}
