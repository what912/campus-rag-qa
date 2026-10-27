"""Consistent SQLite backup plus original uploads, without API credentials or model caches."""
import json
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


if __name__ == '__main__':
    directory = ROOT / 'output/backups'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ('knowledge-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.zip')
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        snapshot = Path(temporary) / 'knowledge.db'
        with closing(sqlite3.connect(ROOT / 'data/knowledge.db')) as source, closing(sqlite3.connect(snapshot)) as destination:
            source.backup(destination)
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot, 'data/knowledge.db')
            for file in (ROOT / 'data/uploads').glob('*'):
                if file.is_file():
                    archive.write(file, 'data/uploads/' + file.name)
            if (ROOT / 'materials/incoming').is_dir():
                for file in (ROOT / 'materials/incoming').rglob('*'):
                    if file.is_file():
                        archive.write(file, file.relative_to(ROOT).as_posix())
            if (ROOT / 'materials/user-material-manifest.json').is_file():
                archive.write(ROOT / 'materials/user-material-manifest.json', 'materials/user-material-manifest.json')
            if (ROOT/'materials/admissions').is_dir():
                for file in (ROOT/'materials/admissions').rglob('*'):
                    if file.is_file():archive.write(file,file.relative_to(ROOT).as_posix())
            if (ROOT/'materials/yz-official').is_dir():
                for file in (ROOT/'materials/yz-official').rglob('*'):
                    if file.is_file():archive.write(file,file.relative_to(ROOT).as_posix())
            for file in (ROOT/'data/reviews').glob('*.json'):
                archive.write(file,'data/reviews/'+file.name)
            archive.writestr('说明.txt', '包含文档原件和 SQLite 片段数据，不含 API 密钥、模型缓存或聊天记录。恢复前请先关闭系统；跨电脑恢复后需要按新路径重新配置模型。')
    print(target)
