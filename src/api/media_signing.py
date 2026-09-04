"""签名公开图链(Issue #17 小程序端):`GET /api/public/media?u=&exp=&sig=` 的签发与校验。

为什么需要它:小程序 `rich-text` 内 `<img>` 由微信客户端发请求,**不带自定义头也不带
Cookie**,读者门控内的 `/api/media/proxy` 不可达;直连原图又撞防盗链 CDN(v3.11 图床
立项原因)。签名链让 `/api/public/*` 的免登录豁免只对「服务端渲染正文时签发过的那些
URL」成立——客户端拿不到密钥,伸不成开放代理。

签名 = HMAC-SHA256(AUTH_SECRET, f"{u}|{exp}") 前 32 位 hex;校验失败/过期/缺参一律 404
(与分享链接同口径:不区分原因,区分等于把签名是否有效告诉猜签名的人)。
密钥沿 api.tokens.AUTH_SECRET(会话 token 与订阅令牌的同一把),不引新配置。
"""

import hashlib
import hmac
import time
from typing import Optional
from urllib.parse import urlencode

from api.tokens import AUTH_SECRET

# 有效期随会话档(7 天):签名链嵌在渲染结果里,和读者一次会话同寿即可。
DEFAULT_TTL_SECONDS = 7 * 24 * 3600
PUBLIC_MEDIA_PATH = "/api/public/media"
_SIG_LEN = 32


def _digest(url: str, exp: int) -> str:
    message = f"{url}|{int(exp)}".encode("utf-8")
    return hmac.new(AUTH_SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()[:_SIG_LEN]


def sign_media_url(url: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS, now: Optional[float] = None) -> str:
    """把一条原始 http(s) 图链签成公开代理路径(相对路径,客户端拼接站点 origin)。

    非 http(s) 输入原样返回空串——调用方(渲染层)据此丢弃该图。
    """
    target = (url or "").strip()
    if not target.lower().startswith(("http://", "https://")):
        return ""
    exp = int((now if now is not None else time.time()) + ttl_seconds)
    query = urlencode({"u": target, "exp": exp, "sig": _digest(target, exp)})
    return f"{PUBLIC_MEDIA_PATH}?{query}"


def verify_media_signature(url: str, exp: str | int | None, sig: str | None, *, now: Optional[float] = None) -> bool:
    """校验签名与有效期。任何缺参/格式错/过期/签名不符都返回 False。"""
    target = (url or "").strip()
    if not target.lower().startswith(("http://", "https://")):
        return False
    try:
        exp_int = int(exp)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    current = now if now is not None else time.time()
    if exp_int < int(current):
        return False
    provided = (sig or "").strip().lower()
    if len(provided) != _SIG_LEN:
        return False
    return hmac.compare_digest(provided, _digest(target, exp_int))
