// 哆啦美出网跳板机 —— njs 解码与反代目标解析。
//
// 请求形态: GET /relay?t=<base64url(原始URL)>[&h=目标域名(仅审计)]
// 由 nginx 的 js_set 调用: $relay_url / $relay_host / $relay_block。
// 无副作用、幂等,可被 nginx 多次求值。

// base64url → 明文。用 atob(兼容性最广;URL 基本为 ASCII,Latin-1 等价原文)。
function decode(t) {
    if (!t) return null;
    var b = String(t).replace(/-/g, '+').replace(/_/g, '/');
    while (b.length % 4) b += '=';
    try {
        return atob(b);
    } catch (e) {
        return null;
    }
}

function parse(r) {
    var url = decode(r.args.t);
    if (!url) return null;
    // scheme://host[:port] 后接 path?query(或空)
    var m = url.match(/^(https?):\/\/([^\/?#]+)(\/[^\s]*|)$/);
    if (!m) return null;
    var uri = m[3] || '/';
    return { scheme: m[1], host: m[2], uri: uri, url: m[1] + '://' + m[2] + uri };
}

// 拦私网/环回/链路本地(含云元数据 169.254.169.254)——本机是公网 ECS 且无鉴权,
// 不拦则可被诱导访问跳板机自身内网/元数据服务(SSRF 横移)。域名交给 DNS,这里
// 只挡 IP 字面量。
function isPrivate(host) {
    var h = host.split(':')[0].toLowerCase();
    if (h === 'localhost') return true;
    if (/^\d+\.\d+\.\d+\.\d+$/.test(h)) {
        if (/^127\./.test(h)) return true;
        if (/^10\./.test(h)) return true;
        if (/^192\.168\./.test(h)) return true;
        if (/^169\.254\./.test(h)) return true;
        if (/^172\.(1[6-9]|2[0-9]|3[01])\./.test(h)) return true;
        if (/^0\./.test(h)) return true;
    }
    if (h === '::1') return true;
    if (h.indexOf('fe80:') === 0 || h.indexOf('fc') === 0 || h.indexOf('fd') === 0) return true;
    return false;
}

function url(r) {
    var p = parse(r);
    return p ? p.url : '';
}

function host(r) {
    var p = parse(r);
    return p ? p.host : '';
}

// 返回 '1' 表示应拒绝(解码失败 / 非法 URL / 私网目标),否则空串。
function blocked(r) {
    var p = parse(r);
    if (!p) return '1';
    return isPrivate(p.host) ? '1' : '';
}

export default { url, host, blocked };
