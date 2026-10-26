"""Bundle explicitly selected development artifacts; never package credentials or settings."""
import hashlib
import json
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


if __name__=='__main__':
    prefix='answers-20261013-153455'
    files=list((ROOT/'evaluation/results').glob(prefix+'*.json'))
    files+=list((ROOT/'evaluation/results').glob(prefix+'*.csv'))
    files += [ROOT/path for path in (
        'evaluation/public_questions.development.json',
        'evaluation/professional_questions.candidate.json',
        'evaluation/人工评分模板.md',
        'evaluation/results/retrieval-development-report.json',
        'docs/开发对照结果.md','docs/模型回答核对表.md','docs/专业候选问题核对表.md',
        'docs/本次接入与资料处理记录.md','docs/下一步任务.md',
        'output/figures/generation-development.png','output/figures/generation-development.svg',
        'output/figures/retrieval-development.png','output/figures/retrieval-development.svg',
        'output/figures/retrieval-development.csv')]
    files=sorted(set(files))
    report=json.loads((ROOT/'evaluation/results'/f'{prefix}-corrected.json').read_text(encoding='utf-8'))
    if not report['completed'] or report['generation_attempts']>76:
        raise SystemExit('记录未完成或超过授权上限，停止打包。')
    manifest=[]
    for file in files:
        if not file.is_file():
            raise SystemExit(f'缺少文件：{file.name}')
        manifest.append({'path':file.relative_to(ROOT).as_posix(),
                         'sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
    target=ROOT/'output/开发实验资料包-20261013.zip'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            archive.write(file,file.relative_to(ROOT).as_posix())
        archive.writestr('文件哈希清单.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        archive.writestr('先读说明.txt',
            '这是开发记录，不是正式独立测试结果。76条结果、75次DeepSeek请求。\n'
            '先读docs/开发对照结果.md，再看逐题核对表。直接回答组缺少学校背景；原始提示与回答保留。\n'
            '修正记录只补全原来未调用API的两个结果，原始记录不变。评分栏尚未填写。\n'
            '20道专业候选题尚未人工审核，也未调用模型。\n'
            '本包不含API密钥、配置、模型权重或文档原件。原件与知识库另在项目及知识库备份中。')
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise SystemExit('压缩包完整性检查失败。')
    print(f'Bundle verified: {len(files)} files; {target.stat().st_size} bytes.')
