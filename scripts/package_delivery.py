"""Package reviewable source/materials and a consistent DB; exclude credentials and caches."""
import hashlib
import json
import re
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SECRET=re.compile(rb'sk-[A-Za-z0-9_-]{20,}')

if __name__=='__main__':
    destination=ROOT/'output/校园RAG毕设交付包-20261013.zip'
    files=[]
    for folder in ('campus','static','scripts','tests','docs','samples','evaluation','materials'):
        files.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
    files.extend(ROOT/name for name in ('app.py','run.py','README.md','启动系统.cmd','.gitignore'))
    files.extend(ROOT.glob('requirements*.txt'))
    files.extend(p for p in (ROOT/'output/figures').glob('*') if p.is_file())
    files.extend(p for p in (ROOT/'output/screenshots').glob('optimized-*.png'))
    snapshots=sorted((ROOT/'output/frozen').glob('*'))
    if snapshots:
        files.extend(p for p in snapshots[-1].rglob('*') if p.is_file())
    files.extend(ROOT/'output'/name for name in ('论文技术初稿.docx','restore-verification.json','optimization-verification.json','optimization-tests.log','freeze-tests.log') if (ROOT/'output'/name).is_file())
    with tempfile.TemporaryDirectory(dir=ROOT/'output') as temporary:
        database=Path(temporary)/'knowledge.db'
        with closing(sqlite3.connect(ROOT/'data/knowledge.db')) as source,closing(sqlite3.connect(database)) as target:
            source.backup(target)
        files.extend(p for p in (ROOT/'data/uploads').glob('*') if p.is_file())
        files.extend(p for p in (ROOT/'data/reviews').glob('*.json'))
        config=json.loads((ROOT/'data/settings.json').read_text(encoding='utf-8'))
        config={k:v for k,v in config.items() if k not in ('api_key','remember_key')}
        config.update(enabled=False,remember_key=False,embedding_mode='tfidf',min_score=.08,
                      model_path='models/bge-small-zh-v1.5')
        with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(set(files)):
                body=file.read_bytes()
                if SECRET.search(body):
                    raise ValueError('发现疑似密钥，停止打包：'+str(file.relative_to(ROOT)))
                archive.writestr(file.relative_to(ROOT).as_posix(),body)
            archive.write(database,'data/knowledge.db')
            archive.writestr('data/settings.json',json.dumps(config,ensure_ascii=False,indent=2))
            archive.writestr('开始阅读.txt','先读README.md与docs/整体优化记录.md。包含源码、原件、数据库、开发实验和论文技术草稿。首次异机启动默认TF-IDF原文模式；依赖与BGE模型需按README准备，API密钥需本人重新配置。此包没有API凭据、虚拟环境、模型或聊天记录。正式独立实验尚未完成。')
        with zipfile.ZipFile(destination) as archive:
            if archive.testzip() is not None:
                raise ValueError('压缩包校验失败')
            entries=archive.namelist()
            if any(name.endswith('.dpapi') or Path(name).name in ('.env','credential.json','credentials.json')
                   or '.venv/' in name or '.tools/' in name or name.startswith('models/') for name in entries):
                raise ValueError('交付包包含禁止分发的缓存或凭据文件')
            if any(SECRET.search(archive.read(name)) for name in entries):
                raise ValueError('交付包发现疑似明文凭据')
        verification={'created_at':datetime.now().astimezone().isoformat(),'archive':destination.name,
                      'files':len(entries),'bytes':destination.stat().st_size,
                      'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),
                      'crc_valid':True,'credential_files_excluded':True,'plaintext_key_scan_passed':True,
                      'models_and_virtualenv_excluded':True,'generation_calls':0}
        (ROOT/'output/delivery-verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(verification,ensure_ascii=False))
