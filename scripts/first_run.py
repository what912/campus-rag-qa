"""Prepare a local environment and bundled knowledge before starting the app."""
import argparse
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
IMPORT_CHECK = 'import fastapi, uvicorn, multipart, httpx, numpy, scipy, sklearn, pypdf, docx'
RUNTIME_CHECK = ('import sys, struct, venv, ensurepip; '
                 'assert sys.version_info[:2] == (3, 12) and struct.calcsize("P") == 8')


def child_environment():
    environment = dict(os.environ)
    environment.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    environment.setdefault('OMP_NUM_THREADS', '2')
    environment.setdefault('MKL_NUM_THREADS', '2')
    environment.setdefault('TOKENIZERS_PARALLELISM', 'false')
    return environment


def read_state(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    try:
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def prepare_lock(root):
    directory = root / '.tools'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'prepare.lock').open('a+b') as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
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
                if os.name == 'nt':
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_step(command, root, message, capture=False):
    print(message, flush=True)
    try:
        result = subprocess.run([str(part) for part in command], cwd=root, env=child_environment(),
                                capture_output=capture, text=True, encoding='utf-8', errors='replace')
    except OSError as exc:
        raise RuntimeError(f'无法执行准备步骤：{exc}') from exc
    if result.returncode:
        if capture:
            print(((result.stderr or '') + '\n' + (result.stdout or ''))[-4000:], flush=True)
        raise RuntimeError(f'准备步骤失败（退出码 {result.returncode}）。请检查上方提示，联网后重新双击启动。')
    return result


def ensure_environment(root):
    environment = root / '.venv'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    usable = False
    if python.is_file():
        try:
            result = subprocess.run([str(python), '-I', '-c', RUNTIME_CHECK], cwd=root,
                                    env=child_environment(), capture_output=True)
            usable = result.returncode == 0
        except OSError:
            pass
    if not usable:
        if environment.exists():
            expected = root.resolve() / '.venv'
            backup = root.resolve() / '.tools' / f'venv-incomplete-{time.time_ns()}-{os.getpid()}'
            if environment.is_symlink() or environment.resolve() != expected or not backup.is_relative_to(root.resolve() / '.tools'):
                raise RuntimeError('运行环境路径不在本项目内，已停止自动修复。')
            if Path(sys.executable).resolve().is_relative_to(environment.resolve()):
                raise RuntimeError('当前运行环境无法继续使用，请关闭窗口后重新双击启动。')
            backup.parent.mkdir(parents=True, exist_ok=True)
            environment.rename(backup)
            print('已保留未完成的旧环境，正在重新准备。', flush=True)
        run_step([sys.executable, '-m', 'venv', environment], root,
                 '正在创建项目运行环境，仅首次需要，请保持此窗口打开。')
    probe = subprocess.run([str(python), '-I', '-c', IMPORT_CHECK], cwd=root,
                           env=child_environment(), capture_output=True)
    requirements = root / 'requirements.txt'
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = environment / '.campus-requirements.json'
    previous = read_state(marker)
    changed = previous.get('sha256') not in (None, digest)
    if probe.returncode or changed:
        pip = subprocess.run([str(python), '-m', 'pip', '--version'], cwd=root,
                             env=child_environment(), capture_output=True)
        if pip.returncode:
            run_step([python, '-m', 'ensurepip', '--upgrade'], root, '正在准备依赖安装工具。')
        run_step([python, '-m', 'pip', 'install', '--disable-pip-version-check', '--only-binary=:all:',
                  '--timeout', '30', '--retries', '2', '-r', requirements], root,
                 '正在联网安装项目依赖，首次可能需要几分钟；以后会复用已安装的环境。')
        checked = subprocess.run([str(python), '-I', '-c', IMPORT_CHECK], cwd=root,
                                 env=child_environment(), capture_output=True)
        if checked.returncode:
            detail = checked.stderr.decode('utf-8', errors='replace')[-3000:]
            raise RuntimeError('依赖安装后的检查未通过：\n' + detail)
    if previous.get('sha256') != digest:
        write_state(marker, {'sha256': digest})
    return python


def has_existing_corpus(database):
    if not database.is_file():
        return False
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
            table = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='documents'").fetchone()
            return bool(table and connection.execute('SELECT 1 FROM documents WHERE sample=0 LIMIT 1').fetchone())
    except sqlite3.Error as exc:
        raise RuntimeError('现有知识库无法读取，已停止准备以保留原有资料。请检查 data 目录。') from exc


def ensure_corpus(root, python, data_dir):
    marker = data_dir / '.first-run.json'
    state = read_state(marker)
    if state.get('status') == 'complete' and (data_dir / 'knowledge.db').is_file():
        return
    if state.get('status') != 'pending' and has_existing_corpus(data_dir / 'knowledge.db'):
        # An established developer/user database keeps its own document selection.
        # The first-run importer never changes its settings, credentials or corpus.
        return
    write_state(marker, {'status': 'pending'})
    run_step([python, root / 'scripts/bootstrap_knowledge.py', '--data-dir', data_dir], root,
             '正在从随附资料建立知识库，不调用大模型。完成后会自动启动网页。', capture=True)
    if not (data_dir / 'knowledge.db').is_file():
        raise RuntimeError('资料导入未生成知识库，请检查上方提示后重试。')
    print('资料准备完成。', flush=True)
    write_state(marker, {'status': 'complete'})


def prepare(root):
    root = Path(root).resolve()
    for relative in ('run.py', 'requirements.txt', 'scripts/bootstrap_knowledge.py', 'materials/manifest.json'):
        if not (root / relative).is_file():
            raise RuntimeError('项目文件不完整。请先右键 ZIP 选择“全部解压”，再双击解压目录中的“启动系统.cmd”。')
    with prepare_lock(root) as acquired:
        if not acquired:
            print('另一个启动窗口正在准备运行环境，请保持原窗口打开，等待它完成。', flush=True)
            return None
        python = ensure_environment(root)
        data_dir = Path(os.environ.get('CAMPUS_DATA_DIR', str(root / 'data')))
        if not data_dir.is_absolute():
            data_dir = root / data_dir
        ensure_corpus(root, python, data_dir.resolve())
        return python


def main(argv=None):
    parser = argparse.ArgumentParser(description='首次自动准备校园问答系统', add_help=False)
    parser.add_argument('--prepare-only', action='store_true')
    args, remaining = parser.parse_known_args(argv)
    try:
        python = prepare(ROOT)
        if python is None or args.prepare_only:
            return 0
        return subprocess.run([str(python), str(ROOT / 'run.py'), *remaining],
                              cwd=ROOT, env=child_environment()).returncode
    except KeyboardInterrupt:
        print('\n已停止。再次双击启动时会继续准备。', flush=True)
        return 130
    except (RuntimeError, OSError) as exc:
        print('\n启动未完成：' + str(exc), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
