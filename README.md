# 知校 · 校园知识问答系统

本科毕业设计：**基于大模型 RAG 的校园知识问答系统设计与实现**。当前范围：中国传媒大学通用教学办事文件，以及 **2023 级计算机科学与技术专业**培养方案、课程大纲。

更新于 2026-10-26，系统 v0.5.0。实现真实校园资料处理、本地语义检索、证据生成与多轮问答。新增本地历史会话、追问改写、真实流式输出、引用点击定位与导入记录。**原开发电脑已接通DeepSeek；从仓库首次安装时默认关闭生成，需要本人填写API配置。**

已有回答核对、用户评分保存、答案与依据导出，以及逐题“仅查看原文（不调用模型）”。此前19题×4方案开发对照保存76条结果、实际75次生成请求；本次补充没有发送新的真实生成请求。开发题标注证据全部进入前4条，不能将其理解为生成准确率或独立测试结论。本轮依据和边界见 [任务文档对照与补充记录](docs/任务文档对照与补充记录.md)，上一轮记录见 [整体优化记录](docs/整体优化记录.md)。

论文内容见 [论文技术初稿](docs/论文技术初稿.md)；原开发电脑另有 `output/论文技术初稿.docx`。该初稿对应2026-10-13实现，本次多轮与流式功能应按新记录补入论文。论文尚需学院本届模板与正式结果；演示练习见 [答辩讲解与演示指南](docs/答辩讲解与演示指南.md)。

## 从 GitHub 首次运行

需要Python 3.12。仓库不包含凭据、虚拟环境、模型缓存或本机数据库；包含当前资料原件与哈希清单。仓库默认私有，校园资料权利范围见 [第三方资料说明](THIRD_PARTY_NOTICES.md)。

```powershell
git clone https://github.com/what912/campus-rag-qa.git
cd campus-rag-qa
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\bootstrap_knowledge.py
.\.venv\Scripts\python.exe run.py
```

首次即可使用TF-IDF和原文问答，不调用生成模型。Linux或macOS将 `.venv\Scripts\python.exe` 换成 `.venv/bin/python`。语义模型、OCR和API接入按后续章节准备。自动测试使用 `pip install -r requirements-dev.txt`，随后运行 `python scripts/run_checks.py`；测试使用临时数据，不读取本机真实API配置。

## 多轮问答与历史

同一会话可先问“数据结构课程的考核方式是什么”，再问“那操作系统呢”。系统会显示补全后的查询，并重新检索原文。常见代词和短追问由规则处理，指代不清会请用户说明；复杂跨轮推理不保证自动解析。

会话自动保存在本机 `data/conversations.db`，刷新后可恢复；“历史会话”可打开或删除，“新建问答”开始独立上下文。每会话最多100轮、最多保存100个会话。历史展示的是当时保存的证据快照，原文更新后应重新核对。

启用生成时页面读取真实上游SSE增量，支持停止接收。停止接收不保证服务商取消生成或计费。中断的部分文本明确标记，未完成时不保存为完整回答。本轮流式协议用模拟HTTP接口测试，未新增真实DeepSeek生成测试。

## 在这台电脑启动

1. 双击项目根目录的 `启动系统.cmd`，保持启动窗口打开。
2. 浏览器打开 <http://127.0.0.1:8765>。可勾选“仅查看原文（不调用模型）”体验本地检索；取消后发送问题会使用已开启的DeepSeek接口，可能产生费用。
3. “我的知识库”可查看文档、片段、原件，也可上传自己的资料。
4. 结束后在启动窗口按 `Ctrl+C`。端口占用时先访问已有页面；另开服务使用 `.\.venv\Scripts\python.exe run.py --port 8766`。

本机依赖和模型已经准备好，检索与 OCR 可离线运行。启动脚本不会自动下载依赖。原件、向量、片段留在项目文件夹；调用外部模型时，问题和检索片段会发送给配置的服务。

## 当前资料与检索

- 收集 6 份官网 PDF：学籍、转专业、学位、在读证明、成绩单流程和 2025 版培养方案修读说明。
- 前 5 份已入库；2025 版修读说明仅供参考，没有混入 2023 级专业问答。文件适用对象与现行效力仍需按原文核对。
- 库内另有 4 份明确标注的虚构示例。页面默认排除示例，正式实验也排除。
- 共 11 份文档、134 个片段，其中 7 份实际资料。BGE 小型中文模型在 CPU 上实际生成 512 维向量，当前默认采用语义与 TF-IDF 混合加问题重点规则重排序；原RRF和纯向量策略仍可选择。
- 已导入用户提供的 2022 版培养方案（确认 2023 级沿用）和教师/教务来源课程大纲；文件未写明的信息仍提示无法确认。

