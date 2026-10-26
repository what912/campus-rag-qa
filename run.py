"""Run from any working directory. Port and browser opening are optional CLI flags."""
import argparse
import threading
import webbrowser

import uvicorn

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='启动校园 RAG 原型')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    url = f'http://127.0.0.1:{args.port}'
    print('\n知校 · 校园知识问答系统\n打开浏览器访问：' + url + '\n按 Ctrl+C 停止服务。\n', flush=True)
    if not args.no_browser:
        timer = threading.Timer(1.5, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    uvicorn.run('app:app', host='127.0.0.1', port=args.port, log_level='info')
