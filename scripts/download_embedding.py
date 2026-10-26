"""Download a pinned, public BGE model into the project without remote model code."""
import argparse
import json
import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ['HF_HOME'] = str(ROOT / 'models' / 'hf-cache')
os.environ['HF_HUB_DISABLE_XET'] = '1'


if __name__ == '__main__':
    from huggingface_hub import snapshot_download
    parser=argparse.ArgumentParser()
    parser.add_argument('--revision',default='7999e1d3359715c523056ef9478215996d62a620',help='指定模型提交，默认使用本次已验证版本')
    args=parser.parse_args()
    name = 'BAAI/bge-small-zh-v1.5'
    destination = ROOT / 'models' / 'bge-small-zh-v1.5'
    snapshot_download(name, revision=args.revision, local_dir=destination, token=False,
                      allow_patterns=['*.json', '*.txt', '*.safetensors', '1_Pooling/*', 'README.md'],
                      max_workers=3)
    (destination / 'campus_model_info.json').write_text(json.dumps({
        'model_id':name, 'revision':args.revision, 'source':'https://huggingface.co/' + name,
        'license':'MIT', 'downloaded_at':date.today().isoformat(),
        'query_prefix':'为这个句子生成表示以用于检索相关文章：'}, ensure_ascii=False, indent=2), encoding='utf-8')
    print('模型已下载：' + str(destination))
