import json, re, sys, pathlib
d = pathlib.Path(sys.argv[1]); out = d / 'all-in-one.html'
index = (d/'index.html').read_text(encoding='utf-8')
dirs = {k: (d/f'direction-{k}.html').read_text(encoding='utf-8') for k in 'abc'}

NAME_FALLBACK = "new URLSearchParams(location.search || window.M230_PARAMS || '')"
NAV_SCRIPT = """<script>
/* 单文件打包版:方向页的目录链接改为通知外层切换(about:srcdoc 里相对链接无效) */
document.addEventListener('click', function (e) {
  var a = e.target && e.target.closest ? e.target.closest('[data-bundle-nav]') : null;
  if (!a) return;
  e.preventDefault();
  try { parent.postMessage({ m230: 'nav', to: a.getAttribute('data-bundle-nav') }, '*'); } catch (err) {}
}, true);
document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { try { parent.postMessage({ m230: 'esc' }, '*'); } catch (err) {} } });
</script>
</body>"""

def patch_links(html):
    html = html.replace('href="index.html"', 'href="#" data-bundle-nav="index"')
    html = re.sub(r'href="direction-([abc])\.html(#[^"]*)?"', r'href="#" data-bundle-nav="\1"', html)
    return html

patched = {}
for k, html in dirs.items():
    n = html.count('new URLSearchParams(location.search)')
    assert n >= 3, (k, n)
    html = html.replace('new URLSearchParams(location.search)', NAME_FALLBACK)
    html = patch_links(html)
    assert html.count('</body>') == 1, k
    html = html.replace('</body>', NAV_SCRIPT, 1)
    patched[k] = html

# ── index ──
idx = patch_links(index)
old_load = "      if (ifr.getAttribute('src') !== want) ifr.setAttribute('src', want);"
assert idx.count(old_load) == 1
new_load = """      if (ifr.dataset.m230 !== want) {
        ifr.dataset.m230 = want;
        var qi = want.indexOf('?'); var key = want.slice(10, 11); /* direction-<k>.html */
        ifr.srcdoc = M230_WITH(key, want.slice(qi + 1)); /* 参数注入 <head>(srcdoc 没有 location.search,window.name 也不可靠) */
      }"""
idx = idx.replace(old_load, new_load, 1)

data_script = '<script>/* 单文件打包版:三方向页原文内嵌(由 build_bundle.py 生成,勿手改;改样页请改 direction-*.html 后重新打包) */\nfunction M230_WITH(k, params) { return M230_DIR[k].replace(/<head[^>]*>/, function (m) { return m + \'<script>window.M230_PARAMS=\' + JSON.stringify(params) + \';<\\/script>\'; }); }\nvar M230_DIR = ' + json.dumps({k: v for k, v in patched.items()}, ensure_ascii=False).replace('<', '\\u003c') + ';</script>\n'
layer = """<div id="m230-layer" hidden style="position:fixed;inset:0;z-index:9999;background:#fff;display:flex;flex-direction:column">
  <div style="flex:none;height:44px;display:flex;align-items:center;gap:16px;padding:0 16px;border-bottom:1px solid rgba(0,0,0,.12);font:600 14px/1 system-ui,-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;background:#fff;color:#111">
    <button type="button" id="m230-close" style="all:unset;cursor:pointer;padding:8px 12px;border-radius:8px;background:rgba(0,0,0,.06)">← 返回总览</button>
    <span id="m230-layer-title"></span>
    <span style="margin-left:auto;font-weight:400;color:#666">单文件打包版 · Esc 关闭</span>
  </div>
  <iframe id="m230-layer-frame" name="bundle=1" title="方向页" style="flex:1;border:0;width:100%"></iframe>
</div>
<script>
(function () {
  var layer = document.getElementById('m230-layer'), frame = document.getElementById('m230-layer-frame'), title = document.getElementById('m230-layer-title');
  var names = { a: 'A 纸面', b: 'B 仪器', c: 'C 夜航' };
  function openDir(k) {
    if (!M230_DIR[k]) return;
    title.textContent = names[k] || k;
    frame.srcdoc = M230_WITH(k, '');
    layer.hidden = false; document.documentElement.style.overflow = 'hidden';
  }
  function closeLayer() { layer.hidden = true; frame.srcdoc = ''; document.documentElement.style.overflow = ''; }
  document.addEventListener('click', function (e) {
    var a = e.target && e.target.closest ? e.target.closest('[data-bundle-nav]') : null;
    if (!a) return;
    e.preventDefault();
    var to = a.getAttribute('data-bundle-nav');
    if (to === 'index') { closeLayer(); window.scrollTo(0, 0); } else openDir(to);
  }, true);
  document.getElementById('m230-close').addEventListener('click', closeLayer);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !layer.hidden) closeLayer(); });
  window.addEventListener('message', function (e) {
    var m = e.data; if (!m || !m.m230) return;
    if (m.m230 === 'esc') { closeLayer(); return; }
    if (m.m230 !== 'nav') return;
    if (m.to === 'index') { closeLayer(); window.scrollTo(0, 0); } else openDir(m.to);
  });
  /* 同主题:外层切暗色时给层的工具条换色 */
  var mo = new MutationObserver(function () {
    var dark = document.documentElement.dataset.theme === 'dark';
    layer.style.background = dark ? '#111' : '#fff';
    layer.firstElementChild.style.background = dark ? '#111' : '#fff';
    layer.firstElementChild.style.color = dark ? '#eee' : '#111';
  });
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
})();
</script>
</body>"""
m = re.search(r'<body[^>]*>', idx); assert m
idx = idx[:m.end()] + '\n' + data_script + idx[m.end():]
assert idx.count('</body>') == 1
idx = idx.replace('</body>', layer, 1)
idx = idx.replace('<title>', '<title>[单文件] ', 1)
out.write_text(idx, encoding='utf-8')
print('wrote', out, out.stat().st_size, 'bytes')
