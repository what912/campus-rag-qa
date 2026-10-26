"""Inspect the exact Git index for credentials, local runtime data and oversized files."""
import json
import re
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PATTERNS=(re.compile(rb'sk-[A-Za-z0-9_-]{20,}'),re.compile(rb'gh[pousr]_[A-Za-z0-9]{24,}'),
          re.compile(rb'github_pat_[A-Za-z0-9_]{30,}'),re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'))

if __name__=='__main__':
    files=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode('utf-8').split('\0')
    files=[p for p in files if p]
    indexed=subprocess.check_output(['git','cat-file','--batch'],cwd=ROOT,
                                    input=''.join(':'+p+'\n' for p in files).encode('utf-8'))
    cursor=0
    bad=[];total=0;largest=0
    for name in files:
        if name.startswith(('data/','models/','.venv/','.tools/','tmp/','output/','artifacts/')) or name.endswith(('.dpapi','.pyc')) or Path(name).name.startswith('.env'):
            bad.append({'file':name,'reason':'local_data_or_credentials'})
        boundary=indexed.index(b'\n',cursor)
        header=indexed[cursor:boundary].split();size=int(header[-1])
        body=indexed[boundary+1:boundary+1+size];cursor=boundary+size+2
        total+=len(body);largest=max(largest,len(body))
        if len(body)>50*1024*1024: bad.append({'file':name,'reason':'oversized'})
        if any(pattern.search(body) for pattern in PATTERNS): bad.append({'file':name,'reason':'possible_secret'})
    report={'tracked_files':len(files),'tracked_bytes':total,'largest_file_bytes':largest,
            'credential_scan_passed':not bad,'excluded_runtime_directories':True,'findings':bad}
    print(json.dumps(report,ensure_ascii=False,indent=2))
    raise SystemExit(1 if bad else 0)
