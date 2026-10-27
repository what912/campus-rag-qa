"""Campus RAG prototype. All durable data stays inside the workspace's data folder."""
import json
import os
import re
import threading
import time
from contextlib import asynccontextmanager,nullcontext
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse,StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from campus.documents import MAX_BYTES
from campus.knowledge import CATEGORIES, KnowledgeBase
from campus.providers import generate_answer, validate_base_url, test_provider,stream_answer,answer_warnings
from campus.credentials import load_key, save_key, clear_key, same_origin
from campus.routing import infer_category
from campus.evaluation import evaluate_cases
from campus.experiments import list_reports,read_report,read_review,save_review
from campus.conversations import ConversationStore,resolve_question
from campus.ingestion import ImportLog
from campus.admissions import infer_admission_scope,choose_admission_year,describe_scope,PROGRAMS

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('CAMPUS_DATA_DIR', str(ROOT / 'data')))


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    category: str = ''
    include_samples: bool = True
    top_k: int = Field(default=4, ge=1, le=8)
    cohort: str | None = Field(default=None, max_length=50)
    major: str | None = Field(default=None, max_length=150)
    answer_mode:Literal['auto','extractive']='auto'
    session_id:str|None=Field(default=None,pattern=r'^[a-f0-9]{32}$')


class SettingsRequest(BaseModel):
    enabled: bool = False
    base_url: str = Field(default='', max_length=500)
    model: str = Field(default='', max_length=200)
    api_key: str | None = Field(default=None, max_length=2000)
    clear_key: bool = False
    remember_key: bool = False
    embedding_mode: str = 'tfidf'
    model_path: str = Field(default='', max_length=1000)
    min_score: float = Field(default=0.08, ge=0, le=1)
    query_prefix: str = Field(default='', max_length=300)
    retrieval_strategy: str = 'vector'
    enable_ocr: bool = False
    target_cohort: str = Field(default='2023', max_length=50)
    target_major: str = Field(default='计算机科学与技术', max_length=150)


class ReviewRow(BaseModel):
    id:str=Field(max_length=100)
    mode:Literal['direct','tfidf','semantic','hybrid']
    correctness:int|None=Field(default=None,ge=0,le=2)
    evidence:Literal['supported','partial','unsupported']|None=None
    citation:Literal['supported','partial','unsupported']|None=None
    notes:str=Field(default='',max_length=3000)


class ReviewRequest(BaseModel):
    reviewer:str=Field(min_length=1,max_length=100)
    rows:list[ReviewRow]=Field(max_length=800)


