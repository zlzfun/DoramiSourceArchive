#!/bin/bash
# 发版:在 main 上 bump 版本号 → 提交 → 打 annotated tag → 推送。tag 一推,
# GitHub Actions 自动建 Release(release.yml)并把版本节点同步到内网 master(sync-master.yml);
# 生产机随后 ./deploy-docker.sh 或 ./deploy.sh 即部署这一版。流程全文见 docs/release-process.md。
#
# 用法:scripts/release.sh <X.Y.Z> [-m "一句话说明"] [--no-push] [--yes]
#   X.Y.Z      新版本号(SemVer:MINOR=功能波 / PATCH=修复;必须大于最近的 tag)
#   -m         tag 说明的首行(缺省用「vX.Y.Z」);正文自动附上 <上一 tag>..HEAD 的提交清单
#   --no-push  只在本地提交与打 tag,不推送(想再看一眼时用;之后手动 git push origin main vX.Y.Z)
#   --yes      跳过确认
#
# 前置校验(任一不满足即退出,什么都不改):在 main 上、入库文件无未提交修改(uv.lock 除外,见下)、
# 与 origin/main 同步(不领先不落后)、版本号大于最近 tag、tag 本地与远端都不存在。
#
# 改哪些文件:src/version.py(单一事实来源)、pyproject.toml、uv.lock 的根包 version 行。
# uv.lock 只改「索引」不动工作区:开发机的 uv.lock 常带镜像源改写,永不入库(work-rhythm 惯例),
# 故从 HEAD 的 uv.lock 派生新内容直接写进索引(hash-object + update-index),工作区那份保持原样。
set -euo pipefail
cd "$(dirname "$0")/.."

fail() { echo "ERROR: $*" >&2; exit 1; }
usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; }

VERSION="" MESSAGE="" PUSH=1 YES=0
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        -m) shift; MESSAGE="${1:-}"; [ -n "$MESSAGE" ] || fail "-m 需要一段说明" ;;
        --no-push) PUSH=0 ;;
        --yes) YES=1 ;;
        --*) usage >&2; fail "未知参数: $1" ;;
        *) [ -z "$VERSION" ] || fail "只能指定一个版本号"; VERSION="${1#v}" ;;
    esac
    shift
done
[ -n "$VERSION" ] || { usage >&2; fail "缺少版本号"; }
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "版本号须为 X.Y.Z 形式: $VERSION"
TAG="v${VERSION}"

# ── 校验 ──
branch="$(git rev-parse --abbrev-ref HEAD)"
[ "$branch" = "main" ] || fail "发版必须在 main 上(当前 $branch)"

dirty="$(git status --porcelain --untracked-files=no | grep -v ' uv.lock$' || true)"
[ -z "$dirty" ] || { echo "$dirty" >&2; fail "有未提交的修改,先提交或 stash"; }

git fetch --quiet origin main --tags || fail "git fetch 失败"
local_sha="$(git rev-parse HEAD)"; remote_sha="$(git rev-parse origin/main)"
[ "$local_sha" = "$remote_sha" ] \
    || fail "本地 main($(git rev-parse --short HEAD))与 origin/main($(git rev-parse --short origin/main))不一致,先 pull 或 push"

git ls-remote --exit-code --tags origin "refs/tags/${TAG}" >/dev/null 2>&1 && fail "tag ${TAG} 远端已存在"
git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null && fail "tag ${TAG} 本地已存在"
prev_tag="$(git tag --list 'v*' --sort=-v:refname | head -1)"
if [ -n "$prev_tag" ]; then
    highest="$(printf '%s\n%s\n' "${prev_tag#v}" "$VERSION" | sort -V | tail -1)"
    [ "$highest" = "$VERSION" ] || fail "版本号必须大于最近的 tag ${prev_tag}"
fi

current="$(grep -o '__version__ = "[^"]*"' src/version.py | sed 's/.*"\(.*\)"/\1/')"
[ "$current" != "$VERSION" ] || fail "src/version.py 已经是 ${VERSION}(PR 里不该改版本号,发版时统一改)"

# ── 预览 ──
range="${prev_tag:+${prev_tag}..}HEAD"
changes="$(git log --no-merges --format='- %s' "$range")"
echo "发版 ${TAG}(上一版 ${prev_tag:-无};${current} → ${VERSION})"
echo "本版内容(${range}):"
echo "${changes:-  (无新提交)}"
echo
if [ "$YES" != "1" ]; then
    read -r -p "确认提交、打 tag$( [ "$PUSH" = 1 ] && echo '并推送' )?[y/N] " ans
    case "$ans" in y|Y|yes) ;; *) echo "已取消,未做任何修改。"; exit 0 ;; esac
fi

# ── 改版本号 ──
python3 - "$VERSION" <<'PYEOF'
import re, sys
v = sys.argv[1]
for path, pat, rep in [
    ("src/version.py", r'^__version__ = "[^"]*"', f'__version__ = "{v}"'),
    ("pyproject.toml", r'^version = "[^"]*"', f'version = "{v}"'),
]:
    s = open(path, encoding="utf-8").read()
    s2, n = re.subn(pat, rep, s, count=1, flags=re.M)
    assert n == 1, f"{path}: 没找到版本行"
    open(path, "w", encoding="utf-8").write(s2)
PYEOF
git add src/version.py pyproject.toml

# uv.lock:从 HEAD 那份派生(只换根包 version 行)直接写进索引,工作区不动
if git cat-file -e HEAD:uv.lock 2>/dev/null; then
    new_blob="$(git show HEAD:uv.lock | python3 -c '
import re, sys
v = sys.argv[1]; s = sys.stdin.read()
s2, n = re.subn(r"(name = \"doramisourcearchive\"\nversion = )\"[^\"]*\"", lambda m: m.group(1) + "\"" + v + "\"", s, count=1)
assert n == 1, "uv.lock: 没找到根包 version 行"
sys.stdout.write(s2)' "$VERSION" | git hash-object -w --stdin)"
    git update-index --cacheinfo "100644,${new_blob},uv.lock"
fi

# ── 提交 + tag ──
git commit --quiet -m "release: ${TAG}"
title="${MESSAGE:-${TAG}}"
git tag -a "$TAG" -m "$title" -m "$(printf '本版内容(%s):\n%s' "${range}" "${changes:-(无新提交)}")"
echo "已提交 $(git rev-parse --short HEAD) 并打 tag ${TAG}"

if [ "$PUSH" = 1 ]; then
    git push --quiet origin main "$TAG"
    echo "已推送。GitHub Actions 会自动建 Release 并同步 master;生产机执行 ./deploy-docker.sh(或 ./deploy.sh)部署 ${TAG}。"
else
    echo "未推送(--no-push)。确认后执行:git push origin main ${TAG}"
fi
