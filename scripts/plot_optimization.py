"""Export development figures and implementation diagrams using actual local results."""
import csv
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyBboxPatch

ROOT=Path(__file__).resolve().parents[1]
FONT=FontProperties(fname=r'C:\Windows\Fonts\msyh.ttc')
OUT=ROOT/'output/figures';OUT.mkdir(parents=True,exist_ok=True)


def box(ax,x,y,w,h,text):
    ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle='round,pad=.015',facecolor='#f8fafb',edgecolor='#536c7b',linewidth=1.2))
    ax.text(x+w/2,y+h/2,text,ha='center',va='center',fontproperties=FONT,fontsize=11)


def arrow(ax,a,b):
    ax.annotate('',xy=b,xytext=a,arrowprops={'arrowstyle':'->','color':'#536c7b','lw':1.3})


if __name__=='__main__':
    report=json.loads((ROOT/'evaluation/results/retrieval-optimization-report.json').read_text(encoding='utf-8'))
    rows=report['results'];labels=['TF-IDF','BGE 语义','RRF 混合','规则重排序']
    fig,axes=plt.subplots(1,2,figsize=(10,4.3),dpi=180)
    for ax,metric,title in zip(axes,['mean_recall_at_k','mean_mrr'],['平均片段 Recall@4','MRR']):
        values=[r[metric] for r in rows];ax.bar(range(4),values,color=['#82939e','#637d90','#3e6a7e','#237b82'],width=.6)
        ax.set_xticks(range(4),labels,fontproperties=FONT,fontsize=10);ax.set_ylim(0,1.12)
        ax.set_title(title,fontproperties=FONT,fontsize=13);ax.spines[['top','right']].set_visible(False)
        ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
        for i,v in enumerate(values):ax.text(i,v+.025,f'{v:.3f}',ha='center',fontsize=10)
    fig.suptitle('19 道开发题的检索修复检查',fontproperties=FONT,fontsize=15)
    fig.text(.5,.025,'开发题参与规则设计；标注证据命中不能解释为独立测试或生成答案正确率。',fontproperties=FONT,ha='center',fontsize=9)
    fig.tight_layout(rect=(0,.08,1,.94))
    for extension in ('png','svg'):fig.savefig(OUT/f'retrieval-optimization.{extension}',bbox_inches='tight')
    with (OUT/'retrieval-optimization.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.writer(file);writer.writerow(['方案','证据命中率','平均片段Recall@4','MRR','检索P95毫秒'])
        for label,row in zip(labels,rows):writer.writerow([label,row['evidence_hit_rate'],row['mean_recall_at_k'],row['mean_mrr'],row['retrieval_p95_ms']])
    plt.close(fig)
    fig,ax=plt.subplots(figsize=(10,4.5),dpi=180);ax.set_xlim(0,10);ax.set_ylim(0,4.5);ax.axis('off')
    for i,text in enumerate(['正式资料\n来源与版本','解析与必要OCR\n标题与页码','400字符切片\n60字符重叠','SQLite片段\nTF-IDF与BGE']):
        box(ax,.25+i*2.45,3.3,2.1,.8,text)
        if i<3:arrow(ax,(2.4+i*2.45,3.7),(2.65+i*2.45,3.7))
    for i,text in enumerate(['用户问题\n学校专业年级','类别路由\n范围筛选','检索与规则排序\n前4条证据','证据约束生成\nDeepSeek接口']):
        box(ax,.25+i*2.45,1.6,2.1,.8,text)
        if i<3:arrow(ax,(2.4+i*2.45,2),(2.65+i*2.45,2))
    arrow(ax,(8.65,3.25),(6.2,2.45));arrow(ax,(8.65,1.55),(8.65,.95))
    box(ax,5.15,.15,4.5,.75,'回答与来源编号  原文片段  原件下载')
    ax.text(.3,.65,'证据不足时本地提示无法确认\n不发送生成请求',fontproperties=FONT,fontsize=10,color='#536c7b')
    for extension in ('png','svg'):fig.savefig(OUT/f'system-architecture.{extension}',bbox_inches='tight')
    plt.close(fig)
    fig,ax=plt.subplots(figsize=(10,4),dpi=180);ax.set_xlim(0,10);ax.set_ylim(0,4);ax.axis('off')
    box(ax,.2,1,2.7,2.7,'documents\nid 主键\nname category version\nsource sha256 file_path\nsample metadata')
    box(ax,3.6,1,2.7,2.7,'chunks\nid 主键\ndocument_id 外键\nordinal text\npage heading')
    arrow(ax,(2.95,2.3),(3.55,2.3));ax.text(3.2,2.55,'1:N',ha='center',fontsize=10)
    box(ax,7,1,2.7,2.7,'评分JSON文件\n实验文件名\nsource_sha256\n题号与方案\n评分人与复核意见')
    box(ax,.2,.05,6.1,.65,'metadata 保存初始化等内部状态')
    ax.text(7,.4,'实验原始JSON按哈希绑定评分',fontproperties=FONT,fontsize=9,color='#536c7b')
    for extension in ('png','svg'):fig.savefig(OUT/f'data-model.{extension}',bbox_inches='tight')
    print('Exported development chart, architecture and data model.')
