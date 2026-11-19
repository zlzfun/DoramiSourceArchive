"""样式表动效普查(issue #301 方案 §1 的数字出自这里;收口时再跑一遍对比)。只读。

    python3 docs/design/issue-301-motion-language/tools/census.py frontend/src/index.css [frontend/src/mobile.css …]

口径:
- 先去注释,再按花括号把样式表走一遍,拿到每条声明和它所在规则的选择器(多行声明、嵌套的 @layer / @variant / @media 都算)。
- 「写死的时长」= transition / animation 系声明的值里出现的时间字面量(0 不算),按毫秒归一;`var(--motion-*)` 不算写死。
- 「token 引用」= 全文 `var(--motion-xxx)` 的出现次数(一条声明里出现两次算两次);另给涉及的声明条数。
- 「家族」按选择器前缀粗分:手机壳(.m-*)、登录与品牌(auth / login / arrive / stage)、读者面(.reader / .bp- / .rk- / .rail / .qa- 等)、其余。
"""
import collections
import pathlib
import re
import sys

TIME = re.compile(r"(?<![\w.-])(\d*\.?\d+)(ms|s)\b")
MOTION_PROP = re.compile(r"^(transition|animation)(-duration|-delay)?$")
READER = re.compile(r"\.reader|\.bp-|\.rk-|\.brief|\.rail|\.vrail|\.qa-|\.ask|\.markdown|\.social|\.pod|\.player|\.disc|\.ev-|\.toast|\.ctx-|\.popover|\.tip")
AUTH = re.compile(r"auth|login|arrive|constellation|stage")
MOBILE = re.compile(r"\.m-|\.msheet")


def declarations(css):
    """产出 (选择器, 属性, 值)。选择器取最内层的非 @ 规则前言;@keyframes 里的帧不算规则。"""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    stack = []      # 每层:前言文本
    buf = ""
    i = 0
    n = len(css)
    while i < n:
        ch = css[i]
        if ch in "\"'":
            j = i + 1
            while j < n and css[j] != ch:
                j += 2 if css[j] == "\\" else 1
            buf += css[i:j + 1]
            i = j + 1
            continue
        if ch == "{":
            stack.append(buf.strip())
            buf = ""
        elif ch == "}":
            if buf.strip():
                yield from _emit(stack, buf)
            buf = ""
            if stack:
                stack.pop()
        elif ch == ";":
            yield from _emit(stack, buf)
            buf = ""
        else:
            buf += ch
        i += 1


def _emit(stack, text):
    if ":" not in text:
        return
    prop, _, value = text.partition(":")
    prop = prop.strip()
    if not re.fullmatch(r"-{0,2}[\w-]+", prop):
        return
    sel = next((s for s in reversed(stack) if s and not s.startswith("@")), "")
    in_keyframes = any(s.startswith("@keyframes") for s in stack)
    yield ("" if in_keyframes else " ".join(sel.split())), prop, " ".join(value.split())


def family(sel):
    if MOBILE.search(sel):
        return "手机壳"
    if AUTH.search(sel):
        return "登录与品牌"
    if READER.search(sel):
        return "读者面"
    return "其余"


def census(path):
    css = pathlib.Path(path).read_text(encoding="utf-8")
    bare = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    values = collections.Counter()
    by_family = collections.defaultdict(collections.Counter)
    token_refs = collections.Counter()
    token_decls = collections.Counter()
    beziers = collections.Counter()
    for sel, prop, value in declarations(css):
        for name in set(re.findall(r"var\((--motion-[a-z-]+)\)", value)):
            token_decls[name] += 1
        for name in re.findall(r"var\((--motion-[a-z-]+)\)", value):
            token_refs[name] += 1
        if prop.startswith("--"):
            continue
        for b in re.findall(r"cubic-bezier\([^)]*\)", value):
            beziers[re.sub(r"\s+", "", b)] += 1
        if not MOTION_PROP.match(prop):
            continue
        for num, unit in TIME.findall(value):
            ms = round(float(num) * (1000 if unit == "s" else 1))
            if ms == 0:
                continue
            values[ms] += 1
            by_family[family(sel)][ms] += 1
    print(f"== {path}")
    print(f"@keyframes: {len(re.findall(r'@keyframes\s', bare))}")
    print(f"写死的时长: {len(values)} 种取值、{sum(values.values())} 处")
    for fam in ("读者面", "登录与品牌", "手机壳", "其余"):
        c = by_family.get(fam, {})
        top = ", ".join(f"{k}ms × {v}" for k, v in sorted(c.items(), key=lambda kv: -kv[1])[:5])
        print(f"  {fam}: {len(c)} 种、{sum(c.values())} 处" + (f"(最多:{top})" if c else ""))
    print(f"写死的 cubic-bezier(非自定义属性里): {len(beziers)} 种、{sum(beziers.values())} 处")
    print("token 引用(出现次数 / 涉及声明条数):")
    for name, n in token_refs.most_common():
        print(f"  {name}: {n} / {token_decls[name]}")


for p in sys.argv[1:]:
    census(p)
