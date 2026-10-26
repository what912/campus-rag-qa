"""Small local vector index with an explicit lexical baseline and optional neural embeddings."""
import hashlib
import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer

from .documents import parse_document, chunk_pages
from .search_features import focus_query,intent_match
from .admissions import infer_admission_scope,scope_matches,choose_admission_year

CATEGORIES = ['培养方案', '课程大纲', '办事流程', '校园服务', '招生信息', '其他']


class KnowledgeBase:
    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.uploads = directory / 'uploads'
        self.uploads.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.mode = 'tfidf'
        self.model_path = ''
        self.model = None
        self.rows = []
        self.matrix = None
        self.vectorizer = None
        self.dimension = 0
        self.query_prefix = ''
        self.enable_ocr = False
        self.lexical_matrix = None
        self.lexical_features = []
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
                    version TEXT NOT NULL, source TEXT NOT NULL, sample INTEGER NOT NULL,
                    sha256 TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL, file_path TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL, text TEXT NOT NULL, page INTEGER, heading TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
            ''')
            if 'metadata' not in [r['name'] for r in db.execute('PRAGMA table_info(documents)')]:
                db.execute("ALTER TABLE documents ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
            for doc in db.execute('SELECT id,file_path FROM documents').fetchall():
                restored = self.uploads / Path(doc['file_path'].replace('\\', '/')).name
                if restored.is_file() and str(restored) != doc['file_path']:
                    db.execute('UPDATE documents SET file_path=? WHERE id=?', (str(restored), doc['id']))
        self.rebuild()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.directory / 'knowledge.db')
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def rebuild(self, db=None, mode=None, model_path=None, query_prefix=None):
        with self.lock:
            if db is None:
                with self.connect() as connection:
                    return self.rebuild(connection, mode, model_path, query_prefix)
            next_mode = mode or self.mode
            next_path = self.model_path if model_path is None else model_path
            next_prefix = self.query_prefix if query_prefix is None else query_prefix
            rows = [dict(r) for r in db.execute('''SELECT c.*, d.name, d.category,
                d.version, d.source, d.sample, d.metadata FROM chunks c JOIN documents d ON c.document_id=d.id
                ORDER BY d.created_at, c.ordinal''')]
            for row in rows:
                row['metadata'] = json.loads(row['metadata'])
            texts = [r['text'] for r in rows]
            model, vectorizer, matrix, dimension = None, None, None, 0
            if next_mode == 'semantic':
                if not next_path or not Path(next_path).is_dir():
                    raise ValueError('请填写已下载的本地语义模型文件夹；系统不会自动下载模型。')
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as exc:
                    raise ValueError('尚未安装语义模型依赖，请按 README 安装 requirements-semantic.txt。') from exc
                try:
                    model = (self.model if self.mode == next_mode and self.model_path == next_path
                             else SentenceTransformer(next_path, device='cpu', local_files_only=True, trust_remote_code=False))
                    model.max_seq_length = min(model.max_seq_length, 512)
                    dimension = (model.get_embedding_dimension() if hasattr(model, 'get_embedding_dimension')
                                 else model.get_sentence_embedding_dimension())
                    if texts:
                        matrix = model.encode_document(texts, normalize_embeddings=True,
                                                       show_progress_bar=False, prompt='', batch_size=16)
                except Exception as exc:
                    raise ValueError('本地模型加载或编码失败，请检查模型格式和依赖。') from exc
            elif next_mode == 'tfidf':
                if texts:
                    vectorizer = TfidfVectorizer(analyzer='char', ngram_range=(2, 3),
                                               max_features=30_000, sublinear_tf=True)
                    try:
                        matrix = vectorizer.fit_transform(texts)
                        dimension = matrix.shape[1]
                    except ValueError as exc:
                        raise ValueError('文档内容过短，无法建立索引。') from exc
            else:
                raise ValueError('未知向量模式。')
            lexical_matrix = None
            if next_mode == 'semantic' and texts:
                vectorizer = TfidfVectorizer(analyzer='char', ngram_range=(2, 3),
                                            max_features=30_000, sublinear_tf=True)
                lexical_matrix = vectorizer.fit_transform(texts)
            self.rows, self.model, self.vectorizer = rows, model, vectorizer
            self.matrix, self.dimension = matrix, dimension
            self.mode, self.model_path = next_mode, next_path
            self.query_prefix = next_prefix
            self.lexical_matrix = lexical_matrix
            analyzer = vectorizer.build_analyzer() if vectorizer is not None else None
            self.lexical_features = [set(analyzer(text)) for text in texts] if analyzer else []

    def save_index(self):
        """Export a rebuildable index. Startup rebuilds from SQLite rather than unpickling files."""
        with self.lock:
            if self.matrix is not None:
                if sparse.issparse(self.matrix):
                    sparse.save_npz(self.directory / 'vectors.npz', self.matrix)
                else:
                    np.savez_compressed(self.directory / 'vectors.npz', vectors=self.matrix)
            else:
                (self.directory / 'vectors.npz').unlink(missing_ok=True)
            info = {'mode': self.mode, 'dimension': int(self.dimension or 0),
                    'chunk_ids': [r['id'] for r in self.rows],
                    'vocabulary': {key: int(value) for key, value in self.vectorizer.vocabulary_.items()}
                    if self.vectorizer is not None else None}
            (self.directory / 'index.json').write_text(json.dumps(info, ensure_ascii=False), encoding='utf-8')

    def add(self, filename, content, category='其他', version='', source='', sample=False, metadata=None):
        filename = Path(filename.replace('\\', '/')).name[:180]
        if category not in CATEGORIES:
            raise ValueError('请选择有效的文档分类。')
        digest = hashlib.sha256(content).hexdigest()
        with self.lock:
            with self.connect() as db:
                duplicate = db.execute('SELECT id FROM documents WHERE sha256=?', (digest,)).fetchone()
                if duplicate:
                    raise ValueError('这份文件已经在知识库中，无需重复导入。')
                pages = parse_document(filename, content, enable_ocr=self.enable_ocr)
                allowed = {'faculty', 'major', 'cohort', 'published_at', 'review_note', 'reference_only', 'authority',
                           'domain','admission_year','admission_level','admission_program','original_filename',
                           'conversion_method','original_sha256','ocr_text_layer','actual_title'}
                meta = {key:value for key,value in (metadata or {}).items() if key in allowed}
                meta['extraction_method'] = 'ocr' if meta.get('ocr_text_layer') or any(p.get('extraction_method') == 'ocr' for p in pages) else 'text'
                if any(p.get('contains_images') for p in pages):
                    meta['contains_images'] = True
                chunks = chunk_pages(pages)
                if not chunks:
                    raise ValueError('文件没有可切分的文本。')
                document_id = uuid.uuid4().hex
                path = self.uploads / (document_id + Path(filename).suffix.lower())
                path.write_bytes(content)
                try:
                    db.execute('''INSERT INTO documents
                        (id,name,category,version,source,sample,sha256,created_at,file_path,metadata)
                        VALUES (?,?,?,?,?,?,?,?,?,?)''',
                               (document_id, filename, category, version[:100], source[:1000], int(sample),
                                digest, datetime.now(timezone.utc).isoformat(), str(path),
                                json.dumps(meta, ensure_ascii=False)))
                    db.executemany('INSERT INTO chunks VALUES (?,?,?,?,?,?)',
                                   [(uuid.uuid4().hex, document_id, c['ordinal'], c['text'],
                                     c['page'], c['heading']) for c in chunks])
                    self.rebuild(db)
                except Exception:
                    path.unlink(missing_ok=True)
                    raise
            self.save_index()
            return document_id

    def seed(self, samples: Path):
        with self.lock:
            with self.connect() as db:
                if db.execute("SELECT 1 FROM metadata WHERE key='seeded'").fetchone():
                    return
            for path in sorted(samples.glob('*.md')):
                category = path.stem.split('_', 1)[-1]
                try:
                    self.add(path.name, path.read_bytes(), category, '虚构演示版',
                             '项目内置示例，非学校官方文件', True)
                except ValueError as exc:
                    if '已经' not in str(exc):
                        raise
            with self.connect() as db:
                db.execute("INSERT OR REPLACE INTO metadata VALUES ('seeded', '1')")

    def documents(self):
        with self.lock, self.connect() as db:
            docs = [dict(r) for r in db.execute('''SELECT d.id,d.name,d.category,d.version,d.source,
                d.sample,d.created_at,d.sha256,d.metadata,count(c.id) chunk_count FROM documents d
                LEFT JOIN chunks c ON c.document_id=d.id GROUP BY d.id ORDER BY d.created_at DESC''')]
            for doc in docs:
                doc['metadata'] = json.loads(doc['metadata'])
            return docs

    def document(self, document_id):
        with self.lock, self.connect() as db:
            row = db.execute('SELECT * FROM documents WHERE id=?', (document_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result['metadata'] = json.loads(result['metadata'])
            result['chunks'] = [dict(r) for r in db.execute(
                'SELECT * FROM chunks WHERE document_id=? ORDER BY ordinal', (document_id,))]
            return result

    def delete(self, document_id):
        with self.lock:
            document = self.document(document_id)
            if document is None:
                return False
            with self.connect() as db:
                db.execute('DELETE FROM documents WHERE id=?', (document_id,))
                self.rebuild(db)
            Path(document['file_path']).unlink(missing_ok=True)
            self.save_index()
            return True

    def search(self, query, top_k=4, category='', include_samples=True, min_score=0.08,
               cohort='', major='', strategy='vector'):
        with self.lock:
            if self.matrix is None or not self.rows:
                return []
            if self.mode == 'semantic':
                vector = self.model.encode_query([query], normalize_embeddings=True,
                                                 show_progress_bar=False, prompt=self.query_prefix)[0]
                scores = self.matrix @ vector
            else:
                vector = self.vectorizer.transform([query])
                scores = (self.matrix @ vector.T).toarray().ravel()
            admission_scope=choose_admission_year(self.rows,infer_admission_scope(query,category))
            def applies(row):
                meta = row['metadata']
                years = re.findall(r'20\d{2}', str(meta.get('cohort', '')))
                return (not meta.get('reference_only')
                        and scope_matches(meta,admission_scope)
                        and (not cohort or not years or cohort in years)
                        and (not major or not meta.get('major') or major == meta['major']))
            lexical_scores = None
            query_terms = set(self.vectorizer.build_analyzer()(query)) if self.vectorizer is not None else set()
            coverage = [len(query_terms & terms) / max(1, len(query_terms)) for terms in self.lexical_features]
            focused_scores = None
            focused_coverage=coverage
            if strategy in ('hybrid','hybrid_rerank') and self.mode == 'semantic':
                lexical_scores = (self.lexical_matrix @ self.vectorizer.transform([query]).T).toarray().ravel()
                if strategy == 'hybrid_rerank':
                    focused_scores=(self.lexical_matrix @ self.vectorizer.transform([focus_query(query)]).T).toarray().ravel()
                    if admission_scope['domain']:
                        focused_terms=set(self.vectorizer.build_analyzer()(focus_query(query)))
                        focused_coverage=[len(focused_terms & terms)/max(1,len(focused_terms)) for terms in self.lexical_features]
            eligible = [i for i, row in enumerate(self.rows)
                        if (not category or row['category'] == category)
                        and (include_samples or not row['sample'])
                        and applies(row)
                        and ((scores[i] >= min_score and (self.mode == 'semantic' or coverage[i] >= 0.12))
                             or (lexical_scores is not None and lexical_scores[i] >= 0.08 and coverage[i] >= 0.12)
                             or (focused_scores is not None and focused_scores[i] >= 0.08 and focused_coverage[i] >= (0.3 if admission_scope['domain'] else 0.12))
                             # A short, explicit clause label can have low cosine similarity in a long PDF.
                             # Exact labels admit candidates only after year/program hard filtering.
                             or (focused_scores is not None and admission_scope['domain']
                                 and intent_match(query,row['text'])==1.0))]
            ranking = {i:float(scores[i]) for i in eligible}
            if lexical_scores is not None:
                ranking = {i:0.0 for i in eligible}
                for values, threshold in ((scores, min_score), (lexical_scores, 0.08)):
                    ordered = sorted([i for i in eligible if values[i] >= threshold and
                                      (values is scores or coverage[i] >= 0.12)], key=lambda i:float(values[i]), reverse=True)
                    for rank, i in enumerate(ordered, 1):
                        ranking[i] += 1 / (60 + rank)
            if focused_scores is not None and eligible:
                # RRF remains one feature; detail wording and filename topic limit boilerplate dominance.
                analyzer=self.vectorizer.build_analyzer()
                topic={i:len(query_terms & set(analyzer(self.rows[i]['name']))) for i in eligible}
                peak_focus=max(float(focused_scores[i]) for i in eligible) or 1
                peak_topic=max(topic.values()) or 1
                peak_rrf=max(ranking.values()) or 1
                ranking={i:.25*float(focused_scores[i])/peak_focus+.20*topic[i]/peak_topic+
                         .15*ranking[i]/peak_rrf+.40*intent_match(query,self.rows[i]['text']) for i in eligible}
            eligible.sort(key=lambda i:ranking[i], reverse=True)
            return [dict(self.rows[i], score=round(float(scores[i]), 4), citation=n + 1,
                         focused_score=round(float(focused_scores[i]),4) if focused_scores is not None else None,
                         ranking_score=round(ranking[i], 6))
                    for n, i in enumerate(eligible[:top_k])]

    def stats(self):
        with self.lock:
            docs = self.documents()
            return {'documents': len(docs), 'chunks': len(self.rows), 'dimension': self.dimension,
                    'sample_documents': sum(d['sample'] for d in docs), 'mode': self.mode}
