'use strict';
window.CampusChat=(()=>{
  let controller=null;
  const storageKey='campus-current-session';
  function remember(id){
    state.sessionId=id;
    try{if(id)localStorage.setItem(storageKey,id);else localStorage.removeItem(storageKey);}catch{}
  }
  function reset(){remember(null);$('#active-session-label').textContent='新会话';}
  async function list(){
    const rows=await api('/api/sessions');
    $('#session-list').innerHTML=rows.map(s=>'<article class="session-item"><button data-open-session="'+escapeHtml(s.id)+'"><strong>'+escapeHtml(s.title)+'</strong><small>'+s.turn_count+'轮 · '+escapeHtml(new Date(s.updated_at).toLocaleString())+'</small></button><button data-delete-session="'+escapeHtml(s.id)+'" class="text-button">删除</button></article>').join('')||'<p class="muted">还没有本地历史会话。</p>';
  }
  async function open(id){
    if(state.busy)return;
    const session=await api('/api/sessions/'+encodeURIComponent(id));
    state.answers=[];$('#conversation').replaceChildren();
    session.turns.forEach(turn=>{message('user',turn.question);message('assistant',turn.result.answer,turn.result);});
    if(session.turns.length)renderEvidence(session.turns.at(-1).result);
    else $('#conversation').innerHTML=welcomeMarkup;
    remember(id);$('#active-session-label').textContent=session.title;
    setView('chat');$('#history-dialog').close();
  }
  async function consumeSSE(response,onEvent){
    if(!response.ok){const detail=await response.json().catch(()=>({}));throw new Error(typeof detail.detail==='string'?detail.detail:'请求未完成，请重试。');}
    if(!response.body)throw new Error('浏览器不支持流式读取，请更换现代浏览器。');
    const reader=response.body.getReader();const decoder=new TextDecoder();let buffer='';
    try{
      while(true){
        const {value,done}=await reader.read();
        buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});
        buffer=buffer.replace(/\r\n/g,'\n');
        let boundary;
        while((boundary=buffer.indexOf('\n\n'))>=0){
          const frame=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);
          const event=frame.split('\n').find(l=>l.startsWith('event:'))?.slice(6).trim();
          const data=frame.split('\n').filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trimStart()).join('\n');
          if(event&&data)onEvent(event,JSON.parse(data));
        }
        if(done)break;
      }
    }finally{reader.releaseLock();}
  }
  async function ask(question){
    question=question.trim();if(!question||state.busy)return;
    state.busy=true;$('#send-button').disabled=true;$('#new-chat').disabled=true;$('#history-open').disabled=true;
    $('#stop-answer').hidden=false;$('#welcome')?.remove();message('user',question);$('#question').value='';
    const pending=message('thinking','正在查找原文…');const body=pending.querySelector('.message-body');
    let partial='',completed=false;
    controller=new AbortController();
    try{
      if(!state.sessionId){const s=await api('/api/sessions',{method:'POST'});remember(s.id);}
      if(controller.signal.aborted)throw new DOMException('Aborted','AbortError');
      const response=await fetch('/api/ask/stream',{method:'POST',signal:controller.signal,
        headers:{'Content-Type':'application/json'},body:JSON.stringify({question,session_id:state.sessionId,
          category:$('#chat-category').value,include_samples:$('#include-samples').checked,top_k:4,
          answer_mode:$('#extractive-only').checked?'extractive':'auto'})});
      await consumeSSE(response,(event,data)=>{
        if(event==='status')body.textContent=data.message;
        if(event==='sources')renderEvidence(data);
        if(event==='delta'){partial+=data.text;body.textContent=partial;pending.classList.add('streaming-answer');scrollConversation();}
        if(event==='error')throw new Error(data.message);
        if(event==='done'){
          completed=true;data.recorded_at=new Date().toISOString();pending.remove();
          message('assistant',data.answer,data);renderEvidence(data);
          $('#active-session-label').textContent=question.slice(0,40);
        }
      });
      if(!completed)throw new Error('连接在回答完成前中断，本次内容未确认为完整回答。');
    }catch(error){
      if(partial){body.textContent=partial;pending.className='message incomplete-answer';}
      else pending.remove();
      message('error',error.name==='AbortError'?'已停止接收。未完成的回答不作为完整记录；服务端是否已完成可在历史会话中核对。':error.message);
    }finally{
      state.busy=false;controller=null;$('#send-button').disabled=false;$('#new-chat').disabled=false;
      $('#history-open').disabled=false;$('#stop-answer').hidden=true;$('#question').focus();
    }
  }
  $('#history-open').addEventListener('click',async()=>{try{await list();$('#history-dialog').showModal();}catch(e){toast(e.message);}});
  $('#stop-answer').addEventListener('click',()=>controller?.abort());
  $('#session-list').addEventListener('click',async event=>{
    const openButton=event.target.closest('[data-open-session]');const deleteButton=event.target.closest('[data-delete-session]');
    try{
      if(openButton)await open(openButton.dataset.openSession);
      if(deleteButton){
        await api('/api/sessions/'+encodeURIComponent(deleteButton.dataset.deleteSession),{method:'DELETE'});
        if(state.sessionId===deleteButton.dataset.deleteSession)$('#new-chat').click();
        await list();toast('本机会话已删除。');
      }
    }catch(e){toast(e.message);}
  });
  async function restore(){
    let id;try{id=localStorage.getItem(storageKey);}catch{}
    if(id){try{await open(id);}catch{reset();}}
  }
  restore();
  return {ask,reset,open,consumeSSE};
})();
