import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { pwaBuild } from '../pwa/build.mjs';

const source = readFileSync(new URL('../pwa/sw.js', import.meta.url), 'utf8');
const offline = readFileSync(new URL('../pwa/offline.html', import.meta.url), 'utf8');
function worker(fetchImpl) {
  const events = {};
  runInNewContext(source.replace('"__OFFLINE_HTML__"', JSON.stringify(offline)), {
    URL, Response, fetch: fetchImpl,
    self: { location: { origin: 'https://reader.example' }, addEventListener: (name, handler) => { events[name] = handler; } },
  });
  return (request = {}) => {
    let response;
    events.fetch({ request: { url: 'https://reader.example/reader', method: 'GET', mode: 'navigate', ...request },
      respondWith: (value) => { response = value; } });
    return response;
  };
}

test('worker returns fresh online navigation, not a cached shell', async () => {
  const expected = new Response('new build');
  assert.equal(await worker(async () => expected)(), expected);
});
test('offline navigation gets a standalone, no-store fallback', async () => {
  const response = await worker(async () => { throw new TypeError('offline'); })();
  assert.equal(response.status, 503);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  const html = await response.text();
  assert.match(html, /暂时无法连接/);
  assert.match(html, /<button type="button" id="retry">/);
  assert.doesNotMatch(html, /<script[^>]+src\s*=|<link|<img/); // no secondary network dependency
});
test('offline retry reloads the current document instead of resolving an empty link', () => {
  let onClick;
  let reloads = 0;
  runInNewContext(offline.match(/<script>([\s\S]+?)<\/script>/)[1], {
    document: { getElementById: (id) => {
      assert.equal(id, 'retry');
      return { addEventListener: (name, handler) => { assert.equal(name, 'click'); onClick = handler; } };
    } },
    window: { location: { reload: () => { reloads++; } } },
  });
  assert.equal(reloads, 0);
  onClick();
  assert.equal(reloads, 1);
});
test('worker never intercepts private/API/MCP, resources, writes or other origins', () => {
  const dispatch = worker(() => { throw new Error('must not fetch'); });
  for (const request of [
    { url: 'https://reader.example/api/auth/session' }, { url: 'https://reader.example/api' },
    { url: 'https://reader.example/api/articles?owner=private' },
    { url: 'https://reader.example/mcp' }, { url: 'https://reader.example/mcp/' },
    { url: 'https://reader.example/manifest.webmanifest' }, { url: 'https://reader.example/assets/app.js' },
    { url: 'https://other.example/reader' }, { method: 'POST' }, { mode: 'cors' },
  ]) assert.equal(dispatch(request), undefined, JSON.stringify(request));
});
test('HTTP errors are preserved rather than disguised as offline pages', async () => {
  const response = await worker(async () => new Response('backend error', { status: 502 }))();
  assert.equal(response.status, 502);
  assert.equal(await response.text(), 'backend error');
});
test('build fingerprint follows bundle changes without an app-shell cache', () => {
  const build = (code) => {
    let artifact;
    pwaBuild().generateBundle.call({ emitFile: (file) => { artifact = file; } }, {}, { 'app.js': { code } });
    assert.equal(artifact.fileName, 'sw.js');
    assert.doesNotMatch(artifact.source, /__OFFLINE_HTML__/);
    return artifact.source;
  };
  assert.equal(build('version1'), build('version1'));
  assert.notEqual(build('version1'), build('version2'));
});
test('manifest and PNG dimensions agree; maskable is a separate asset', () => {
  const manifest = JSON.parse(readFileSync(new URL('../public/manifest.webmanifest', import.meta.url), 'utf8'));
  assert.equal(manifest.id, '/'); assert.equal(manifest.start_url, '/');
  assert.equal(manifest.scope, '/'); assert.equal(manifest.display, 'standalone');
  for (const icon of [...manifest.icons, { src: '/brand/pwa-apple-180.png', sizes: '180x180' }]) {
    const png = readFileSync(new URL(`../public${icon.src}`, import.meta.url));
    assert.equal(png.subarray(1, 4).toString(), 'PNG');
    assert.equal(`${png.readUInt32BE(16)}x${png.readUInt32BE(20)}`, icon.sizes);
  }
  assert.equal(manifest.icons.filter((icon) => icon.purpose === 'maskable').length, 1);
});
