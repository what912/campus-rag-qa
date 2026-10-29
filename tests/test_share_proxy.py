"""Pure ASGI checks: no listening sockets, real upstreams or model calls."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

import httpx

from scripts.share_proxy import COOKIE_NAME, LOGIN_PATH, MAX_BODY_BYTES, create_share_proxy, issue_session_cookie, load_config


def run_immediate(coroutine):
    """These in-memory transports require no IO/event-loop socket on Windows."""
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    finally:
        coroutine.close()
    raise AssertionError('An in-memory ASGI operation unexpectedly requested IO')


class Chunks(httpx.AsyncByteStream):
    def __init__(self, parts):
        self.parts = parts
        self.closed = False

    async def __aiter__(self):
        for part in self.parts:
            yield part

    async def aclose(self):
        self.closed = True


class ShareProxyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temporary.name)/'access.json'
        self.config = {'username': 'demo', 'password': 'a-test-only-password-of-sufficient-length',
                       'upstream': 'http://127.0.0.1:8765', 'public_origin': 'https://example.trycloudflare.com'}
        self.write_config()
        self.seen = []
        self.response_parts = [b'data: one\n\n', b'data: two\n\n']
        self.response_headers = {'content-type': 'text/event-stream', 'connection': 'keep-alive, x-hop', 'x-hop': 'remove-me'}
        self.response_status = 200
        self.streams = []

        async def upstream(request):
            self.seen.append(request)
            stream = Chunks(self.response_parts)
            self.streams.append(stream)
            return httpx.Response(self.response_status, headers=self.response_headers, stream=stream)

        self.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream), trust_env=False)
        self.app = create_share_proxy(self.config_path, client=self.client)

    def tearDown(self):
        run_immediate(self.client.aclose())
        self.temporary.cleanup()

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config), encoding='utf-8')

    def request(self, path='/', method='GET', body=b'', headers=None, auth=True, chunks=None):
        request_headers = {'host': 'example.trycloudflare.com', 'x-forwarded-proto': 'https'}
        if auth:
            value = (self.config['username']+':'+self.config['password']).encode()
            request_headers['authorization'] = 'Basic ' + base64.b64encode(value).decode()
        request_headers.update(headers or {})
        parts = list(chunks) if chunks is not None else [body]
        messages = []

        async def receive():
            if parts:
                part = parts.pop(0)
                return {'type': 'http.request', 'body': part, 'more_body': bool(parts)}
            return {'type': 'http.disconnect'}

        async def send(message):
            messages.append(message)

        path_only, _, query = path.partition('?')
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'}, 'http_version': '1.1',
                 'method': method, 'scheme': 'http', 'path': path_only, 'raw_path': path_only.encode(),
                 'query_string': query.encode(), 'root_path': '', 'headers': [(k.encode(), v.encode()) for k,v in request_headers.items()],
                 'client': ('127.0.0.1', 1234), 'server': ('127.0.0.1', 8767)}
        run_immediate(self.app(scope, receive, send))
        start = next(m for m in messages if m['type'] == 'http.response.start')
        content = b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')
        return start['status'], dict(start['headers']), content, messages

    def test_every_frontend_and_sensitive_endpoint_requires_authentication(self):
        for path, method in [('/', 'GET'), ('/static/app.js', 'GET'), ('/docs', 'GET'),
                             ('/api/documents/x/download', 'GET'), ('/api/sessions', 'GET'),
                             ('/api/settings', 'PUT'), ('/api/ask/stream', 'POST')]:
            status, headers, *_ = self.request(path, method, auth=False)
            self.assertEqual(status, 401)
            self.assertIn(b'Basic', headers[b'www-authenticate'])
        self.assertEqual(self.seen, [])

    def test_missing_or_changed_invalid_config_fails_closed(self):
        self.config_path.unlink()
        self.assertEqual(self.request()[0], 503)
        self.config_path.write_text('{bad json', encoding='utf-8')
        self.assertEqual(self.request()[0], 503)
        self.assertEqual(self.seen, [])

    def test_cross_site_writes_and_unconfigured_host_are_rejected(self):
        for headers in [{'origin': 'https://evil.example'}, {'origin': 'null'},
                        {'sec-fetch-site': 'cross-site'},
                        {'origin': 'http://127.0.0.1:8767'}]:
            self.assertEqual(self.request('/api/settings', 'PUT', headers=headers)[0], 403)
        self.assertEqual(self.request(headers={'host': 'evil.example'})[0], 421)
        self.assertEqual(self.seen, [])

    def test_public_http_redirects_before_any_password_challenge(self):
        status, headers, *_ = self.request('/docs?x=1', auth=False, headers={'x-forwarded-proto': 'http'})
        self.assertEqual(status, 308)
        self.assertEqual(headers[b'location'], b'https://example.trycloudflare.com/docs?x=1')
        self.assertNotIn(b'www-authenticate', headers)
        self.assertEqual(self.seen, [])
        self.assertEqual(self.request(auth=False, headers={'host': '127.0.0.1:8767', 'x-forwarded-proto': 'http'})[0], 401)

    def test_origin_is_reloaded_after_tunnel_url_changes_and_https_required(self):
        self.config['public_origin'] = ''
        self.write_config()
        self.assertEqual(self.request('/api/ask', 'POST')[0], 421)
        self.config['public_origin'] = 'https://new.example'
        self.write_config()
        self.assertEqual(self.request('/api/ask', 'POST', headers={'host': 'new.example', 'origin': 'https://new.example'})[0], 200)
        self.config['public_origin'] = 'http://new.example'
        self.write_config()
        with self.assertRaises(ValueError):
            load_config(self.config_path)

    def test_authenticated_json_post_and_query_forwarded_without_shared_auth_or_proxy_headers(self):
        body = b'{"answer_mode":"extractive"}'
        self.assertEqual(self.request('/api/ask?x=1', 'POST', body,
                         headers={'origin': self.config['public_origin'], 'content-type': 'application/json',
                                  'x-forwarded-host': 'evil.example', 'x-forwarded-for': '8.8.8.8',
                                  'connection': 'keep-alive, x-hop', 'x-hop': 'removed'})[0], 200)
        sent = self.seen[-1]
        self.assertEqual(str(sent.url), 'http://127.0.0.1:8765/api/ask?x=1')
        self.assertEqual(sent.content, body)
        for header in ['authorization', 'x-forwarded-host', 'x-forwarded-for', 'x-forwarded-proto', 'x-hop']:
            self.assertNotIn(header, sent.headers)
        self.assertNotEqual(sent.headers.get('connection'), 'keep-alive, x-hop')

    def test_sse_is_not_buffered_and_upstream_resources_close(self):
        status, headers, body, messages = self.request('/api/ask/stream', 'POST')
        self.assertEqual(status, 200)
        self.assertEqual(headers[b'content-type'], b'text/event-stream')
        self.assertNotIn(b'x-hop', headers)
        chunks = [m['body'] for m in messages if m['type'] == 'http.response.body' and m.get('body')]
        self.assertEqual(chunks, self.response_parts)
        self.assertTrue(self.streams[-1].closed)
        self.assertEqual(self.app.state.limiter._value, 4)

    def test_multipart_upload_and_pdf_bytes_remain_unchanged(self):
        body = b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="x.pdf"\r\n\r\n%PDF-1.7\x00\xff\r\n--boundary--\r\n'
        self.response_parts = [b'%PDF-1.7\x00\xff']
        self.response_headers = {'content-type': 'application/pdf', 'content-disposition': 'attachment; filename="x.pdf"'}
        status, headers, received, _ = self.request('/api/documents', 'POST', body,
                                                   headers={'content-type': 'multipart/form-data; boundary=boundary'})
        self.assertEqual(status, 200)
        self.assertEqual(self.seen[-1].content, body)
        self.assertEqual(received, self.response_parts[0])
        self.assertIn(b'attachment', headers[b'content-disposition'])

    def test_large_requests_and_busy_gateway_do_not_reach_upstream(self):
        self.assertEqual(self.request('/api/documents', 'POST', headers={'content-length': str(MAX_BODY_BYTES+1)})[0], 413)
        self.assertEqual(self.request('/api/documents', 'POST', chunks=[b'x'*MAX_BODY_BYTES, b'x'])[0], 413)
        self.assertEqual(self.app.state.limiter._value, 4)
        self.app.state.limiter._value = 0
        self.assertEqual(self.request('/api/ask', 'POST')[0], 429)
        self.assertEqual(self.seen, [])

    def test_upstream_redirect_becomes_relative_to_preserve_public_link(self):
        self.response_status = 307
        self.response_headers = {'location': 'http://127.0.0.1:8765/docs?x=1'}
        status, headers, *_ = self.request('/docs/')
        self.assertEqual(status, 307)
        self.assertEqual(headers[b'location'], b'/docs?x=1')

    def test_config_cannot_select_external_upstream(self):
        self.config['upstream'] = 'https://example.com'
        self.write_config()
        self.assertEqual(self.request()[0], 503)
        self.assertEqual(self.seen, [])

    def enable_cookie_auth(self):
        self.config['cookie_auth'] = True
        self.write_config()

    def login(self, password=None, headers=None):
        body = urlencode({'username': self.config['username'], 'password': password or self.config['password']}).encode()
        return self.request(LOGIN_PATH, 'POST', body, auth=False,
                            headers={'origin': self.config['public_origin'], 'content-type': 'application/x-www-form-urlencoded', **(headers or {})})

    def test_cookie_login_sets_host_only_secure_cookie_and_preserves_app_access(self):
        self.enable_cookie_auth()
        status, headers, *_ = self.login()
        self.assertEqual(status, 303)
        self.assertEqual(headers[b'location'], b'/')
        cookie = headers[b'set-cookie'].decode()
        for flag in ['Secure', 'HttpOnly', 'SameSite=strict', 'Path=/', 'Max-Age=43200']:
            self.assertIn(flag, cookie)
        self.assertNotIn('Domain=', cookie)
        session = cookie.split(';')[0]
        status, headers, *_ = self.request('/api/status', auth=False, headers={'cookie': session})
        self.assertEqual(status, 200)
        self.assertEqual(headers[b'x-frame-options'], b'DENY')
        self.assertNotIn('cookie', self.seen[-1].headers)

    def test_cookie_wrong_login_does_not_issue_token_or_basic_challenge(self):
        self.enable_cookie_auth()
        status, headers, content, _ = self.login(password='incorrect')
        self.assertEqual(status, 401)
        self.assertNotIn(b'set-cookie', headers)
        self.assertNotIn(b'www-authenticate', headers)
        self.assertNotIn(self.config['password'].encode(), content)
        self.assertEqual(self.seen, [])

    def test_cookie_expired_tampered_and_password_rotated_sessions_fail_closed(self):
        self.enable_cookie_auth()
        config = load_config(self.config_path)
        valid = issue_session_cookie(config)
        invalid = valid[:-1] + ('0' if valid[-1] != '0' else '1')
        for token in [invalid, issue_session_cookie(config, now=0), 'fake-token']:
            self.assertEqual(self.request('/api/status', auth=False, headers={'cookie': COOKIE_NAME+'='+token})[0], 401)
        self.config['password'] += '-rotated'
        self.write_config()
        self.assertEqual(self.request('/api/status', auth=False, headers={'cookie': COOKIE_NAME+'='+valid})[0], 401)
        self.assertEqual(self.seen, [])

    def test_cookie_mode_http_never_challenges_basic_and_form_posts_absolute_https(self):
        self.enable_cookie_auth()
        for proto in ['http', 'https']:
            status, headers, body, _ = self.request(auth=False, headers={'x-forwarded-proto': proto})
            self.assertEqual(status, 401)
            self.assertNotIn(b'www-authenticate', headers)
            self.assertIn(('action="'+self.config['public_origin']+LOGIN_PATH+'"').encode(), body)
            self.assertIn(b'window.location.protocol', body)
        # Even correct Basic credentials are not accepted for public-cookie mode.
        self.assertEqual(self.request('/api/status', auth=True)[0], 401)
        self.assertEqual(self.request('/api/status', headers={'host': '127.0.0.1:8767'})[0], 200)

    def test_cookie_login_requires_matching_origin_and_rejects_cross_site_writes(self):
        self.enable_cookie_auth()
        for headers in [{'origin': ''}, {'origin': 'https://evil.example'}, {'sec-fetch-site': 'cross-site'}]:
            self.assertEqual(self.login(headers=headers)[0], 403)
        token = issue_session_cookie(load_config(self.config_path))
        self.assertEqual(self.request('/api/settings', 'PUT', auth=False,
                         headers={'cookie': COOKIE_NAME+'='+token, 'origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.seen, [])

    def test_cookie_login_rejects_oversized_body_without_reaching_main_app(self):
        self.enable_cookie_auth()
        status, *_ = self.request(LOGIN_PATH, 'POST', b'x'*16385, auth=False,
                                  headers={'origin': self.config['public_origin'],
                                           'content-type': 'application/x-www-form-urlencoded'})
        self.assertEqual(status, 413)
        self.assertEqual(self.seen, [])

    def test_cookie_is_bound_to_configured_public_origin(self):
        self.enable_cookie_auth()
        token = issue_session_cookie(load_config(self.config_path))
        self.config['public_origin'] = 'https://new.example'
        self.write_config()
        self.assertEqual(self.request('/api/status', auth=False,
                         headers={'host': 'new.example', 'cookie': COOKIE_NAME+'='+token})[0], 401)


if __name__ == '__main__':
    unittest.main()
