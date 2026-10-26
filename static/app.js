'use strict';
const $ = (selector) => document.querySelector(selector);
const state = {documents: [], status: {}, busy: false, answers: [], deleting: null,sessionId:null};
let initialized = false;
let welcomeMarkup = $('#conversation').innerHTML;
let toastTimer;

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
}
async function api(path, options = {}) {
  let response;
  try { response = await fetch(path, options); }
  catch { throw new Error('无法连接本机服务，请检查启动窗口是否仍在运行。'); }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = typeof body.detail === 'string' ? body.detail : '请求未完成，请检查输入后重试。';
    throw new Error(message);
  }
  return body;
}
function toast(message) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4500);
}
function setView(name) {
  const titles = {chat:'校园问答', library:'我的知识库', evaluation:'检索检查', review:'回答核对',guide:'项目指南'};
  document.querySelectorAll('.view').forEach((view) => { view.hidden = view.id !== 'view-' + name; });
  document.querySelectorAll('.nav-button').forEach((button) => {
    button.classList.toggle('active', button.dataset.view === name);
    if (button.dataset.view === name) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  $('#page-title').textContent = titles[name];
  if (name === 'library') renderDocuments();
  if (name === 'review') window.CampusReview?.load();
}
function populateCategories(categories) {
  ['chat-category', 'library-category', 'upload-category'].forEach((id) => {
    const select = $('#' + id);
    const old = select.value;
    const prefix = id === 'upload-category' ? '' : '<option value="">' + (id === 'chat-category' ? '全部资料' : '全部分类') + '</option>';
    select.innerHTML = prefix + categories.map((category) => '<option value="' + escapeHtml(category) + '">' + escapeHtml(category) + '</option>').join('');
    select.value = old || (id === 'upload-category' ? '其他' : '');
  });
}
function updateAnswerModeCaption(){
  $('#composer-caption').textContent=($('#extractive-only').checked||!state.status.llm_enabled)
    ? '当前显示检索原文，不调用生成模型；请核对来源与文件版本。'
    : '发送问题将由大模型依据检索资料生成回答，请核对来源与文件版本。';
}
$('#extractive-only').addEventListener('change',updateAnswerModeCaption);
async function refresh() {
  const [status, documents,imports] = await Promise.all([api('/api/status'), api('/api/documents'),api('/api/imports')]);
  state.status = status;
  state.documents = documents;
  $('#import-records').innerHTML=imports.slice(0,20).map(r=>'<p><strong>'+escapeHtml(r.filename)+'</strong> — '+(r.status==='completed'?'已完成':'未成功')+'：'+escapeHtml(r.message)+(r.chunks?' '+r.chunks+'个片段':'')+' <small>'+escapeHtml(new Date(r.created_at).toLocaleString())+'</small></p>').join('')||'<p class="muted">本次升级后上传的文件会记录处理结果；已有资料不需要重复导入。</p>';
  if (!initialized) {
    $('#include-samples').checked = status.documents === status.sample_documents;
    if (status.documents > status.sample_documents && $('#welcome')) {
      const suggestions = [
        ['培养方案', '2023级计算机科学与技术专业毕业总学分是多少？'],
        ['课程大纲', '数据结构课程的考核方式是什么？'],
        ['学位申请', '申请学士学位需要满足什么要求？'],
        ['成绩单', '电子成绩单发送后在哪里查看订单进度？']
      ];
      $('#welcome .suggestions').innerHTML = suggestions.map(([category, question]) => '<button data-question="' + escapeHtml(question) + '"><small>' + escapeHtml(category) + '</small><span>' + escapeHtml(question) + '</span><span class="suggestion-icon" aria-hidden="true">↗</span></button>').join('');
      $('#welcome .demo-note').textContent = '已载入学校公开资料，请核对文件版本和适用范围。演示资料默认不参与问答。';
      welcomeMarkup = $('#conversation').innerHTML;
    }
    initialized = true;
  }
  populateCategories(status.categories);
  $('#nav-count').textContent = status.documents;
  $('#stat-documents').textContent = status.documents;
  $('#stat-chunks').textContent = status.chunks;
  $('#stat-dimension').textContent = status.dimension;
  $('#mode-text').textContent = status.llm_enabled ? '大模型已开启' : (status.mode === 'semantic' ? '语义检索 · 原文摘录' : '原文检索演示');
  updateAnswerModeCaption();
  renderDocuments();
}
function renderDocuments() {
  const query = $('#document-search').value.trim().toLowerCase();
  const category = $('#library-category').value;
  const docs = state.documents.filter((doc) => (!category || doc.category === category) && (doc.name + ' ' + doc.source).toLowerCase().includes(query));
  $('#document-rows').innerHTML = docs.map((doc) => {
    const suffix = doc.name.split('.').pop().toUpperCase();
    return '<tr><td><div class="document-name"><span class="file-icon">' + escapeHtml(suffix) + '</span><div><strong>' + escapeHtml(doc.name) + '</strong>' + (doc.sample ? '<span class="sample-tag">演示</span>' : '') + '<small>' + escapeHtml(doc.source || '来源未填写') + '</small></div></div></td><td>' + escapeHtml(doc.category) + '</td><td>' + doc.chunk_count + '</td><td>' + escapeHtml(doc.version || '未填写') + '</td><td><div class="row-actions"><button data-document="' + doc.id + '">查看</button><a href="/api/documents/' + doc.id + '/download" aria-label="下载 ' + escapeHtml(doc.name) + '">下载</a><button class="delete" data-delete="' + doc.id + '">删除</button></div></td></tr>';
  }).join('');
  $('#library-empty').hidden = docs.length > 0;
  $('#library-empty').textContent = state.documents.length ? '没有符合搜索条件的文档。' : '这里还没有资料。点击“导入文档”开始建立知识库。';
}
function renderEvidence(result) {
  $('#evidence-count').textContent = result.sources.length + ' 个相关片段';
  if (!result.sources.length) {
    $('#evidence-content').innerHTML = '<div class="evidence-empty"><h3>暂未找到依据</h3><p>尝试补充相关文档，或调整问题中的专业、年级与课程名称。</p></div>';
    return;
  }
  $('#evidence-content').innerHTML = '<p class="evidence-score-note">相似度表示检索匹配程度，不是答案正确率。</p>' + result.sources.map((source, index) => {
    const position = source.page ? '第 ' + source.page + ' 页' : '片段 ' + source.ordinal;
    return '<article class="evidence-card" data-citation="'+source.citation+'"><h3><span class="citation-badge">[' + source.citation + ']</span>' + escapeHtml(source.name) + (source.sample ? '<span class="sample-tag">演示</span>' : '') + '</h3><p class="source-meta">' + escapeHtml(source.category) + ' / ' + position + '<br>相似度 ' + Number(source.score).toFixed(3) + ' · ' + escapeHtml(source.version || '版本未填写') + '</p><details ' + (index === 0 ? 'open' : '') + '><summary>查看原文片段</summary><p class="source-text">' + escapeHtml(source.text) + '</p></details><a href="/api/documents/' + source.document_id + '/download">下载原文件</a></article>';
  }).join('');
}
function scrollConversation() {
  const conversation = $('#conversation');
  conversation.scrollTop = conversation.scrollHeight;
}
function message(kind, text, result) {
  const article = document.createElement('article');
  article.className = 'message ' + kind;
  const heading = document.createElement('div');
  heading.className = 'message-heading';
  heading.innerHTML = kind === 'user' ? '<span>我</span> 你' : '<span>知</span> 知校';
  const body = document.createElement('div');
  body.className = 'message-body';
  body.textContent = text;
  article.append(heading, body);
  if (result) {
    const number = state.answers.push(result) - 1;
    const meta = document.createElement('div');
    meta.className = 'answer-meta';
    const label = {extractive:'原文摘录', rag:'大模型回答', no_evidence:'无足够依据',clarification:'需要补充信息'}[result.mode];
    const strategyLabel=result.retrieval_mode === 'tfidf' ? 'TF-IDF 基线检索' : ({hybrid:'混合检索',hybrid_rerank:'混合检索与重排序',vector:'语义向量检索'}[result.retrieval_strategy]);
    meta.textContent = label + ' · ' + result.elapsed_ms + ' ms · ' + strategyLabel;
    const button = document.createElement('button');
    button.className = 'text-button';
    button.textContent = '查看本次依据';
    button.dataset.answer = number;
    meta.append(button);
    const copy=document.createElement('button');copy.className='text-button';copy.textContent='复制回答';copy.dataset.copyAnswer=number;
    const download=document.createElement('button');download.className='text-button';download.textContent='导出回答与依据';download.dataset.exportAnswer=number;
    meta.append(copy,download);
    article.append(meta);
    body.replaceChildren();
    const valid=new Set(result.sources.map(s=>String(s.citation)));
    String(text).split(/(\[\d+\])/g).forEach(part=>{
      const number=part.match(/^\[(\d+)\]$/)?.[1];
      if(number&&valid.has(number)){
        const link=document.createElement('button');link.className='inline-citation';link.textContent=part;
        link.dataset.sourceCitation=number;link.dataset.answerIndex=String(state.answers.length-1);
        link.setAttribute('aria-label','查看来源'+number);body.append(link);
      }else body.append(document.createTextNode(part));
    });
    if(result.query_rewritten){
      const note=document.createElement('p');note.className='query-resolution';
      note.textContent='结合上一轮，本次查询：'+result.resolved_question;article.append(note);
    }
    result.warnings.forEach((warning) => {
      const notice = document.createElement('p');
      notice.className = 'answer-warning';
      notice.textContent = warning;
      article.append(notice);
    });
  }
  $('#conversation').append(article);
  scrollConversation();
  return article;
}
async function ask(question) {
  return window.CampusChat?.ask(question);
}
async function showSettings() {
  try {
    const values = await api('/api/settings');
    const form = $('#settings-form');
    ['base_url', 'model', 'embedding_mode', 'model_path', 'min_score', 'query_prefix', 'retrieval_strategy', 'target_cohort', 'target_major'].forEach((key) => { form.elements[key].value = values[key]; });
    form.elements.enabled.checked = values.enabled;
    form.elements.remember_key.checked = values.remember_key;
    form.elements.api_key.value = '';
    form.elements.clear_key.checked = false;
    form.elements.enable_ocr.checked = values.enable_ocr;
    $('#key-status').textContent = values.has_key ? (values.remember_key ? '密钥已用 Windows 当前用户加密保存，留空保留。重启可读取，换电脑需重新填写。' : '本次运行已有密钥，留空保留；未加密保存时重启需重新填写。') : '密钥可仅在进程使用，或勾选 Windows 本机加密保存。也可连接无需密钥的本机服务。';
    $('#settings-error').textContent = '';
    $('#settings-dialog').showModal();
  } catch (error) { toast(error.message); }
}

document.addEventListener('click', async (event) => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.dataset.view) setView(button.dataset.view);
  if (button.dataset.go) setView(button.dataset.go);
  if (button.classList.contains('close-dialog')) button.closest('dialog').close();
  if (button.dataset.question) { setView('chat'); await ask(button.dataset.question); }
  if (button.dataset.answer !== undefined) renderEvidence(state.answers[Number(button.dataset.answer)]);
  if(button.dataset.sourceCitation){
    renderEvidence(state.answers[Number(button.dataset.answerIndex)]);
    const card=$('.evidence-card[data-citation="'+button.dataset.sourceCitation+'"]');
    if(card){card.classList.add('selected-evidence');card.querySelector('details').open=true;card.scrollIntoView({block:'nearest',behavior:'smooth'});}
  }
  if (button.dataset.copyAnswer !== undefined) {
    try { await navigator.clipboard.writeText(state.answers[Number(button.dataset.copyAnswer)].answer);toast('回答已复制。'); }
    catch { toast('无法使用剪贴板，可导出回答后复制。'); }
  }
  if (button.dataset.exportAnswer !== undefined) {
    const result=state.answers[Number(button.dataset.exportAnswer)];
    const lines=['# 校园问答记录','',result.question,'',result.answer,'',
      '回答方式：'+result.mode+'；检索策略：'+result.retrieval_strategy+'；记录时间：'+result.recorded_at,'','## 原文依据'];
    result.sources.forEach(s=>lines.push('', '['+s.citation+'] '+s.name+' · '+(s.page ? '文件第'+s.page+'页' : '片段'+s.ordinal)+' · '+s.version,'',s.text));
    const url=URL.createObjectURL(new Blob([lines.join('\n')],{type:'text/markdown;charset=utf-8'}));
    const link=document.createElement('a');link.href=url;link.download='校园问答记录-'+Date.now()+'.md';link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);toast('已导出回答与原文依据。');
  }
  if (button.dataset.document) {
    try {
      const doc = await api('/api/documents/' + button.dataset.document);
      $('#document-title').textContent = doc.name;
      const metadata = doc.metadata || {};
      $('#document-meta').textContent = doc.category + ' / ' + (doc.version || '版本未填写') + ' / ' + doc.chunks.length + ' 个片段' + (doc.sample ? ' / 虚构演示资料' : '') + ' / ' + (metadata.cohort ? metadata.cohort + ' 级' : '年级未单独限定') + ' / ' + (metadata.major || '专业未单独限定') + (metadata.reference_only ? ' / 仅参考，不进入问答' : '') + (metadata.published_at ? ' / 发布：' + metadata.published_at : '');
      $('#document-chunks').innerHTML = doc.chunks.map((chunk) => '<article class="chunk-preview"><h3>片段 ' + chunk.ordinal + (chunk.page ? ' · 第 ' + chunk.page + ' 页' : '') + (chunk.heading ? ' · ' + escapeHtml(chunk.heading) : '') + '</h3><p>' + escapeHtml(chunk.text) + '</p></article>').join('');
      $('#document-dialog').showModal();
    } catch (error) { toast(error.message); }
  }
  if (button.dataset.delete) {
    state.deleting = button.dataset.delete;
    $('#delete-caption').textContent = '删除“' + state.documents.find((doc) => doc.id === state.deleting).name + '”？';
    $('#delete-dialog').showModal();
  }
});
$('#question-form').addEventListener('submit', (event) => { event.preventDefault(); ask($('#question').value); });
$('#question').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); ask(event.target.value); }
});
$('#new-chat').addEventListener('click', () => {
  if (state.busy) return;
  setView('chat');
  state.answers = [];
  $('#conversation').innerHTML = welcomeMarkup;
  $('#evidence-count').textContent = '等待提问';
  $('#evidence-content').innerHTML = '<div class="evidence-empty"><h3>答案从这里开始</h3><p>提问后，这里会展示检索到的资料片段、文件名和位置。</p></div>';
  $('#question').focus();
  window.CampusChat?.reset();
});
$('#document-search').addEventListener('input', renderDocuments);
$('#library-category').addEventListener('change', renderDocuments);
$('#upload-open').addEventListener('click', () => {
  $('#upload-form').reset();
  $('#upload-category').value = '其他';
  $('#file-caption').textContent = '点击选择文件，或拖放到这里';
  $('#upload-error').textContent = '';
  $('#upload-dialog').showModal();
});
$('#upload-file').addEventListener('change', () => { $('#file-caption').textContent = $('#upload-file').files[0]?.name || '点击选择文件，或拖放到这里'; });
['dragenter', 'dragover'].forEach((type) => $('#drop-zone').addEventListener(type, (event) => { event.preventDefault(); $('#drop-zone').classList.add('dragging'); }));
['dragleave', 'drop'].forEach((type) => $('#drop-zone').addEventListener(type, (event) => { event.preventDefault(); $('#drop-zone').classList.remove('dragging'); }));
$('#drop-zone').addEventListener('drop', (event) => {
  if (!event.dataTransfer.files.length) return;
  const transfer = new DataTransfer();
  transfer.items.add(event.dataTransfer.files[0]);
  $('#upload-file').files = transfer.files;
  $('#upload-file').dispatchEvent(new Event('change'));
});
$('#upload-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const file = $('#upload-file').files[0];
  if (!file) return;
  if (file.size > 10 * 1024 * 1024) { $('#upload-error').textContent = '文件不能超过 10 MB。'; return; }
  const data = new FormData(event.target);
  data.set('file', file);
  $('#upload-submit').disabled = true;
  $('#upload-submit').textContent = '正在解析…';
  $('#upload-error').textContent = '';
  try {
    await api('/api/documents', {method:'POST', body:data});
    await refresh();
    $('#upload-dialog').close();
    toast('文档已导入，可以开始提问。');
  } catch (error) { $('#upload-error').textContent = error.message; }
  finally { $('#upload-submit').disabled = false; $('#upload-submit').textContent = '解析并建立索引'; }
});
$('#confirm-delete').addEventListener('click', async () => {
  $('#confirm-delete').disabled = true;
  try {
    await api('/api/documents/' + state.deleting, {method:'DELETE'});
    await refresh();
    $('#delete-dialog').close();
    toast('文档和对应索引已删除。');
  } catch (error) { toast(error.message); }
  finally { $('#confirm-delete').disabled = false; }
});
['settings-open', 'mode-button', 'guide-settings'].forEach((id) => $('#' + id).addEventListener('click', showSettings));
$('#settings-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.target;
  const body = Object.fromEntries(new FormData(form));
  body.enabled = form.elements.enabled.checked;
  body.remember_key = form.elements.remember_key.checked;
  body.clear_key = form.elements.clear_key.checked;
  body.enable_ocr = form.elements.enable_ocr.checked;
  body.min_score = Number(body.min_score);
  $('#settings-save').disabled = true;
  $('#settings-save').textContent = '保存并更新索引…';
  $('#settings-error').textContent = '';
  try {
    await api('/api/settings', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    form.elements.api_key.value = '';
    await refresh();
    $('#settings-dialog').close();
    toast('设置已保存。');
  } catch (error) { $('#settings-error').textContent = error.message; }
  finally { $('#settings-save').disabled = false; $('#settings-save').textContent = '保存设置'; }
});
$('#run-evaluation').addEventListener('click', async () => {
  $('#run-evaluation').disabled = true;
  try {
    const result = await api('/api/evaluation?top_k=4');
    const percent = (value) => value == null ? '—' : Math.round(value * 100) + '%';
    $('#evaluation-results').innerHTML = '<div class="evaluation-summary"><div><strong>' + percent(result.evidence_hit_rate) + '</strong><span>正确证据命中 @' + result.top_k + '</span></div><div><strong>' + percent(result.mean_recall_at_k) + '</strong><span>平均片段 Recall@' + result.top_k + '</span></div><div><strong>' + (result.mean_mrr == null ? '未计算' : result.mean_mrr.toFixed(3)) + '</strong><span>首个正确证据排名 MRR</span></div><div><strong>' + percent(result.document_hit_rate) + '</strong><span>正确文件命中（辅助）</span></div></div><p class="evaluation-note">' + escapeHtml(result.note) + ' 已解析证据标签 ' + result.scored_answerable + ' 题；无依据拒答率：' + percent(result.rejection_rate) + '。</p><div class="table-wrap"><table class="evaluation-table"><thead><tr><th>问题</th><th>标注来源与位置</th><th>文件</th><th>证据片段</th></tr></thead><tbody>' + result.rows.map((row) => '<tr><td>' + escapeHtml(row.question) + '</td><td>' + escapeHtml(row.expected_document || '无依据') + (row.reference_page ? '<br>文件第' + row.reference_page + '页' : '') + '</td><td>' + (row.document_hit ? '找到' : '未找到') + '</td><td class="' + (row.evidence_hit ? 'hit' : 'miss') + '">' + (row.skipped ? '原件缺失' : (!row.label_resolved ? '标签未解析' : (row.evidence_hit ? '找到正确证据' : '未找到正确证据'))) + '</td></tr>').join('') + '</tbody></table></div>';
  } catch (error) { toast(error.message); }
  finally { $('#run-evaluation').disabled = false; }
});