def create_app(data_dir=DATA, seed=True):
    data_dir=Path(data_dir)
    kb = KnowledgeBase(Path(data_dir))
    conversations=ConversationStore(data_dir)
    imports=ImportLog(data_dir)
    if seed:
        kb.seed(ROOT / 'samples')
    settings_path = Path(data_dir) / 'settings.json'
    settings = {'enabled': False, 'base_url': '', 'model': '', 'api_key': '', 'remember_key':False,
                'embedding_mode': 'tfidf', 'model_path': '', 'min_score': 0.08,
                'query_prefix':'', 'retrieval_strategy':'vector', 'enable_ocr':False,
                'target_cohort':'2023', 'target_major':'计算机科学与技术'}
    startup_warning = ''
    if settings_path.exists():
        try:
            persisted = SettingsRequest(**json.loads(settings_path.read_text(encoding='utf-8'))).model_dump()
            settings.update({k: persisted[k] for k in settings if k != 'api_key'})
            kb.rebuild(mode=settings['embedding_mode'], model_path=settings['model_path'],
                       query_prefix=settings['query_prefix'])
        except (ValueError, OSError) as exc:
            settings['embedding_mode'] = 'tfidf'
            settings['model_path'] = ''
            startup_warning = '已恢复默认检索模式，请重新检查配置。' + str(exc)
    if settings['remember_key']:
        try:
            settings['api_key'] = load_key(data_dir, settings['base_url'])
        except (ValueError, OSError):
            startup_warning += ' 本机加密密钥无法读取，请重新填写。'
    settings['api_key'] = os.environ.get('CAMPUS_LLM_API_KEY') or settings['api_key']
    if os.environ.get('CAMPUS_LLM_BASE_URL'):
        settings.update(base_url=os.environ['CAMPUS_LLM_BASE_URL'],
                        model=os.environ.get('CAMPUS_LLM_MODEL', ''), enabled=True)
    kb.save_index()
    kb.enable_ocr = settings['enable_ocr']
    config_lock = threading.RLock()

    @asynccontextmanager
    async def lifespan(application):
        yield

    api = FastAPI(title='知校 · 校园知识问答', version='0.7.0', lifespan=lifespan)
    api.state.kb = kb
    api.state.settings = settings
    api.state.conversations=conversations

    @api.get('/api/health')
    def health():
        return {'status': 'ok'}

    @api.get('/api/status')
    def status():
        with config_lock:
            return dict(kb.stats(), llm_enabled=settings['enabled'],
                        llm_model=settings['model'] if settings['enabled'] else '',
                        categories=CATEGORIES, startup_warning=startup_warning,
                        target_cohort=settings['target_cohort'], target_major=settings['target_major'],
                        retrieval_strategy=settings['retrieval_strategy'], ocr_enabled=kb.enable_ocr)

    @api.get('/api/documents')
    def documents():
        return kb.documents()

    @api.get('/api/imports')
    def import_records():
        return imports.list()

    @api.get('/api/sessions')
    def sessions():
        return conversations.list()

    @api.post('/api/sessions',status_code=201)
    def new_session():
        try: return {'id':conversations.create()}
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc

    @api.get('/api/sessions/{identifier}')
    def read_session(identifier:str):
        try: return conversations.get(identifier)
        except ValueError as exc: raise HTTPException(404,str(exc)) from exc

    @api.delete('/api/sessions/{identifier}')
    def delete_session(identifier:str):
        try:
            if not conversations.delete(identifier): raise HTTPException(404,'会话不存在。')
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc
        return {'message':'本机会话和回答记录已删除。'}

    @api.post('/api/documents', status_code=201)
    def upload(file: UploadFile = File(...), category: str = Form('其他'),
               version: str = Form(''), source: str = Form(''), major: str = Form('', max_length=150),
               cohort: str = Form('', max_length=50), published_at: str = Form('', max_length=50),
               reference_only: bool = Form(False),admission_year:str=Form('',max_length=4),
               admission_program:str=Form('',max_length=80)):
        data = file.file.read(MAX_BYTES + 1)
        try:
            metadata={'major':major,'cohort':cohort,'published_at':published_at,'reference_only':reference_only}
            if category=='招生信息':
                if admission_year and not re.fullmatch(r'20\d{2}',admission_year):raise ValueError('招生年份须按正文填写四位年份。')
                if admission_program and admission_program not in PROGRAMS:raise ValueError('请选择有效的招生项目。')
                if not reference_only and (not admission_year or not admission_program):
                    raise ValueError('招生资料请填写正文招生年份和项目；无法确定时勾选仅参考。')
                metadata.update(domain='admissions',admission_year=admission_year,admission_program=admission_program,
                                admission_level=PROGRAMS[admission_program][1] if admission_program else '',major='',cohort='')
            document_id = kb.add(file.filename or 'document.txt', data, category, version, source,
                                 metadata=metadata)
        except ValueError as exc:
            imports.record(file.filename or 'document.txt','failed',message=str(exc))
            raise HTTPException(400, str(exc)) from exc
        document=kb.document(document_id)
        imports.record(file.filename or 'document.txt','completed',document_id=document_id,
                       chunks=len(document['chunks']),extraction_method=document['metadata']['extraction_method'],
                       message='解析、切片与索引已完成。')
        return {'id': document_id, 'message': '文件已解析并建立索引。'}

    @api.get('/api/documents/{document_id}')
    def document(document_id: str):
        result = kb.document(document_id)
        if result is None:
            raise HTTPException(404, '文档不存在。')
        result.pop('file_path')
        return result

    @api.get('/api/documents/{document_id}/download')
    def download(document_id: str):
        result = kb.document(document_id)
        if result is None:
            raise HTTPException(404, '文档不存在。')
        return FileResponse(result['file_path'], filename=result['name'],
                            media_type='application/octet-stream')

    @api.delete('/api/documents/{document_id}')
    def delete(document_id: str):
        if not kb.delete(document_id):
            raise HTTPException(404, '文档不存在。')
        return {'message': '文档和对应索引已删除。'}

    def prepare_question(request):
        question = request.question.strip()
        if not question:
            raise HTTPException(400, '请输入问题。')
        if request.category and request.category not in CATEGORIES:
            raise HTTPException(400, '无效的分类。')
        start = time.perf_counter()
        turns=[]
        if request.session_id:
            try: turns=conversations.get(request.session_id)['turns']
            except ValueError as exc: raise HTTPException(404,str(exc)) from exc
            if len(turns)>=100: raise HTTPException(409,'单个会话最多100轮，请新建会话。')
        resolution=resolve_question(question,turns)
        resolved=resolution['query']
        inherited_admission=(resolution['context'] or {}).get('admission_scope',{})
        if inherited_admission.get('domain') and inherited_admission.get('year') and infer_admission_scope(resolved)['domain'] and not infer_admission_scope(resolved)['year']:
            resolved=inherited_admission['year']+'年'+resolved
        with config_lock:
            active = dict(settings)
            context=resolution['context'] or {}
            explicit_year = re.search(r'(20\d{2})\s*(?:级|年入学)', resolved)
            cohort = request.cohort if request.cohort is not None else (
                explicit_year.group(1) if explicit_year else context.get('cohort',active['target_cohort']))
            named_major=next((m for m in ('计算机科学与技术','软件工程','网络空间安全') if m+'专业' in resolved),'')
            major = request.major if request.major is not None else named_major or context.get('major',active['target_major'])
            category = infer_category(resolved,request.category)
            admission_scope=choose_admission_year(kb.rows,infer_admission_scope(resolved,category))
            if admission_scope.get('ambiguous'):
                resolution['clarification']='这次问题涉及不同招生层次，请分别提问，或说明需要比较的具体项目、年份和条件，以免套用不同规定。'
            sources = [] if resolution['clarification'] else kb.search(resolved, request.top_k, category,
                                request.include_samples, active['min_score'], cohort, major,
                                active['retrieval_strategy'])
            retrieval_mode = kb.mode
            reference_notice=''
            outline=re.search(r'(?<!\d)(\d{3})(?!\d)',resolved) if re.search(r'考试大纲|考纲',resolved) else None
            if admission_scope['domain'] and not sources and outline:
                reference_notice='当前检索未找到'+outline.group(1)+'科目的完整考试大纲，无法确认具体考试范围；专业目录中的科目名称不能代替大纲正文。'
            elif admission_scope['domain'] and not sources and any(
                row['metadata'].get('reference_only') and row['metadata'].get('domain')=='admissions'
                and row['metadata'].get('admission_program')==admission_scope.get('program') for row in kb.rows):
                reference_notice='库内有该项目的旧版或整理稿，仅作为参考保存，不能据此确认当前招生政策；可在知识库查看原件与说明。'
        retrieval_ms = round((time.perf_counter() - start) * 1000)
        if admission_scope['domain']:
            scoped=resolved+'\n用户所在学校：中国传媒大学。当前招生资料范围：'+describe_scope(admission_scope)+'。'
        else:
            scoped=resolved+f'\n用户所在学校：中国传媒大学。用户选择的适用范围：{cohort or "未指定"}级，{major or "未指定专业"}。'
        return {'question':question,'resolved_question':resolved,'query_rewritten':resolution['rewritten'],
                'clarification':resolution['clarification'],'sources':sources,'active':active,'start':start,
                'retrieval_ms':retrieval_ms,'retrieval_mode':retrieval_mode,'cohort':cohort,'major':major,
                'admission_scope':admission_scope,'scoped':scoped,'reference_notice':reference_notice}

    def local_answer(prepared):
        sources=prepared['sources']
        if prepared['clarification']:
            return prepared['clarification'],'clarification'
        if not sources:
            if prepared.get('reference_notice'):return prepared['reference_notice'],'no_evidence'
            answer = '当前检索未找到足够依据，暂时无法确认。可换一种问法，或说明专业、年级和文件版本；相关资料尚未入库时再补充文件。'
            return answer,'no_evidence'
        answer='本次显示检索原文摘录，请根据原文核对答案。\n\n'+'\n\n'.join(
            f"[{s['citation']}] {s['text']}" for s in sources)
        return answer,'extractive'

    def finish_answer(prepared,answer,mode,warnings,generation_ms,session_id,telemetry=None):
        sources=prepared['sources'];active=prepared['active']
        if any(s['sample'] for s in sources):
            warnings.insert(0, '本次依据包含虚构演示资料，不代表中国传媒大学的正式规定。')
        if any(s['metadata'].get('extraction_method') == 'ocr' for s in sources):
            warnings.append('部分原文由 OCR 识别，涉及数字、课程表或条件时请核对原文件。')
        if any(s['metadata'].get('review_note') for s in sources):
            warnings.append('请对照来源文件的指定版本、适用范围和后续修订。')
        if any(s['metadata'].get('contains_images') for s in sources):
            warnings.append('HTML 中的图片和课程地图未进入文字检索，涉及图中关系时请核对原网页。')
        result={'answer': answer, 'sources': sources, 'mode': mode, 'warnings': warnings,
                'question':prepared['question'],'resolved_question':prepared['resolved_question'],
                'query_rewritten':prepared['query_rewritten'],'session_id':session_id,
                'elapsed_ms': round((time.perf_counter() - prepared['start']) * 1000),
                'retrieval_ms':prepared['retrieval_ms'],'generation_ms':generation_ms,
                'retrieval_mode':prepared['retrieval_mode'],'retrieval_strategy':active['retrieval_strategy'],
                'cohort':prepared['cohort'],'major':prepared['major'],'provider':telemetry or {}}
        result['admission_scope']=prepared['admission_scope']
        if session_id: conversations.append(session_id,prepared['question'],result)
        return result

    @api.post('/api/ask')
    def ask(request: AskRequest):
        try:
            with conversations.answering(request.session_id) if request.session_id else nullcontext():
                prepared=prepare_question(request);started=time.perf_counter();warnings=[]
                if prepared['sources'] and prepared['active']['enabled'] and request.answer_mode=='auto':
                    try: answer,warnings=generate_answer(prepared['scoped'],prepared['sources'],prepared['active'])
                    except ValueError as exc: raise HTTPException(502,str(exc)) from exc
                    mode='rag'
                else: answer,mode=local_answer(prepared)
                return finish_answer(prepared,answer,mode,warnings,
                    round((time.perf_counter()-started)*1000) if mode=='rag' else 0,request.session_id)
        except ValueError as exc: raise HTTPException(400,str(exc)) from exc

    @api.post('/api/ask/stream')
    def ask_stream(request:AskRequest):
        if request.session_id:
            try: conversations.get(request.session_id)
            except ValueError as exc: raise HTTPException(404,str(exc)) from exc
        def event(name,data):
            return 'event: '+name+'\ndata: '+json.dumps(data,ensure_ascii=False)+'\n\n'
        def stream():
            try:
                with conversations.answering(request.session_id) if request.session_id else nullcontext():
                    yield event('status',{'message':'正在检索原文…'})
                    prepared=prepare_question(request);started=time.perf_counter();telemetry={}
                    yield event('sources',{'sources':prepared['sources'],'resolved_question':prepared['resolved_question'],
                                           'query_rewritten':prepared['query_rewritten'],'admission_scope':prepared['admission_scope']})
                    if prepared['sources'] and prepared['active']['enabled'] and request.answer_mode=='auto':
                        parts=[]
                        for delta in stream_answer(prepared['scoped'],prepared['sources'],prepared['active'],telemetry=telemetry):
                            parts.append(delta);yield event('delta',{'text':delta})
                        answer=''.join(parts).strip();mode='rag'
                        warnings=answer_warnings(answer,prepared['sources'],telemetry.get('finish_reason'))
                    else:
                        answer,mode=local_answer(prepared);warnings=[]
                        yield event('delta',{'text':answer})
                    result=finish_answer(prepared,answer,mode,warnings,
                        round((time.perf_counter()-started)*1000) if mode=='rag' else 0,request.session_id,telemetry)
                    yield event('done',result)
            except (ValueError,HTTPException) as exc:
                yield event('error',{'message':exc.detail if isinstance(exc,HTTPException) else str(exc),'incomplete':True})
        return StreamingResponse(stream(),media_type='text/event-stream',
                                 headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

    @api.get('/api/settings')
    def get_settings():
        with config_lock:
            return {**{k: v for k, v in settings.items() if k != 'api_key'},
                    'has_key': bool(settings['api_key'])}

    @api.put('/api/settings')
    def update_settings(request: SettingsRequest):
        values = request.model_dump(exclude={'api_key', 'clear_key'})
        if values['retrieval_strategy'] not in ('vector', 'hybrid','hybrid_rerank'):
            raise HTTPException(400, '请选择向量、混合或混合重排序检索。')
        if values['enabled']:
            try:
                values['base_url'] = validate_base_url(values['base_url'])
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            if not values['model'].strip():
                raise HTTPException(400, '开启大模型时，需要填写模型名称。')
        with config_lock:
            next_key = '' if request.clear_key else (
                request.api_key.strip() if request.api_key and request.api_key.strip() else (
                    settings['api_key'] if same_origin(values['base_url'], settings['base_url']) else ''))
            if values['remember_key'] and next_key and not request.clear_key:
                try:
                    validate_base_url(values['base_url'])
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from exc
            try:
                kb.rebuild(mode=values['embedding_mode'], model_path=values['model_path'],
                           query_prefix=values['query_prefix'])
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            if values['remember_key'] and next_key and not request.clear_key:
                try:
                    save_key(data_dir, next_key, values['base_url'])
                except (ValueError, OSError) as exc:
                    raise HTTPException(400, '本机加密保存失败，请取消保存密钥后重试。') from exc
            else:
                clear_key(data_dir)
            settings.update(values)
            kb.enable_ocr = values['enable_ocr']
            settings['api_key'] = next_key
            # No plaintext credential is persisted or returned by GET.
            settings_path.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding='utf-8')
            kb.save_index()
        return {'message': '配置已保存，索引已更新。' + ('密钥已用 Windows 本机加密保存。' if values['remember_key'] and settings['api_key'] else '密钥仅在本次运行中有效。')}

    @api.post('/api/model/test')
    def test_model(request: SettingsRequest):
        values = request.model_dump()
        with config_lock:
            values['api_key'] = request.api_key.strip() if request.api_key and request.api_key.strip() else (
                settings['api_key'] if same_origin(values['base_url'], settings['base_url']) else '')
        try:
            return test_provider(values)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @api.get('/api/evaluation')
    def evaluation(top_k: int = 4, dataset: str = 'development'):
        if not 1 <= top_k <= 8:
            raise HTTPException(400, 'Top K 需要在 1–8 之间。')
        if dataset not in ('development','samples'):
            raise HTTPException(400, '题集需要选择 development 或 samples。')
        development = ROOT / 'evaluation/public_questions.development.json'
        use_development = dataset == 'development' and development.is_file()
        if use_development:
            dataset = json.loads(development.read_text(encoding='utf-8'))
            cases = dataset['cases']
        else:
            cases = json.loads((ROOT / 'samples' / 'evaluation.json').read_text(encoding='utf-8'))
        with config_lock:
            result=evaluate_cases(kb,cases,top_k=top_k,threshold=settings['min_score'],
                strategy=settings['retrieval_strategy'],cohort=settings['target_cohort'],
                major=settings['target_major'],include_samples=not use_development)
        result['note']=('开发题检查：正确文件与正确证据分开评价，不调用模型，不代表答案准确率。'
                        '无依据题未包含时拒答率为空。' if use_development else '虚构示例检索检查，不是正式实验。')
        return result

    @api.get('/api/experiments')
    def experiments():
        return list_reports(ROOT)

    @api.get('/api/experiments/{filename}')
    def experiment(filename:str):
        try:
            report=read_report(ROOT,filename)
            report['review']=read_review(data_dir,report)
            return report
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc

    @api.put('/api/experiments/{filename}/review')
    def review_experiment(filename:str,request:ReviewRequest):
        try:
            report=read_report(ROOT,filename)
            with config_lock:
                return save_review(data_dir,report,request.reviewer,[row.model_dump() for row in request.rows])
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc

    api.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')

    @api.get('/', include_in_schema=False)
    def index():
        return FileResponse(ROOT / 'static' / 'index.html')

    return api


app = create_app()
