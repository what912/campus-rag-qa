"""Export descriptive charts from saved responses, without API calls or accuracy estimates."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties

ROOT=Path(__file__).resolve().parents[1]


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--summary',type=Path,required=True)
    args=parser.parse_args()
    report=json.loads(args.summary.read_text(encoding='utf-8'))
    if not report.get('completed'):
        parser.error('只能绘制已完成的开发记录。')
    groups=report['groups']
    labels=['直接回答','TF-IDF RAG','语义 RAG','混合 RAG']
    colors=['#7a8998','#48667c','#879aa7','#237b82']
    font=FontProperties(fname=r'C:\Windows\Fonts\msyh.ttc')
    fig,axes=plt.subplots(1,2,figsize=(10,4.4),dpi=180)
    for ax,values,title in (
        (axes[0],[r['mean_generation_ms']/1000 for r in groups],'实际生成请求平均耗时（秒）'),
        (axes[1],[r['total_tokens'] for r in groups],'API 返回的 token 总量')):
        ax.bar(range(4),values,width=.6,color=colors)
        ax.set_xticks(range(4),labels,fontproperties=font,fontsize=9)
        ax.set_ylim(0,max(values)*1.24)
        ax.set_title(title,fontproperties=font,fontsize=12)
        ax.spines[['top','right']].set_visible(False)
        ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
        for i,value in enumerate(values):
            label=f'{value:.2f}' if ax is axes[0] else f'{int(value):,}'
            ax.text(i,value+max(values)*.035,label,ha='center',fontsize=10)
    fig.suptitle(f"{groups[0]['questions']} 题 × {len(groups)} 方案：开发生成记录",fontproperties=font,fontsize=15)
    local=sum(g['local_no_evidence'] for g in groups)
    note=f"{report['generation_attempts']} 次实际请求；{local} 次本地提示未计入生成耗时。"
    note+=('缺少统一学校背景，不能据此比较回答效果。' if not report['baseline_school_explicit'] else '开发记录，不能作为独立测试结论。')
    fig.text(.5,.025,note,
             fontproperties=font,ha='center',fontsize=8.5,color='#526575')
    fig.tight_layout(rect=(0,.075,1,.93))
    output=ROOT/'output/figures';output.mkdir(parents=True,exist_ok=True)
    fig.savefig(output/'generation-development.png',bbox_inches='tight')
    fig.savefig(output/'generation-development.svg',bbox_inches='tight')
    print(output/'generation-development.png')
