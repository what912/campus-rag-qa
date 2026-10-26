"""Save immutable corpus/configuration and a balanced future run plan; does not call APIs."""
import argparse
import hashlib
import json
import platform
import random
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from campus.providers import build_messages

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--dataset',type=Path,default=ROOT/'evaluation/professional_questions.candidate.json')
    args=parser.parse_args()
    data=args.dataset.read_bytes();dataset=json.loads(data)
    destination=ROOT/'output/frozen'/datetime.now().strftime('%Y%m%d-%H%M%S')
    destination.mkdir(parents=True)
    (destination/'dataset.json').write_bytes(data)
    with closing(sqlite3.connect(ROOT/'data/knowledge.db')) as source,closing(sqlite3.connect(destination/'knowledge.db')) as target:
        source.backup(target)
        corpus=[{'id':row[0],'name':row[1],'sha256':row[2],'version':row[3]} for row in target.execute('SELECT id,name,sha256,version FROM documents WHERE sample=0 ORDER BY id')]
    shutil.copytree(ROOT/'data/uploads',destination/'uploads')
    settings=json.loads((ROOT/'data/settings.json').read_text(encoding='utf-8'))
    allowed=('base_url','model','embedding_mode','model_path','query_prefix','min_score','retrieval_strategy','target_cohort','target_major')
    config={k:settings[k] for k in allowed}
    model_dir=Path(config['model_path'])
    model_info=json.loads((model_dir/'campus_model_info.json').read_text(encoding='utf-8'))
    model_hashes={p.relative_to(model_dir).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in model_dir.rglob('*') if p.is_file() and '.cache' not in p.parts
                  and p.suffix in ('.json','.txt','.safetensors')}
    (destination/'embedding-model.json').write_text(json.dumps({'info':model_info,'file_hashes':model_hashes},ensure_ascii=False,indent=2),encoding='utf-8')
    modes=['direct','tfidf','semantic','hybrid']
    ordered=list(dataset['cases']);random.Random(2023).shuffle(ordered)
    schedule=[{'id':case['id'],'mode':mode} for i,case in enumerate(ordered) for mode in modes[i%4:]+modes[:i%4]]
    code_files=['app.py',*['campus/'+p.name for p in sorted((ROOT/'campus').glob('*.py'))],'scripts/evaluate_answers.py','scripts/evaluate_retrieval.py']
    code_dir=destination/'source';code_hashes={}
    for relative in code_files:
        file=ROOT/relative;target=code_dir/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,target)
        code_hashes[relative]=hashlib.sha256(file.read_bytes()).hexdigest()
    manifest={'created_at':datetime.now().astimezone().isoformat(),'status':'prepared_not_executed',
        'dataset_role':dataset.get('role'),'dataset_sha256':hashlib.sha256(data).hexdigest(),
        'database_sha256':hashlib.sha256((destination/'knowledge.db').read_bytes()).hexdigest(),
        'all_labels_reviewed':all(c.get('reviewed') for c in dataset['cases']),
        'requires_user_run_authorization':True,'maximum_generation_calls':len(schedule),'generation_calls_sent':0,
        'school':'中国传媒大学','configuration':config,'embedding_model':model_info,'model_file_hashes':model_hashes,
        'top_k':4,'tfidf_threshold':.08,'semantic_threshold':.55,
        'seed':2023,'execution_order':'balanced',
        'temperature':.1,'max_output_tokens':1400,'chunk_characters':400,'overlap_characters':60,
        'corpus':corpus,'source_hashes':code_hashes,'execution_schedule':schedule,
        'prompts':{'direct_system':'你是校园知识问答助手。请用中文回答，不确定时说明无法确认。',
            'rag_system':build_messages('',[])[0]['content'],
            'shared_background':'用户所在学校：中国传媒大学。用户适用范围：2023级，计算机科学与技术。'},
        'environment':{'python':sys.version,'platform':platform.platform()},
        'note':'候选标签尚未审核时不得当正式测试；这个包只准备计划和快照，不授权、不执行生成。代码以后若变化，需重新冻结，不能套用旧计划。'}
    (destination/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    (destination/'说明.txt').write_text('这是尚未执行的快照与计划。候选题需人工核对。没有发送模型请求。密钥不包含在此目录。',encoding='utf-8')
    print(json.dumps({'snapshot':str(destination),'questions':len(dataset['cases']),'maximum_calls':len(schedule),'labels_reviewed':manifest['all_labels_reviewed'],'calls_sent':0},ensure_ascii=False))
