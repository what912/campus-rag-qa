'use strict';
window.CampusReview=(()=>{
  let report=null,loaded=false;
  const drafts=new Map();
  const labels={direct:'直接回答',tfidf:'TF-IDF RAG',semantic:'语义 RAG',hybrid:'混合 RAG'};
  const key=r=>r.id+'|'+r.mode;
  let scores={};
  const qualityOptions='<option value="">未复核</option><option value="supported">支持</option><option value="partial">部分支持</option><option value="unsupported">不支持</option>';
  function capture(){
    document.querySelectorAll('.review-card').forEach(card=>{
      const row=JSON.parse(card.dataset.row);
      scores[key(row)]={id:row.id,mode:row.mode,correctness:card.querySelector('[name="correctness"]').value === '' ? null : Number(card.querySelector('[name="correctness"]').value),
        evidence:card.querySelector('[name="evidence"]').value||null,citation:card.querySelector('[name="citation"]').value||null,
        notes:card.querySelector('[name="notes"]').value};
    });
  }
  function render(){
    const id=$('#review-question').value;
    const rows=report.rows.filter(r=>r.id===id);
    $('#review-question-text').textContent=rows[0]?.question||'';
    $('#review-reference').textContent=rows[0] ? '标注依据：'+rows[0].reference.document+' · '+(rows[0].reference.page ? '文件第'+rows[0].reference.page+'页' : 'HTML片段')+'；证据提示：'+rows[0].reference.evidence : '';
    $('#review-cards').innerHTML=rows.map(r=>'<article class="review-card" data-row="'+escapeHtml(JSON.stringify({id:r.id,mode:r.mode}))+'"><h2>'+labels[r.mode]+'</h2><p class="review-answer">'+escapeHtml(r.answer)+'</p><details><summary>检索依据 '+r.sources.length+' 段</summary>'+r.sources.map(s=>'<h3>['+s.citation+'] '+escapeHtml(s.name)+' · '+(s.page ? '文件第'+s.page+'页' : '片段'+s.ordinal)+'</h3><p class="review-source">'+escapeHtml(s.text)+'</p>').join('')+'</details><div class="review-score-fields"><label>正确性<select name="correctness"><option value="">未评分</option><option value="0">0 未答对或未回答</option><option value="1">1 部分正确</option><option value="2">2 核心事实正确且条件完整</option></select></label><label>证据支持<select name="evidence">'+qualityOptions+'</select></label><label>引用支持<select name="citation">'+qualityOptions+'</select></label></div><label>评分说明<textarea name="notes" rows="2" maxlength="3000" placeholder="记录遗漏、无依据结论、适用条件或复核意见"></textarea></label></article>').join('');
    document.querySelectorAll('.review-card').forEach(card=>{
      const row=JSON.parse(card.dataset.row);
      const s=scores[key(row)];
      if(s) ['correctness','evidence','citation','notes'].forEach(n=>card.querySelector('[name="'+n+'"]').value=s[n]??'');
      const initial=(report.assistant_initial_review||[]).find(r=>key(r)===key(row));
      if(initial){
        const note=document.createElement('p');note.className='review-initial';
        note.textContent='助手核心初评（待独立复核）：'+initial.status+'。'+initial.note+' 核心参考：'+initial.core_reference_fact;
        card.querySelector('.review-score-fields').before(note);
      }
    });
  }
  async function choose(){
    try{
      if(report){capture();drafts.set(report.filename,{scores:structuredClone(scores),reviewer:$('#reviewer-name').value});}
      report=await api('/api/experiments/'+encodeURIComponent($('#review-report').value));
      scores=drafts.get(report.filename)?.scores||Object.fromEntries((report.review.rows||[]).map(r=>[key(r),r]));
      $('#reviewer-name').value=drafts.get(report.filename)?.reviewer??report.review.reviewer??'';
      const ids=[...new Set(report.rows.map(r=>r.id))];
      $('#review-question').innerHTML=ids.map(id=>'<option value="'+escapeHtml(id)+'">'+escapeHtml(id)+' '+escapeHtml(report.rows.find(r=>r.id===id).question)+'</option>').join('');
      $('#review-status').textContent=report.rows.length+'条已保存结果，'+report.generation_attempts+'次实际请求。'+(report.plan.school ? '' : '本轮直接回答组缺少学校背景，仅作开发分析。');
      render();
    }catch(e){toast(e.message);}
  }
  async function load(){
    if(loaded)return;
    try{
      const reports=await api('/api/experiments');
      reports.sort((a,b)=>b.created_at.localeCompare(a.created_at)||Number(b.corrected)-Number(a.corrected));
      $('#review-report').innerHTML=reports.map(r=>'<option value="'+escapeHtml(r.filename)+'">'+escapeHtml(r.filename)+(r.corrected?'（含路由修正）':'（原始记录）')+'</option>').join('');
      if(reports.length){loaded=true;await choose();}else{$('#review-status').textContent='还没有保存的模型实验记录。';}
    }catch(e){toast(e.message);}
  }
  $('#review-report').addEventListener('change',choose);
  $('#review-question').addEventListener('change',()=>{capture();render();});
  $('#save-review').addEventListener('click',async()=>{
    if(!report)return;capture();
    try{
      const payload={reviewer:$('#reviewer-name').value,rows:Object.values(scores)};
      await api('/api/experiments/'+encodeURIComponent(report.filename)+'/review',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      toast('评分已保存到本机。');
    }catch(e){toast(e.message);}
  });
  $('#export-review').addEventListener('click',()=>{
    if(!report)return;capture();
    const fields=['题号','方案','正确性0至2','证据支持','引用支持','说明','评分人'];
    const records=[fields,...report.rows.map(r=>{const s=scores[key(r)]||{};return [r.id,labels[r.mode],s.correctness??'',s.evidence||'',s.citation||'',s.notes||'',$('#reviewer-name').value];})];
    const cell=v=>'"'+String(v).replace(/"/g,'""')+'"';
    const url=URL.createObjectURL(new Blob(['\ufeff'+records.map(r=>r.map(cell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));
    const a=document.createElement('a');a.href=url;a.download='回答评分.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  return {load};
})();
