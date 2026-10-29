"""Start the local app and open its page only after initialization finishes."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import threading
import time
from urllib.request import ProxyHandler, build_opener
import webbrowser

ROOT = Path(__file__).resolve().parent
TITLE = '知校 · 校园知识问答'


def read_json(url):
    """Loopback checks must not go through the user's system HTTP proxy."""
    try:
        with build_opener(ProxyHandler({})).open(url, timeout=1) as response:
            return json.loads(response.read(1_000_000))
    except (OSError, ValueError):
        return None


def is_campus_service(url):
    health = read_json(url + '/api/health')
    if not isinstance(health, dict) or health.get('status') != 'ok':
        return False
    schema = read_json(url + '/openapi.json')
    if not isinstance(schema, dict) or not isinstance(schema.get('info'), dict):
        return False
    paths = schema.get('paths')
    return (schema['info'].get('title') == TITLE and isinstance(paths, dict)
            and '/api/ask' in paths and '/api/status' in paths)


def open_browser_url(url):
    try:
        if webbrowser.open(url):
            return
    except (OSError, webbrowser.Error):
        pass
    print('未能自动打开浏览器，请手动访问：' + url, flush=True)


def watch_readiness(server, url, stop_event, open_browser=True):
    last_notice = time.monotonic()
    while not stop_event.is_set():
        if server.started and is_campus_service(url):
            print('\n服务已就绪：' + url + '\n按 Ctrl+C 停止服务。\n', flush=True)
            if open_browser:
                open_browser_url(url)
            return
        now = time.monotonic()
        if now - last_notice >= 10:
            print('仍在加载本地模型和知识库，请保持此窗口打开；就绪后会显示访问地址。', flush=True)
            last_notice = now
        stop_event.wait(0.25)


@contextmanager
def startup_lock(port):
    """Prevent repeated double clicks from rebuilding the same index in parallel."""
    directory = ROOT / 'output'
    directory.mkdir(exist_ok=True)
    handle = (directory / f'startup-{port}.lock').open('a+b')
    acquired = False
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except (BlockingIOError, PermissionError):
            pass
        yield acquired
    finally:
        if acquired:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def port_is_listening(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except (ConnectionRefusedError, TimeoutError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description='启动校园 RAG 原型')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('--port 必须介于 1 和 65535 之间。')
    url = f'http://127.0.0.1:{args.port}'
    with startup_lock(args.port) as acquired:
        if is_campus_service(url):
            print('系统已在运行，复用现有服务：' + url, flush=True)
            if not args.no_browser:
                open_browser_url(url)
            return 0
        if not acquired:
            print('系统正在另一个窗口中启动，请保持原启动窗口打开。\n'
                  '加载完成后访问：' + url, flush=True)
            return 0
        if port_is_listening(args.port):
            print(f'启动失败：端口 {args.port} 已被其他服务占用。\n'
                  '请检查占用程序，或运行 python run.py --port 8766。', flush=True)
            return 1

        # Set these before importing the scientific/model libraries inside app.py.
        os.environ.setdefault('OMP_NUM_THREADS', '2')
        os.environ.setdefault('MKL_NUM_THREADS', '2')
        os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
        import uvicorn

        next_step = ('服务就绪后会显示访问地址。' if args.no_browser
                     else '服务就绪后才会打开浏览器。')
        print('\n' + TITLE + '\n正在加载本地模型和知识库，可能需要数分钟。\n'
              + next_step + '请保持此窗口打开。\n', flush=True)
        server = uvicorn.Server(uvicorn.Config('app:app', host='127.0.0.1',
                                               port=args.port, log_level='info'))
        stopped = threading.Event()
        reporter = threading.Thread(target=watch_readiness,
                                    args=(server, url, stopped, not args.no_browser), daemon=True)
        reporter.start()
        try:
            server.run()
        finally:
            stopped.set()
            reporter.join(timeout=3)
        return 0 if server.started else 1

if __name__ == '__main__':
    raise SystemExit(main())
