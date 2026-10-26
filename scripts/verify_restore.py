"""Verify a workspace backup in a disposable restore directory, without credentials or APIs."""
import argparse
import hashlib
import json
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from campus.knowledge import KnowledgeBase

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--backup',type=Path)
    args=parser.parse_args()
    source=args.backup or sorted((ROOT/'output/backups').glob('knowledge-*.zip'))[-1]
    output=(ROOT/'output').resolve()
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        restored=Path(temporary).resolve()
        assert restored.parent==output
        with zipfile.ZipFile(source) as archive:
            if archive.testzip() is not None:
                raise SystemExit('压缩包完整性检查失败。')
            for member in archive.infolist():
                target=(restored/member.filename).resolve()
                if not target.is_relative_to(restored):
                    raise SystemExit('压缩包含不安全的路径。')
                if member.filename.endswith(('credential.dpapi','settings.json')):
                    raise SystemExit('备份意外包含凭据或设置。')
            archive.extractall(restored)
        kb=KnowledgeBase(restored/'data')
        documents=kb.documents()
        for document in documents:
            detail=kb.document(document['id']);path=Path(detail['file_path'])
            if not path.is_relative_to(restored) or hashlib.sha256(path.read_bytes()).hexdigest()!=document['sha256']:
                raise SystemExit('恢复原件路径或哈希不符。')
        proof=kb.search('在读证明可以线上自助办理吗？',include_samples=False,min_score=.08)
        assert any(s['name']=='在读证明管理办法_2024.pdf' for s in proof)
        stats=kb.stats()
    report={'verified_at':datetime.now().astimezone().isoformat(),'backup':source.name,'stats':stats,
        'original_file_hashes_match':True,'paths_relocated':True,'credentials_excluded':True,
        'retrieval_smoke_passed':True,'model_calls':0,
        'note':'本机独立恢复目录演练，不是另一台电脑的完整安装测试；恢复测试使用TF-IDF，不验证远程生成。'}
    (ROOT/'output/restore-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
