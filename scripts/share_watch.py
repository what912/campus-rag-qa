"""Follow temporary-tunnel registrations without sending network requests.

The SSH connection may remain alive while localhost.run replaces its public
hostname. Keep the gateway origin, state, and private instructions in sync.
No credentials, events, URLs, or config contents are printed by this helper.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

REGISTERED_EVENT = 'v1.tcpip_forward.register.accepted'
HTTPS_TOKEN = re.compile(r'https://[^\s<>"\'\\]+', re.IGNORECASE)
DNS_LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z')
CONTROL_HOSTS = {'admin.localhost.run', 'www.localhost.run', 'docs.localhost.run', 'status.localhost.run'}


def valid_public_url(value: str) -> str | None:
    """Return a normalized, provider-owned HTTPS origin or reject the value."""
    if not isinstance(value, str) or value != value.strip():
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').lower()
        if (parsed.scheme.lower() != 'https' or not host or parsed.username is not None
                or parsed.password is not None or parsed.port is not None
                or parsed.path or parsed.query or parsed.fragment or len(host) > 253):
            return None
        if (host in CONTROL_HOSTS or not all(DNS_LABEL.fullmatch(label) for label in host.split('.'))
                or not (host.endswith('.lhr.life') or host.endswith('.localhost.run'))):
            return None
        # urlsplit treats a trailing colon as no port. It is still an explicit
        # port delimiter and is outside the accepted origin representation.
        if parsed.netloc.lower() != host:
            return None
        return 'https://' + host
    except (ValueError, TypeError):
        return None


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def extract_registered_url(line: str) -> str | None:
    """Extract an origin only from a successful registration JSON event."""
    try:
        event = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(event, dict) or event.get('type') != REGISTERED_EVENT:
        return None
    latest = None
    for text in _strings(event.get('data')):
        for token in HTTPS_TOKEN.findall(text):
            candidate = valid_public_url(token)
            if candidate:
                latest = candidate
    return latest


def latest_registered_url(log_path: Path) -> str | None:
    """Scan complete JSON lines; partial writes and unrelated log lines are ignored."""
    latest = None
    try:
        with Path(log_path).open('r', encoding='utf-8-sig', errors='replace') as stream:
            for line in stream:
                candidate = extract_registered_url(line)
                if candidate:
                    latest = candidate
    except OSError:
        return None
    return latest


def process_alive(pid: int) -> bool:
    """Check liveness without signals that can terminate processes on Windows."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)


def _read_json(path: Path) -> dict | None:
    try:
        if Path(path).stat().st_size > 64 * 1024:
            return None
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None


def owned_state(state_path: Path, tunnel_pid: int,
                alive: Callable[[int], bool] = process_alive) -> dict | None:
    state = _read_json(state_path)
    value = state.get('tunnel_pid') if state else None
    if (not isinstance(value, int) or isinstance(value, bool) or value != tunnel_pid
            or not alive(tunnel_pid)):
        return None
    return state


def atomic_write_text(path: Path, text: str) -> None:
    """Replace one local file atomically; never echo its potentially private text."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         prefix='.' + path.name + '.', suffix='.tmp',
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def atomic_write_json(path: Path, value: dict) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def instructions_text(url: str, username: str, password: str, updated_at: str) -> str:
    return ('知校 · 临时公网演示\n'
            f'更新时间：{updated_at}\n\n'
            f'访问网址：{url}\n账号：{username}\n密码：{password}\n\n'
            '请在其他电脑浏览器打开以上 HTTPS 网址并登录。\n'
            '这份文件含访问口令，请只交给需要试用的人。\n'
            '免费域名可能轮换；请以本文件中的最新网址为准。\n'
            '本电脑需保持开机、联网，主服务和公网演示需保持运行。\n'
            '停止演示可双击项目根目录的“停止公网演示.cmd”；本机服务不会因此停止。\n')


def sync_public_url(state_path: Path, config_path: Path, instruction_path: Path,
                    tunnel_pid: int, url: str, *,
                    alive: Callable[[int], bool] = process_alive,
                    updated_at: str | None = None) -> bool:
    """Synchronize only a live, currently recorded tunnel; preserve other fields.

    Callers can inject a liveness function and timestamp for isolated tests.
    The function never prints the config, instructions, or their credentials.
    """
    url = valid_public_url(url)
    if not url or owned_state(state_path, tunnel_pid, alive) is None:
        return False
    config = _read_json(config_path)
    if config is None:
        return False
    username, password = config.get('username'), config.get('password')
    if (not isinstance(username, str) or not 1 <= len(username) <= 80
            or not isinstance(password, str) or not 20 <= len(password) <= 256
            or any(character in username + password for character in '\r\n')):
        return False
    try:
        prior_instructions = Path(instruction_path).read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError):
        prior_instructions = ''
    state = owned_state(state_path, tunnel_pid, alive)
    if state is None:
        return False
    correct_instructions = all(line in prior_instructions.splitlines() for line in
                               (f'访问网址：{url}', f'账号：{username}', f'密码：{password}'))
    if (state.get('public_url') == url and config.get('public_origin') == url
            and config.get('cookie_auth') is True and correct_instructions):
        return False
    timestamp = updated_at or datetime.now(timezone.utc).isoformat()
    config['public_origin'] = url
    config['cookie_auth'] = True
    atomic_write_json(config_path, config)
    # Re-read before replacing state so updates to proxy_pid/watcher_pid made
    # by the launcher are retained, and a replaced/stopped tunnel exits closed.
    state = owned_state(state_path, tunnel_pid, alive)
    if state is None:
        return False
    state['public_url'] = url
    state['updated_at'] = timestamp
    atomic_write_json(state_path, state)
    if owned_state(state_path, tunnel_pid, alive) is None:
        return False
    atomic_write_text(instruction_path, instructions_text(url, username, password, timestamp))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--log', required=True, type=Path)
    parser.add_argument('--instructions', required=True, type=Path)
    parser.add_argument('--tunnel-pid', required=True, type=int)
    args = parser.parse_args()
    if args.tunnel_pid <= 0:
        parser.error('--tunnel-pid must be positive')
    while owned_state(args.state, args.tunnel_pid) is not None:
        latest = latest_registered_url(args.log)
        if latest:
            try:
                sync_public_url(args.state, args.config, args.instructions,
                                args.tunnel_pid, latest)
            except OSError:
                # Antivirus/file replacement contention can be transient. The
                # next pass retries from the log without disclosing file data.
                pass
        time.sleep(2)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
