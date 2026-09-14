// 构建来源文案(tag 即发布波):runtime.build = {ref, sha, source}。
// ref 恰为 v{version} 即发布版;否则(describe 的 -N-gSHA / -dirty 后缀,或空)标注非发布版,
// 让「生产跑的是哪一版」在 设置 → 关于 可核对。
export function buildLabel(version, build) {
  const ref = build?.ref || '';
  const sha = build?.sha ? build.sha.slice(0, 7) : '';
  if (!ref && !sha) return '未知';
  const isRelease = !!version && ref === `v${version}`;
  const head = ref || sha;
  const tail = ref && sha ? ` · ${sha}` : '';
  return isRelease ? `发布版 ${head}${tail}` : `非发布版 ${head}${tail}`;
}