refresh().then(() => {
  if (state.status.startup_warning) toast(state.status.startup_warning);
}).catch((error) => { $('#mode-text').textContent = '服务未连接'; toast(error.message); });

$('#upload-form .form-grid').insertAdjacentHTML('afterend', '<div class="form-grid"><label>适用专业<input name="major" maxlength="150" placeholder="全校通用文件留空"></label><label>适用入学年级<input name="cohort" maxlength="50" placeholder="例如 2023，通用文件留空"></label></div><label class="field-label">发布日期<input name="published_at" type="date"></label><label class="checkbox-label"><input name="reference_only" type="checkbox"> 仅作为参考版本保存，不进入问答检索</label>');
$('#key-status').insertAdjacentHTML('afterend', '<label class="checkbox-label"><input name="remember_key" type="checkbox"> 使用 Windows 本机加密保存密钥，重启后可用</label>');
$('#settings-form .dialog-actions').insertAdjacentHTML('beforebegin', '<label class="field-label">检索策略<select name="retrieval_strategy"><option value="vector">向量检索</option><option value="hybrid">语义 + TF-IDF 混合检索</option><option value="hybrid_rerank">混合检索与问题重点重排序</option></select></label><label class="field-label">语义查询前缀<input name="query_prefix" placeholder="不同模型采用各自的检索指令"></label><div class="form-grid"><label>默认入学年级<input name="target_cohort" value="2023" maxlength="50"></label><label>默认专业<input name="target_major" value="计算机科学与技术" maxlength="150"></label></div><label class="checkbox-label"><input name="enable_ocr" type="checkbox"> 对 PDF 扫描页启用本地 OCR</label><p class="field-hint">OCR 文字需核对数字与表格。换电脑时需先安装 OCR 依赖并准备模型。</p><div class="connection-actions"><button id="preset-deepseek" class="secondary-button" type="button">填入 DeepSeek 配置示例</button><button id="test-model" class="secondary-button" type="button">检测 API 连接</button></div><p id="connection-result" class="field-hint" role="status"></p>');
$('#preset-deepseek').addEventListener('click', () => {
  const form = $('#settings-form');
  form.elements.base_url.value = 'https://api.deepseek.com';
  form.elements.model.value = 'deepseek-flash';
  $('#connection-result').textContent = '示例配置核对于 2026-10-13。请先在服务商控制台创建密钥，再填入此表单。';
});
$('#test-model').addEventListener('click', async () => {
  const form = $('#settings-form');
  const body = Object.fromEntries(new FormData(form));
  body.enabled = form.elements.enabled.checked;
  body.remember_key = form.elements.remember_key.checked;
  body.clear_key = form.elements.clear_key.checked;
  body.enable_ocr = form.elements.enable_ocr.checked;
  body.min_score = Number(body.min_score);
  $('#test-model').disabled = true;
  $('#connection-result').textContent = '正在读取模型列表，不发送校园资料…';
  try {
    const result = await api('/api/model/test', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    $('#connection-result').textContent = result.message + (result.selected_model_available ? ' 所填模型可用。' : ' 当前模型名不在返回列表中，可用模型：' + result.models.slice(0, 10).join('、'));
  } catch (error) { $('#connection-result').textContent = error.message; }
  finally { $('#test-model').disabled = false; }
});