来源、原件路径、获取日期和哈希见 `materials/manifest.json` 与 `materials/资料来源清单.md`。

## 接入真实大模型

打开“模型与检索设置”：

1. 点击“填入 DeepSeek 配置示例”。本次根据官方文档填写 `https://api.deepseek.com` 和 `deepseek-flash`；如以后变更，以官方文档和模型列表为准。
2. 在 [官方控制台](https://platform.deepseek.com/)本人完成账号验证、创建密钥，并按控制台要求准备可用额度。
3. 将密钥填入系统页面，点击“检测 API 连接”。检测只读取模型列表，不发送校园资料、不调用生成接口。
4. 勾选“开启大模型回答”，保存后提问。核对答案中的引用和右侧原文。

密钥可仅在进程使用，也可勾选 Windows 本机加密保存；当前已加密保存，启动可读取。不写入配置明文、备份或读取配置的响应。换电脑需重新填写；无需把密钥发到聊天里。基础地址后由程序拼接 `/chat/completions`，不要填完整聊天接口路径。

也可连接其他兼容 Chat Completions 的服务，或已经准备好的本机接口，例如 `http://127.0.0.1:11434/v1`。当前没有安装本机生成模型。外部调用失败会明确报错，不会将摘录冒充模型生成。

配置依据：[DeepSeek 首次调用](https://api-docs.deepseek.com/)、[思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)。生成质量仍需真实调用和人工评价，编号有效不代表结论得到支持。

## 文档处理

| 格式 | 处理方式 |
|---|---|
| PDF | 保留 PDF 文件页序号，不跨页切片；文字极少的页面可用本地 OCR |
| DOCX | 依次读取段落、表格；不虚构 Word 页码 |
| HTML | 只读取正文、标题与表格，不执行脚本；图片课程地图不进入文字检索 |
| TXT / Markdown | UTF-8 或 GB18030，按标题和句子切片 |

默认切片 400 字符、重叠最多 60 字符。这是字符数，不是 token 数；当前语料最长片段为 376 个模型 token，低于已选模型的 512 上限。文件上限 10 MB、提取文字 50 万字符、PDF 300 页；每文件最多处理 20 个扫描页。

OCR 已准备 RapidOCR/ONNX 模型，扫描 PDF 测试实际通过。复杂表格、公式、数字及图文顺序仍需检查。上传时填写专业、入学年级、版本、来源、发布日期；“仅参考”文件不进入问答。结构化字段不能替代原文适用条款核对。

## 换电脑安装和恢复

使用 Python 3.12，打开项目目录中的 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-semantic.txt -r requirements-ocr.txt -r requirements-analysis.txt
.\.venv\Scripts\python.exe scripts\download_embedding.py
.\.venv\Scripts\python.exe scripts\bootstrap_knowledge.py
.\.venv\Scripts\python.exe run.py
```

下载依赖与首次准备模型需要联网。OCR 首次使用会下载约 30 MB 模型；当前电脑已下载。Embedding 约 96 MB，模型 revision 和来源保存在模型目录。程序不执行模型远程自定义代码。

精确复现本次依赖可替换安装命令为：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt --extra-index-url https://download.pytorch.org/whl/cpu
```

从备份恢复时先关闭系统，把压缩包内 `data` 文件夹恢复到项目。文档原件路径按当前项目重定位；进入设置重新填写本机模型路径、生成接口与密钥，向量索引可重建。备份不包含模型、API 密钥或聊天记录。下载BGE并安装语义依赖后，在设置中选择semantic、本地模型目录、hybrid_rerank与阈值0.55，保存即可；默认下载固定本次验证的模型提交。

## 检查、实验与备份

```powershell
.\.venv\Scripts\python.exe scripts\run_checks.py
.\.venv\Scripts\python.exe scripts\evaluate_retrieval.py
.\.venv\Scripts\python.exe scripts\backup.py
```

真实生成对照入口已经准备好，先输出计划，不调用 API：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_answers.py --limit 19
```

只有添加 --run 才实际调用模型，密钥优先读取本机加密保存或受控环境变量，否则在终端隐藏输入。19 题最多 76 次生成请求，失败会停止并保存部分结果。程序在临时知识库快照上运行，不改页面配置；输出待人工评分的回答，不自动宣称准确率。本次授权批次已经结束，不需要重跑。

结果与限制见 [开发对照结果](docs/开发对照结果.md)，逐题回答见 [模型回答核对表](docs/模型回答核对表.md)。原始记录为 `evaluation/results/answers-20261013-153455.json`；修正通用成绩规则路由后另用2次调用，最终为 `answers-20261013-153455-corrected.json`，总计75次。语义组有1条未达到证据阈值的本地提示。直接回答组本轮缺少学校名称，后续脚本已修正，本轮不能用于公平的效果结论。

离线重新汇总和绘图不会发送请求：

```powershell
.\.venv\Scripts\python.exe scripts\summarize_answers.py --report evaluation\results\answers-20261013-153455-corrected.json
.\.venv\Scripts\python.exe scripts\plot_answers.py --summary evaluation\results\answers-20261013-153455-corrected-summary.json
```

19 题问题集已由用户核对，4 道无依据题已删除；该集已用于改进检索规则。脚本默认拒绝把未经人工核对的题目当正式数据，`--allow-draft` 的输出明确标注草稿。当前结果不能当独立测试结论，更不能当生成答案准确率。

另有20道专业候选题：`evaluation/professional_questions.candidate.json`，说明和参考答案见 [专业候选问题核对表](docs/专业候选问题核对表.md)。只做了原文匹配，尚未人工审核，没有用于检索实验或模型请求；复核后再决定测试用途。

脚本比较 TF-IDF、语义、原混合与规则重排序，记录片段 Recall@4、MRR、误拒答与检索 P95。未含无依据题时不计算无依据拒答率。正式实验需人工核对题目，补充未参与调参的新题，冻结配置，再用同一模型比较直接回答与各 RAG 方案。人工评分模板：`evaluation/人工评分模板.md`；页面“回答核对”可保存独立用户评分，助手初评仅作参考。

原开发电脑保留20题旧快照 `output/frozen/20261013-183034`，计划最多80次请求，发送0次。它对应旧代码，不纳入GitHub仓库；本次更新后不能直接执行旧快照。标签审核后需重新冻结并另行明确本批次额度。只查看草稿计划可运行：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_answers.py --dataset evaluation\professional_questions.candidate.json --allow-draft --limit 20
```

冻结计划统一学校、专业与年级背景，平衡执行顺序并保存种子；模型、顺序或代码变化需重新冻结。本命令没有 `--run`，不会调用模型。

网页“检索检查”当前针对 19 道已核对开发题，不调用大模型。API 文档：<http://127.0.0.1:8765/docs>。

## 项目文件

| 路径 | 内容 |
|---|---|
| `app.py` / `run.py` | FastAPI 问答流程与启动入口 |
| `campus/documents.py` / `ocr.py` | 解析、切片、扫描页 OCR |
| `campus/knowledge.py` | SQLite、TF-IDF、语义与混合检索 |
| `campus/providers.py` | API 连接检测、生成与引用编号检查 |
| `campus/conversations.py` | 本机会话数据库与规则追问改写 |
| `campus/ingestion.py` | 导入成功、重复与失败记录 |
| `static/chat-history.js` | SSE增量读取、会话恢复与删除 |
| `scripts/bootstrap_knowledge.py` | 从仓库原件恢复知识库，不调用模型 |
| `scripts/run_checks.py` | 隔离真实配置的自动功能检查 |
| `docs/verification/v0.5.0.json` | 本轮44项测试与浏览器验收摘要 |
| `static/` | 问答、来源、知识库与配置界面 |
| `materials/` | 官方原件、来源清单、待补充文件 |
| `models/` | 本地模型和下载记录，不纳入版本控制 |
| `evaluation/` | 开发问题集、评分模板、实验输出 |
| `data/` | 文档库、上传原件、向量、非敏感配置 |
| `output/backups/` | 数据库与原件备份 |
| `docs/课题完善清单.md` | 26 项问题、解决办法和需要你做的事 |
| `docs/技术方案.md` | 当前架构、模块、实验设计 |
| `docs/文献阅读清单.md` | 原始论文与官方链接 |
| `docs/原型验证记录.md` | 带日期的实际验证记录 |

当前仅监听本机，适合个人毕业设计演示；未实现多人账号权限与服务器任务队列。聊天记录保存在本机独立会话数据库，不纳入Git或普通资料备份。SQLite 与直接余弦检索适用于当前小型资料库，大规模扩展时再引入专门向量库和增量索引。

原接入验证见 docs/本次接入与资料处理记录.md；本轮验证与剩余事项见 docs/整体优化记录.md 和 output/optimization-verification.json。检索、用量和耗时图表在 output/figures，属于开发记录。备份恢复演练已通过，属于本机独立目录验证。
