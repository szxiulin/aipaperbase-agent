/* Local research workspace. Reuses the existing API, selection and confirmation UI. */
window.Research = (() => {
  let records = [], editing = null, saving = false;
  const text = escapeHtml;
  async function sync() { records = (await api('/api/research')).items; }
  function reading(eid) { return records.find(r => r.kind === 'reading' && r.data.entity_id === eid)?.data.status || 'unread'; }
  async function saveReading(eid,status) { await save('reading',{entity_id:eid,status}); }
  async function save(kind,data,id='') {
    const result = await postJson('/api/research/save',{kind,data,record_id:id});
    await sync(); return result;
  }
  function scopeIds() {
    if(myLibrary.selected.size) return [...myLibrary.selected];
    if(state.currentCollectionId) return myLibrary.items.filter(p=>p.collections?.some(c=>c.collection_id===state.currentCollectionId)).map(p=>p.entity_id);
    return [];
  }
  async function startScoped() {
    const ids=scopeIds(); if(!ids.length) throw new Error('先勾选论文，或打开一个非空集合');
    const conv=await postJson('/api/chats',{title:'研究 '+ids.length+' 篇论文'});
    await save('chat_scope',{conversation_id:conv.conversation_id,entity_ids:ids,collection_id:state.currentCollectionId || ''});
    switchView('rag');await openChat(conv.conversation_id);
  }
  async function showScope(cid) {
    await sync(); if(chatState.current?.conversation_id!==cid) return;
    let bar=$('#chat-research-scope');
    if(!bar){bar=document.createElement('div');bar.id='chat-research-scope';$('#chat-header').after(bar);}
    const scope=records.find(r=>r.kind==='chat_scope'&&r.data.conversation_id===cid)?.data;
    bar.textContent=scope ? '本对话固定研究 '+scope.entity_ids.length+' 篇：'+scope.entity_ids.join('、')+'。需要改变范围时请重新选择论文开启对话。' : '本对话未限定论文范围';
  }
  async function load() {
    await sync(); await loadCollections();
    $('#research-collection').innerHTML='<option value="">选择集合</option>'+state.collections.map(c=>`<option value="${text(c.collection_id)}">${text(c.name)}</option>`).join('');
    if(state.currentCollectionId) $('#research-collection').value=state.currentCollectionId;
    render();
  }
  function render() {
    const visible=records.filter(r=>['note','progress','comparison'].includes(r.kind));
    $('#research-records').innerHTML=visible.length ? visible.map(r=>`<article class="research-record"><strong>${text(r.data.title || r.data.question || ({progress:'集合研究进展',comparison:'论文比较表',note:'笔记'}[r.kind]))}</strong><p>${text({note:'笔记',progress:'进展',comparison:'比较表'}[r.kind])} · ${r.data.status === 'draft' ? '草稿待核对' : '已保存'} · ${text(r.updated_at)}</p><button class="secondary-button" data-edit-research="${text(r.record_id)}">打开与编辑</button><button class="text-button" data-export-research="${text(r.record_id)}">导出 Markdown</button></article>`).join(''):'<p class="empty">还没有研究资料。可以写笔记，或在“我的论文”选择论文建立比较表。</p>';
    $$('[data-edit-research]').forEach(b=>b.onclick=()=>edit(records.find(r=>r.record_id===b.dataset.editResearch)));
    $$('[data-export-research]').forEach(b=>b.onclick=()=>exportRecord(records.find(r=>r.record_id===b.dataset.exportResearch)));
    $('#research-watches').innerHTML=records.filter(r=>r.kind==='watch').map(r=>`<p>${text(r.data.query)} <button class="secondary-button" data-watch="${text(r.record_id)}">检查新增</button><span></span></p>`).join('');
    $$('[data-watch]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{const r=await postJson('/api/research/watch-check',{record_id:b.dataset.watch});b.nextElementSibling.textContent=`新增 ${r.total} 篇：${r.entity_ids.join('、') || '暂无'}（相对保存时的目录）`;}catch(e){showError(e);}finally{b.disabled=false;}});
  }
  function edit(record) {
    editing=JSON.parse(JSON.stringify(record));const d=editing.data;
    let fields='';
    if(record.kind==='comparison') fields=`<p>固定范围 ${d.entity_ids.length} 篇。空白表示待核验，模型草稿也需检查原文。</p><div class="table-wrap"><table><thead><tr><th>论文</th>${d.fields.map(f=>`<th>${text(f)}</th>`).join('')}</tr></thead><tbody>${d.rows.map((r,i)=>`<tr><td>${text(r.title)}</td>${d.fields.map((f,j)=>{const c=r.cells[f]||{};return `<td><textarea aria-label="${text(r.title+' '+f)}" data-cell="${i}:${j}">${text(c.value||'')}</textarea>${(c.evidence||[]).map(e=>`<details><summary>查看依据</summary><p>${text(e.text)}</p><small>${text(e.chunk_id)}</small></details>`).join('')}</td>`;}).join('')}</tr>`).join('')}</tbody></table></div>`;
    else if(record.kind==='progress') fields=['question','conclusion','uncertainty','next'].map((f,i)=>`<label>${['研究问题','已确认结论','争议与待核验','下一步'][i]}<textarea name="${f}">${text(d[f]||'')}</textarea></label>`).join('');
    else fields=`<label>笔记内容<textarea name="content">${text(d.content||'')}</textarea></label>${(d.evidence||[]).map(e=>`<details><summary>${text(e.title||e.entity_id)} · 证据快照</summary><p>${text(e.text||'')}</p><small>${text(e.section||e.chunk_id||'')}</small></details>`).join('')}`;
    $('#research-editor').innerHTML=`<form id="research-edit-form"><label>标题<input name="title" required value="${text(d.title||'')}"/></label>${fields}<button class="primary-button">核对并保存</button><button type="button" id="research-cancel" class="text-button">关闭</button><span id="research-save-status" role="status"></span></form>`;
    $('#research-cancel').onclick=()=>{$('#research-editor').innerHTML='';editing=null;};
    $('#research-edit-form').onsubmit=async event=>{
      event.preventDefault();if(saving)return;saving=true;const button=event.submitter;button.disabled=true;
      try{
        const form=new FormData(event.target);d.title=form.get('title');d.status='confirmed';
        if(record.kind==='comparison') $$('[data-cell]').forEach(el=>{const [i,j]=el.dataset.cell.split(':').map(Number);const f=d.fields[j];d.rows[i].cells[f]={...d.rows[i].cells[f],value:el.value,reviewed:true};});
        else if(record.kind==='progress') ['question','conclusion','uncertainty','next'].forEach(f=>d[f]=form.get(f));
        else d.content=form.get('content');
        const result=await save(record.kind,d,editing.record_id);editing.record_id=result.record_id;$('#research-save-status').textContent='已保存';render();
      }catch(e){showError(e);}finally{saving=false;button.disabled=false;}
    };
    $('#research-editor').scrollIntoView({block:'start'});
  }
  async function createComparison() {
    const ids=scopeIds();if(!ids.length)throw new Error('先选择要比较的论文');
    const fields=['方法','任务与数据集','实验条件与结果','局限'];
    const data={title:'论文比较表',entity_ids:ids,fields,status:'draft',rows:ids.map(id=>({entity_id:id,title:myLibrary.items.find(p=>p.entity_id===id)?.title||id,cells:Object.fromEntries(fields.map(f=>[f,{value:'',evidence:[]}]))}))};
    const r=await save('comparison',data);switchView('research');await load();edit(r);
  }
  function exportRecord(r) {
    const d=r.data;let body='# '+(d.title||'研究资料')+'\n\n';
    if(r.kind==='comparison'){
      const clean=x=>String(x||'待核验').replace(/\|/g,'\\|').replace(/\n/g,'<br>');
      body+='|论文|'+d.fields.join('|')+'|\n|---|'+d.fields.map(()=>'---').join('|')+'|\n';
      body+=d.rows.map(row=>'|'+clean(row.title)+'|'+d.fields.map(f=>clean(row.cells[f]?.value)).join('|')+'|').join('\n');
      body+='\n\n## 证据\n'+d.rows.map(row=>d.fields.map(f=>(row.cells[f]?.evidence||[]).map(e=>`${row.title} / ${f} / ${e.chunk_id}\n> ${e.text}`).join('\n')).join('\n')).join('\n');
    }else if(r.kind==='progress')body+=['question','conclusion','uncertainty','next'].map((f,i)=>`## ${['研究问题','已确认结论','争议与待核验','下一步'][i]}\n${d[f]||''}`).join('\n\n');
    else body+=(d.content||'')+'\n\n'+(d.evidence||[]).map(e=>`> ${e.text||''}\n来源：${e.title||e.entity_id} ${e.section||''} ${e.chunk_id||''}`).join('\n\n');
    const url=URL.createObjectURL(new Blob([body],{type:'text/markdown;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='research.md';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  async function showTasks(){
    const [result,config]=await Promise.all([api('/api/research/tasks'),api('/api/rag/status')]);
    $('#research-config').textContent=config.status==='unconfigured'?'尚未配置模型服务；本地收藏、笔记、阅读记录和比较表编辑可用。':'全文服务状态：'+(config.status||'请查看逐项状态')+'。本页仅查询状态，不执行付费测试。';
    $('#research-tasks').innerHTML=result.items.map(t=>`<article class="research-record">${text(t.requested.join('、'))} · ${text({done:'完成',running:'运行中',error:'失败',interrupted:'已中断'}[t.status]||t.status)}<p>${text(t.error||'')}</p>${['error','interrupted'].includes(t.status)?`<button class="secondary-button" data-retry-task="${text(t.task_id)}">重试未完成部分</button>`:''}</article>`).join('')||'<p>暂无入库任务</p>';
    $$('[data-retry-task]').forEach(b=>b.onclick=async()=>{const t=result.items.find(t=>t.task_id===b.dataset.retryTask);if(!await askConfirm('按当前状态重试任务？可能调用配置的下载、解析和模型服务。'))return;b.disabled=true;try{const task=await postJson(`/api/chats/${t.conversation_id}/ingest`,{entity_ids:t.requested});await waitTask(task.task_id,'/api/downloads/status');await showTasks();}catch(e){showError(e);b.disabled=false;}});
  }
  $('#research-refresh').onclick=()=>load().catch(showError);
  $('#research-new-note').onclick=()=>edit({kind:'note',data:{title:'研究笔记',content:'',status:'draft'}});
  $('#research-progress').onclick=()=>{const cid=$('#research-collection').value;if(!cid){showError(new Error('先选择一个集合'));return;}edit(records.find(r=>r.kind==='progress'&&r.data.collection_id===cid)||{kind:'progress',data:{title:'集合研究进展',collection_id:cid}});};
  $('#research-tasks-refresh').onclick=()=>showTasks().catch(showError);
  $('#research-watch').onsubmit=async e=>{e.preventDefault();try{await save('watch',{query:new FormData(e.target).get('query')});render();}catch(err){showError(err);}};
  $('#research-lookup').onsubmit=async e=>{e.preventDefault();try{const result=await api('/api/research/lookup?q='+encodeURIComponent(new FormData(e.target).get('query')));$('#research-import-results').innerHTML=result.items.map(p=>`<p>${text(p.title)} <button class="secondary-button" data-import-id="${text(p.entity_id)}">附加所选 PDF</button><button class="text-button" data-collect-id="${text(p.entity_id)}">加入集合</button></p>`).join('')||'<p>目录未匹配到论文。暂不能创建目录外论文，请保留原文件。</p>';$$('[data-collect-id]').forEach(b=>b.onclick=()=>openPicker([b.dataset.collectId]).catch(showError));$$('[data-import-id]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{const file=$('#research-pdf').files[0];if(!file||file.size>20*1024*1024)throw new Error('请选择不超过 20 MB 的 PDF');const content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});const r=await postJson('/api/research/upload',{entity_id:b.dataset.importId,content});b.textContent=r.message;}catch(err){showError(err);}finally{b.disabled=false;}});}catch(err){showError(err);}};
  $('#research-backup-file').onchange=async e=>{try{const file=e.target.files[0];if(!file||file.size>30*1024*1024)throw new Error('请选择不超过 30 MB 的备份');const backup=JSON.parse(await file.text());const preview=await postJson('/api/research/restore',{backup});$('#research-backup-preview').innerHTML=`<p>${text(preview.policy)}</p><pre>${text(JSON.stringify(preview.counts,null,2))}</pre><button id="research-restore" class="primary-button">确认补充导入</button>`;$('#research-restore').onclick=async()=>{const b=$('#research-restore');b.disabled=true;try{const r=await postJson('/api/research/restore',{backup,confirm:true});$('#research-backup-preview').textContent=`已补充 ${r.added} 条记录`;await load();}catch(err){showError(err);b.disabled=false;}};}catch(err){showError(err);}};
  document.addEventListener('click',async e=>{const b=e.target.closest('.save-research-note');if(!b)return;b.disabled=true;try{const data=await api(`/api/chats/${chatState.current.conversation_id}/messages`);const m=data.messages.find(m=>m.message_id===b.dataset.mid);if(!m)throw new Error('消息尚未保存，请稍后重试');await save('note',{title:'聊天证据笔记',content:m.content,evidence:m.chunks||[],conversation_id:chatState.current.conversation_id,status:'confirmed'},'message:'+m.message_id);b.textContent='已保存到研究资料';}catch(err){showError(err);b.disabled=false;}});
  return {load,sync,reading,saveReading,startScoped,showScope,createComparison};
})();
