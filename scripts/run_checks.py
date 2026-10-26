"""Run tests against disposable application data, never the user's configured knowledge base."""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--pattern',default='test_*.py')
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='campus-checks-') as temporary:
        env=dict(os.environ,CAMPUS_DATA_DIR=temporary,OMP_NUM_THREADS='2',MKL_NUM_THREADS='2',TOKENIZERS_PARALLELISM='false',PYTHONIOENCODING='utf-8')
        for key in ('CAMPUS_LLM_API_KEY','CAMPUS_LLM_BASE_URL','CAMPUS_LLM_MODEL'):
            env.pop(key,None)
        result=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-p',args.pattern,'-v'],
                              cwd=ROOT,env=env,timeout=300)
        raise SystemExit(result.returncode)
