"""Inspect the publisher's portable-renderer download directory; no model requests."""
import re
import argparse
import hashlib
import json
from pathlib import Path
import httpx

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--version',default='');parser.add_argument('--download',action='store_true')
    args=parser.parse_args()
    url='https://download.documentfoundation.org/libreoffice/portable/'
    if args.version:
        if not re.fullmatch(r'\d+\.\d+\.\d+',args.version):
            parser.error('invalid version')
        url+=args.version+'/'
    if not args.download:
        response=httpx.get(url,follow_redirects=True,timeout=45);response.raise_for_status()
        print('\n'.join(re.findall(r'href="([^"]+)"',response.text)))
    else:
        if args.version!='26.2.4':
            parser.error('只下载已核对的26.2.4版本')
        filename='LibreOfficePortable_26.2.4_MultilingualStandard.paf.exe'
        expected='4bde93374aef4409243505b20d16561a4628ac7591457dd01fc6e1ccf571ba65'
        folder=Path(__file__).resolve().parents[1]/'.tools/downloads';folder.mkdir(parents=True,exist_ok=True)
        target=folder/filename
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest()==expected:
            print('Verified renderer download already exists.');raise SystemExit(0)
        digest=hashlib.sha256();size=0;next_progress=32*1024*1024
        with httpx.Client(follow_redirects=True,timeout=60) as client,client.stream('GET',url+filename) as response:
            response.raise_for_status()
            with target.with_suffix('.part').open('wb') as out:
                for chunk in response.iter_bytes(1024*1024):
                    out.write(chunk);digest.update(chunk);size+=len(chunk)
                    if size>=next_progress:
                        print(f'Renderer download: {size//1048576} MiB',flush=True);next_progress+=32*1024*1024
        if digest.hexdigest()!=expected:
            raise SystemExit('校验失败，未执行下载文件。')
        target.with_suffix('.part').replace(target)
        (folder/'renderer-source.json').write_text(json.dumps({'url':url+filename,'sha256':expected,'bytes':size}),encoding='utf-8')
        print('Portable renderer download verified.',flush=True)
