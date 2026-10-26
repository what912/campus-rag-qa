"""Create exportable research plots from the reviewed development retrieval report."""
import csv
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties

ROOT = Path(__file__).resolve().parents[1]


if __name__ == '__main__':
    report = json.loads((ROOT/'evaluation/results/retrieval-development-report.json').read_text(encoding='utf-8'))
    results = report['results']
    font = FontProperties(fname=r'C:\Windows\Fonts\msyh.ttc')
    plt.rcParams['axes.unicode_minus'] = False
    labels=['TF-IDF','BGE 语义','RRF 混合']
    colors=['#48667c','#879aa7','#237b82']
    fig, axes=plt.subplots(1,2,figsize=(9,4),dpi=180)
    for ax, metric, title in zip(axes,('mean_recall_at_k','mean_mrr'),('片段 Recall@4','MRR')):
        values=[r[metric] for r in results]
        ax.bar(range(3),values,color=colors,width=.58)
        ax.set_ylim(0,1.08)
        ax.set_xticks(range(3),labels,fontproperties=font)
        ax.set_title(title,fontproperties=font,fontsize=13)
        ax.grid(axis='y',alpha=.2)
        ax.set_axisbelow(True)
        ax.spines[['top','right']].set_visible(False)
        for i,value in enumerate(values):
            ax.text(i,value+.025,f'{value:.3f}',ha='center',fontsize=10)
    fig.suptitle('19 道已核对开发题：检索对照',fontproperties=font,fontsize=15)
    fig.text(.5,.015,'开发数据，不能作为独立测试结论；未含无依据题，不评价生成答案。',
             fontproperties=font,ha='center',fontsize=9,color='#526575')
    fig.tight_layout(rect=(0,.06,1,.93))
    output=ROOT/'output/figures';output.mkdir(parents=True,exist_ok=True)
    fig.savefig(output/'retrieval-development.png',bbox_inches='tight')
    fig.savefig(output/'retrieval-development.svg',bbox_inches='tight')
    with (output/'retrieval-development.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.writer(file)
        writer.writerow(['方案','Recall@4','MRR','检索P95毫秒','已标注问题数'])
        for label,row in zip(labels,results):
            writer.writerow([label,row['mean_recall_at_k'],row['mean_mrr'],row['retrieval_p95_ms'],row['scored_answerable']])
    print(output/'retrieval-development.png')
