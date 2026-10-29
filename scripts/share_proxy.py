"""Password-protected loopback gateway for a temporary, single-owner demo.

The tunnel must point to this process, never directly to the application port.
The private JSON config is re-read on every request so public_origin can be set
after a tunnel URL is known. No credentials are logged or sent to the main app.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

import httpx
from starlette.applications import Starlette
from starlette.requests import ClientDisconnect, Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.routing import Route

ROOT = Path(__file__).resolve().parents[1]
MAX_BODY_BYTES = 11 * 1024 * 1024
LOGIN_MAX_BYTES = 16 * 1024
LOGIN_PATH = '/__share__/login'
COOKIE_NAME = '__Host-campus-demo'
COOKIE_MAX_AGE = 12 * 60 * 60
SECURITY_HEADERS = {'Cache-Control': 'no-store', 'X-Frame-Options': 'DENY',
                    'Content-Security-Policy': "frame-ancestors 'none'"}
HOP_HEADERS = {
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
    'proxy-connection', 'te', 'trailer', 'transfer-encoding', 'upgrade',
}
UNSAFE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}


@dataclass(frozen=True)
class ShareConfig:
    username: str
    password: str = field(repr=False)
    upstream: str = 'http://127.0.0.1:8765'
    public_origin: str = ''
    cookie_auth: bool = False


def normalized_origin(value: str) -> str:
    """Accept a single origin, never URLs containing userinfo, paths or queries."""
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('Invalid origin')
    port = parsed.port
    host = parsed.hostname.lower()
    if ':' in host:
        host = '[' + host + ']'
    if port is not None and (parsed.scheme, port) not in (('http', 80), ('https', 443)):
        host += ':' + str(port)
    return parsed.scheme.lower() + '://' + host


def load_config(path: Path) -> ShareConfig:
    try:
        if path.stat().st_size > 16 * 1024:
            raise ValueError('Oversized config')
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        username, password = value['username'], value['password']
        if (not isinstance(username, str) or not 1 <= len(username) <= 80
                or ':' in username or not isinstance(password, str)
                or not 20 <= len(password) <= 256):
            raise ValueError('Invalid credentials')
        upstream = normalized_origin(value.get('upstream', 'http://127.0.0.1:8765'))
        parsed = urlsplit(upstream)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.port is None:
            raise ValueError('Upstream must be an explicit loopback HTTP port')
        public_origin = value.get('public_origin', '')
        if not isinstance(public_origin, str):
            raise ValueError('Invalid public origin')
        public_origin = normalized_origin(public_origin) if public_origin else ''
        if public_origin and urlsplit(public_origin).scheme != 'https':
            raise ValueError('Public origin must use HTTPS')
        cookie_auth = value.get('cookie_auth', False)
        if not isinstance(cookie_auth, bool):
            raise ValueError('Invalid authentication mode')
        return ShareConfig(username, password, upstream, public_origin, cookie_auth)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        # Never propagate malformed credential content into logs or responses.
        raise ValueError('Shared access configuration is missing or invalid') from None


def authorized(headers, config: ShareConfig) -> bool:
    values = headers.getlist('authorization')
    if len(values) != 1:
        return False
    scheme, _, token = values[0].partition(' ')
    if scheme.casefold() != 'basic' or not token:
        return False
    try:
        decoded = base64.b64decode(token, validate=True).decode('utf-8')
        username, sep, password = decoded.partition(':')
    except (binascii.Error, UnicodeError, ValueError):
        return False
    return bool(sep) and credentials_match(username, password, config)


def credentials_match(username: str, password: str, config: ShareConfig) -> bool:
    return bool(
        secrets.compare_digest(username.encode(), config.username.encode())
        & secrets.compare_digest(password.encode(), config.password.encode()))


def issue_session_cookie(config: ShareConfig, now=None) -> str:
    now = int(time.time()) if now is None else int(now)
    payload = {'u': config.username, 'o': config.public_origin, 'i': now,
               'e': now + COOKIE_MAX_AGE, 'n': secrets.token_urlsafe(18)}
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':'), ensure_ascii=True).encode()).rstrip(b'=')
    signature = hmac.new(config.password.encode(), b'campus-demo-cookie-v1:' + encoded, hashlib.sha256).hexdigest()
    return encoded.decode('ascii') + '.' + signature


def valid_session_cookie(token: str, config: ShareConfig, now=None) -> bool:
    if not token or len(token) > 4096:
        return False
    encoded, separator, signature = token.partition('.')
    if not separator or not re.fullmatch(r'[A-Za-z0-9_-]+', encoded) or not re.fullmatch(r'[0-9a-f]{64}', signature):
        return False
    expected = hmac.new(config.password.encode(), b'campus-demo-cookie-v1:' + encoded.encode('ascii'), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(signature, expected):
        return False
    try:
        raw = base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True)
        payload = json.loads(raw)
        now = int(time.time()) if now is None else int(now)
        return (payload['u'] == config.username and payload['o'] == config.public_origin
                and type(payload['i']) is int and type(payload['e']) is int
                and payload['i'] <= now < payload['e']
                and 0 < payload['e'] - payload['i'] <= COOKIE_MAX_AGE
                and isinstance(payload['n'], str) and 16 <= len(payload['n']) <= 64)
    except (ValueError, TypeError, KeyError, binascii.Error):
        return False


def login_page(config: ShareConfig, message='请输入共享访问账号和密码。'):
    """No Basic challenge: browsers submit the form only to canonical HTTPS."""
    nonce = secrets.token_urlsafe(18)
    target = config.public_origin + LOGIN_PATH
    js_target = json.dumps(target, ensure_ascii=True).replace('<', '\\u003c').replace('>', '\\u003e')
    html = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>知校 · 共享访问登录</title>
<style>body{font:16px/1.6 system-ui,sans-serif;background:#f3f6fa;color:#19314d;margin:0;padding:8vh 20px}
main{max-width:360px;margin:auto;background:white;padding:32px;border-radius:14px;box-shadow:0 12px 40px #17324c10}
h1{font-size:25px;margin:0 0 8px}p{color:#526579}label{display:block;margin-top:18px}
input,button{box-sizing:border-box;width:100%;padding:12px;border:1px solid #ccd8e3;border-radius:7px;font:inherit}
button{margin-top:24px;background:#176d91;color:white;border:0;cursor:pointer}</style>
<script nonce="''' + nonce + '''">if(window.location.protocol!=="https:"){window.location.replace(''' + js_target + ''');}</script>
</head><body><main><h1>知校 · 共享访问</h1><p>''' + escape(message) + '''</p>
<form method="post" action="''' + escape(target, quote=True) + '''">
<label>访问账号<input name="username" autocomplete="username" maxlength="80" required></label>
<label>访问密码<input name="password" type="password" autocomplete="current-password" maxlength="256" required></label>
<button type="submit">登录并打开项目</button></form></main></body></html>'''
    headers = dict(SECURITY_HEADERS)
    headers['Content-Security-Policy'] = ("default-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
        "style-src 'unsafe-inline'; script-src 'nonce-" + nonce + "'; form-action " + config.public_origin)
    return HTMLResponse(html, status_code=401, headers=headers)


def filtered_headers(raw_headers, extra_blocked=()):
    connection_tokens = set()
    for name, value in raw_headers:
        if name.lower() == b'connection':
            connection_tokens.update(part.strip().lower() for part in value.decode('latin-1').split(','))
    blocked = HOP_HEADERS | connection_tokens | set(extra_blocked)
    return [(name.lower(), value) for name, value in raw_headers if name.decode('latin-1').lower() not in blocked]


def error(message: str, status: int, headers=None):
    return JSONResponse({'detail': message}, status_code=status,
                        headers={**SECURITY_HEADERS, **(headers or {})})


class ProxyResponse(StreamingResponse):
    """Release upstream resources and the slot even on browser disconnect."""
    def __init__(self, upstream_response, limiter):
        self.upstream_response = upstream_response
        self.limiter = limiter
        super().__init__(upstream_response.aiter_raw(), status_code=upstream_response.status_code)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                await self.upstream_response.aclose()
            finally:
                self.limiter.release()


def create_share_proxy(config_path: Path, port=8767, client=None, max_concurrent=4):
    if not 1 <= port <= 65535 or not 1 <= max_concurrent <= 16:
        raise ValueError('Invalid gateway port or concurrency')
    config_path = Path(config_path)
    local_origin = 'http://127.0.0.1:' + str(port)
    owned_client = client is None
    client = client or httpx.AsyncClient(
        trust_env=False, follow_redirects=False,
        timeout=httpx.Timeout(connect=10, read=None, write=60, pool=5),
        limits=httpx.Limits(max_connections=max_concurrent, max_keepalive_connections=max_concurrent),
    )
    limiter = asyncio.Semaphore(max_concurrent)

    @asynccontextmanager
    async def lifespan(app):
        yield
        if owned_client:
            await client.aclose()

    async def proxy(request: Request):
        try:
            config = load_config(config_path)
        except ValueError:
            return error('共享访问尚未配置或配置无效。', 503)
        # Allowed hosts come from explicit local/public configuration. Forwarded
        # headers cannot choose an origin, upstream host, or destination port.
        allowed_origins = {local_origin}
        if config.public_origin:
            allowed_origins.add(config.public_origin)
        allowed_hosts = {urlsplit(origin).netloc for origin in allowed_origins}
        hosts = request.headers.getlist('host')
        if len(hosts) != 1 or hosts[0].lower() not in allowed_hosts:
            return error('此访问地址尚未配置。', 421)
        public_request = bool(config.public_origin and hosts[0].lower() == urlsplit(config.public_origin).netloc)
        if public_request and config.cookie_auth:
            # Browser Secure cookies enforce TLS without trusting an external
            # tunnel's sometimes incorrect forwarded-protocol metadata.
            session_valid = valid_session_cookie(request.cookies.get(COOKIE_NAME, ''), config)
            if request.url.path == LOGIN_PATH:
                if request.method in ('GET', 'HEAD'):
                    return RedirectResponse('/', status_code=303, headers=SECURITY_HEADERS) if session_valid else login_page(config)
                if request.method != 'POST':
                    return error('登录请求方法无效。', 405)
                origins = request.headers.getlist('origin')
                if (len(origins) != 1 or origins[0] != config.public_origin
                        or request.headers.get('sec-fetch-site', '').lower() == 'cross-site'):
                    return error('不允许跨站登录。', 403)
                if request.headers.get('content-type', '').partition(';')[0].strip().lower() != 'application/x-www-form-urlencoded':
                    return error('登录表单格式无效。', 415)
                lengths = request.headers.getlist('content-length')
                if lengths:
                    try:
                        if len(lengths) != 1 or int(lengths[0]) < 0:
                            raise ValueError
                        if int(lengths[0]) > LOGIN_MAX_BYTES:
                            return error('登录表单过大。', 413)
                    except ValueError:
                        return error('请求大小无效。', 400)
                form_body = bytearray()
                try:
                    async for chunk in request.stream():
                        if len(form_body) + len(chunk) > LOGIN_MAX_BYTES:
                            return error('登录表单过大。', 413)
                        form_body.extend(chunk)
                    form = parse_qs(form_body.decode('utf-8'), keep_blank_values=True,
                                    strict_parsing=True, max_num_fields=4)
                    if len(form.get('username', [])) != 1 or len(form.get('password', [])) != 1:
                        return login_page(config, '账号或密码不正确。')
                    if not credentials_match(form['username'][0], form['password'][0], config):
                        return login_page(config, '账号或密码不正确。')
                except (ValueError, UnicodeError):
                    return error('登录表单格式无效。', 400)
                except ClientDisconnect:
                    return error('客户端已断开。', 499)
                response = RedirectResponse('/', status_code=303, headers=SECURITY_HEADERS)
                response.set_cookie(COOKIE_NAME, issue_session_cookie(config), max_age=COOKIE_MAX_AGE,
                                    secure=True, httponly=True, samesite='strict', path='/')
                return response
            if not session_valid:
                return login_page(config) if request.method in ('GET', 'HEAD') else error('请先通过HTTPS页面登录。', 401)
        elif public_request:
            # The trusted loopback tunnel supplies this header. Do not challenge
            # for Basic credentials on the tunnel's plaintext HTTP address.
            schemes = request.headers.getlist('x-forwarded-proto')
            if len(schemes) != 1 or schemes[0].lower() != 'https':
                target = config.public_origin + request.url.path
                if request.url.query:
                    target += '?' + request.url.query
                return RedirectResponse(target, status_code=308, headers=SECURITY_HEADERS)
        if not (public_request and config.cookie_auth) and not authorized(request.headers, config):
            return error('请输入此演示服务的访问账号和密码。', 401,
                         {'WWW-Authenticate': 'Basic realm="Campus RAG private demo", charset="UTF-8"'})
        if request.method in UNSAFE_METHODS:
            origins = request.headers.getlist('origin')
            if request.headers.get('sec-fetch-site', '').lower() == 'cross-site':
                return error('不允许跨站提交。', 403)
            if origins:
                if len(origins) != 1:
                    return error('不允许跨站提交。', 403)
                try:
                    incoming_origin = normalized_origin(origins[0])
                except ValueError:
                    return error('不允许跨站提交。', 403)
                # Requiring the corresponding Host avoids accepting a public
                # Origin against the local endpoint or a spoofed third host.
                if incoming_origin not in allowed_origins or urlsplit(incoming_origin).netloc != hosts[0].lower():
                    return error('不允许跨站提交。', 403)
        lengths = request.headers.getlist('content-length')
        if lengths:
            try:
                length = int(lengths[0])
                if len(lengths) != 1 or length < 0:
                    raise ValueError
            except ValueError:
                return error('请求大小无效。', 400)
            if length > MAX_BODY_BYTES:
                return error('请求超过共享服务的大小限制。', 413)
        # Never create an unbounded queue of uploads or model calls.
        if limiter.locked():
            return error('演示服务繁忙，请稍后重试。', 429, {'Retry-After': '2'})
        await limiter.acquire()
        try:
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_BODY_BYTES:
                    limiter.release()
                    return error('请求超过共享服务的大小限制。', 413)
                body.extend(chunk)
            raw_path = request.scope.get('raw_path', request.url.path.encode('utf-8'))
            query = request.scope.get('query_string', b'')
            if query:
                raw_path += b'?' + query
            target = httpx.URL(config.upstream).copy_with(raw_path=raw_path)
            headers = filtered_headers(request.headers.raw, {'authorization', 'cookie', 'host', 'content-length', 'forwarded',
                                       'x-forwarded-host', 'x-forwarded-proto', 'x-forwarded-for'})
            upstream_request = client.build_request(request.method, target, headers=headers, content=bytes(body))
            upstream_response = await client.send(upstream_request, stream=True)
            response = ProxyResponse(upstream_response, limiter)
            raw_headers = filtered_headers(upstream_response.headers.raw, {'cache-control', 'x-frame-options', 'content-security-policy'})
            # Main-app redirects must stay on the address the visitor opened.
            rewritten = []
            for name, value in raw_headers:
                if name == b'location':
                    location = value.decode('latin-1')
                    parsed = urlsplit(location)
                    if parsed.scheme + '://' + parsed.netloc == config.upstream:
                        value = urlunsplit(('', '', parsed.path or '/', parsed.query, parsed.fragment)).encode('latin-1')
                rewritten.append((name, value))
            response.raw_headers = rewritten + [(name.lower().encode(), value.encode()) for name, value in SECURITY_HEADERS.items()]
            return response
        except ClientDisconnect:
            limiter.release()
            return error('客户端已断开。', 499)
        except httpx.HTTPError:
            limiter.release()
            return error('本机毕设服务暂时不可用，请稍后重试。', 502)
        except BaseException:
            limiter.release()
            raise

    app = Starlette(routes=[Route('/{path:path}', proxy, methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])],
                    lifespan=lifespan)
    app.state.limiter = limiter
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT/'data/share-access.json')
    parser.add_argument('--port', type=int, default=8767)
    args = parser.parse_args()
    load_config(args.config)  # Fail closed before accepting any connections.
    import uvicorn
    uvicorn.run(create_share_proxy(args.config, args.port), host='127.0.0.1', port=args.port,
                log_level='info', server_header=False)
