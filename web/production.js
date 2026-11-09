import {agentPanel, taskAgentButtons, taskHasAgent, initAgentUI, handleAgentClick} from '/production-agents.js';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const names = {waiting:'Chờ',working:'Đang xử lý thủ công',done:'Hoàn thành',error:'Lỗi'};
const actions = {enabled:'Bật điều phối',start:'Nhận xử lý',complete:'Ghi hoàn thành',fail:'Ghi lỗi',retry:'Đưa lại hàng chờ',pause:'Tạm dừng',resume:'Tiếp tục'};
let host, board = null, selected = '', stageFilter = '', editing = null, generation = 0, pending = false, showAll = false, sourceVideo = '';
const sec = v => { v = Number(v)||0; const m = Math.floor(v/60); const r = Math.round(v-m*60); return m ? `${m} phút${r?` ${r} giây`:''}` : `${Math.round(v)} giây`; };
async function get(path) { const r = await fetch(path); const data = await r.json(); if (!r.ok) throw Error(data.error || 'Không tải được dữ liệu.'); return data; }
async function run(fn) { if(pending)return;pending=true;try { await fn(); } catch(e) { host.toast(e.message,true); } finally {pending=false;} }
const stageName = id => board.stages.find(s => s.id === id)?.name || id;
const sceneCode = id => board.scenes.find(s => s.id === id)?.code || 'Toàn phim';

export async function loadProduction(id = selected) {
  const current = ++generation;
  try {
    const [list, existing] = await Promise.all([get('/api/production'),get('/api/projects')]);
    if(current !== generation)return;
    selected = id || list.projects[0]?.id || '';
    $('productionProjects').innerHTML = list.projects.length ? list.projects.map(p => `<button type="button" class="production-project ${p.id===selected?'selected':''}" data-production-project="${p.id}"><b>${esc(p.name)}</b><small>${p.done}/${p.total} tác vụ · ${p.status==='paused'?'Tạm dừng':p.errors?`${p.errors} lỗi`:'Đang mở'}</small></button>`).join('') : '<p class="detail">Chưa có dự án sản xuất.</p>';
    $('productionExisting').innerHTML = '<option value="">Chọn dự án…</option>'+existing.projects.map(p => `<option value="${p.id}">${esc(p.name)} · ${p.scenes} cảnh</option>`).join('');
    if(selected) {
      const data = await get('/api/production/'+selected);
      if(current !== generation)return;
      board=data;render();
    } else {board=null;$('productionBoard').innerHTML='<div class="card production-empty"><h2>Một video gốc → phim AI động vật 8–9 phút</h2><p>Chọn video gốc và tạo dự án. Agent phân tích diễn biến, viết kịch bản toàn phim và chia cảnh, khóa nhân vật & môi trường, viết prompt; Muse tạo video; Editor + Sound Agent lập kế hoạch dựng cho CapCut. Âm thanh: chỉ tiếng tự nhiên. Mỗi bước chỉ mở khi bạn duyệt bước trước.</p></div>';}
  } catch(e) { if(current===generation)host.toast(e.message,true); }
}
function render() {
  const expandedAgentDetails=new Set([...document.querySelectorAll('[data-agent-details][open]')].map(e=>e.dataset.agentDetails));
  const sceneDetailsOpen=$('productionSceneDetails')?.open,eventsOpen=$('productionEventDetails')?.open;
  const p=board.project,s=board.summary,paused=p.production_status==='paused';
  const active=new Set(board.agent_state?.runs.filter(r=>['running','review'].includes(r.status)).map(r=>r.task_id)||[]);
  const actionable=t=>t.ready||t.status==='working'||t.status==='error'||active.has(t.id);
  const tasks=board.tasks.filter(t => (!stageFilter || t.stage===stageFilter) && (showAll || stageFilter || actionable(t)));
  $('productionBoard').innerHTML=`<div class="card production-overview"><div class="production-heading"><div><div class="eyebrow">DỰ ÁN WILDLIFE · ${paused?'TẠM DỪNG':'ĐANG MỞ'}</div><h2>${esc(p.name)}</h2><p>${esc(p.brief)}</p></div><div class="production-actions"><button type="button" data-production-control="${paused?'resume':'pause'}">${paused?'▶ Tiếp tục':'Ⅱ Tạm dừng'}</button><button type="button" data-production-export>⇩ Hồ sơ JSON</button></div></div><div class="production-stats"><div><b>${s.progress}%</b><span>Tiến độ công đoạn</span></div><div><b>${board.scenes.length}</b><span>Cảnh · ${board.scenes.filter(x=>x.difficult).length} cảnh khó</span></div><div><b>${s.planned_seconds?sec(s.planned_seconds):'—'}</b><span>Kịch bản / mục tiêu ${sec(s.target_seconds)}</span></div><div><b>${s.done}/${s.total}</b><span>Tác vụ xong · ${s.errors} lỗi</span></div></div><progress max="100" value="${s.progress}" aria-label="Tiến độ công đoạn">${s.progress}%</progress><p class="detail">${board.jobs.length} video job theo mã cảnh · ${board.characters.length} cá thể đã khóa.</p>${p.source_video?`<p class="detail">Video gốc: ${esc(p.source_video)}</p>`:'<p class="detail production-error">Chưa có video gốc: phân tích chỉ dựa trên yêu cầu.</p>'}${p.source_url?`<p class="detail">Link tham chiếu: ${esc(p.source_url)}</p>`:''}</div>
  ${agentPanel(board)}
  <div class="production-stages">${board.stages.map((stage,i)=>{const ts=board.tasks.filter(t=>t.stage===stage.id),done=ts.filter(t=>t.status==='done').length;return `<button type="button" class="card production-stage ${stageFilter===stage.id?'selected':''}" data-production-stage="${stage.id}"><small>${stage.per_scene?'THEO TỪNG CẢNH':'TOÀN PHIM'}</small><b>${esc(stage.name)}</b><span>${done}/${ts.length} xong${ts.some(t=>t.status==='error')?' · Có lỗi':''}</span></button>`;}).join('')}</div>
  <div class="card production-work"><div class="production-heading"><h2>${stageFilter?esc(stageName(stageFilter)):showAll?'Mọi tác vụ':'Việc cần làm bây giờ'}</h2><div class="production-actions"><button type="button" data-production-show-all>${showAll?'Chỉ việc cần làm':'Xem mọi tác vụ'}</button><button type="button" data-production-stage="">Tất cả công đoạn</button></div></div><p class="detail">Giao agent hoặc nhận xử lý thủ công. Tạm dừng khóa cập nhật bảng này; video đã giao Muse vẫn do Studio quản lý.</p>${tasks.length?'':'<p class="detail">Không có việc sẵn sàng: đang đợi agent chạy, đợi bạn duyệt kết quả ở Phòng điều phối AI, hoặc đợi video Muse.</p>'}<div class="production-task-list">${tasks.map(t=>`<article class="production-task"><div><small>${esc(sceneCode(t.scene_id))} · lần ${t.attempts}</small><h3>${esc(stageName(t.stage))}</h3><span class="production-status ${t.status}">${esc(names[t.status])}</span>${t.status==='waiting'&&!t.ready?`<small>${paused?'Dự án tạm dừng':`Chờ ${t.blocked_by.length} kết quả trước đó`}</small>`:''}${t.error?`<p class="production-error">${esc(t.error)}</p>`:''}${t.output?`<details><summary>Kết quả đã lưu</summary><pre>${esc(t.output)}</pre></details>`:''}</div><div class="production-actions">${taskAgentButtons(board,t)}${t.ready&&!taskHasAgent(board,t)?`<button type="button" data-production-start="${t.id}">Nhận xử lý</button>`:''}${!paused&&t.status==='working'&&!taskHasAgent(board,t)?`<button type="button" data-production-result="${t.id}">Ghi kết quả</button>`:''}${!paused&&t.status==='error'?`<button type="button" data-production-retry="${t.id}">Thử lại công đoạn</button>`:''}${t.job_id?`<button type="button" data-production-job="${t.job_id}">Mở video job</button>`:''}</div></article>`).join('')}</div></div>
  <details id="productionSceneDetails" class="card production-work" ${sceneDetailsOpen?'open':''}><summary>Hồ sơ ${board.scenes.length} cảnh & video job</summary><p class="detail">Giao Muse tạo job 10 giây, 16:9, ngân sách dự kiến 1 credit. Nếu Studio đang chạy hàng đợi, job có thể bắt đầu ngay. Kiểm tra ảnh nhân vật và thiết lập trong Studio.</p>${board.scenes.map(scene=>{const jobs=board.jobs.filter(j=>j.scene_id===scene.id),task=board.tasks.find(t=>t.stage==='video'&&t.scene_id===scene.id);return `<article class="production-task"><div><b>${esc(scene.code)}</b><p>${esc(scene.action||scene.source)}</p><small>Scene ID: ${scene.id}</small><details><summary>Prompt</summary><pre>${esc(scene.prompt||'Chưa có prompt được xác nhận.')}</pre></details>${jobs.map(j=>`<p><button type="button" data-production-job="${j.id}">Video job · ${esc(j.status)}</button> <small>${esc(j.id)}</small></p>`).join('')}</div>${!paused&&task?.status==='working'&&scene.prompt&&!jobs.some(j=>['waiting','running'].includes(j.status)||j.batch==='production-'+task.id+'-'+task.attempts)&&(!jobs.length||task.attempts>1)?`<button type="button" data-production-queue="${scene.id}">Giao Muse · dự kiến 1 credit</button>`:''}</article>`;}).join('')}</details>
  <details id="productionEventDetails" class="card production-work" ${eventsOpen?'open':''}><summary>Nhật ký · ${board.events.length} sự kiện gần nhất</summary><ol class="production-events">${board.events.map(e=>`<li><time>${esc(new Date(e.created*1000).toLocaleString('vi-VN'))}</time><b>${esc(actions[e.action]||e.action)}</b><span>${esc(e.task_id?sceneCode(board.tasks.find(t=>t.id===e.task_id)?.scene_id):'Dự án')}</span><p>${esc(e.detail)}</p></li>`).join('')}</ol></details>`;
  document.querySelectorAll('[data-agent-details]').forEach(e=>{e.open=expandedAgentDetails.has(e.dataset.agentDetails);});
}
function openResult(id) {
  editing=board.tasks.find(t=>t.id===id);
  $('productionTaskTitle').textContent=sceneCode(editing.scene_id)+' · '+stageName(editing.stage);
  $('productionTaskHint').textContent=editing.stage==='prompt'?'Dán prompt cuối cùng. Nội dung sẽ được lưu vào hồ sơ cảnh.':editing.stage==='video'?'Chọn job đã xong của đúng cảnh. Tạo video trong Studio, rồi quay lại cập nhật bảng.':'Ghi kết quả thực tế hoặc dẫn chiếu đến hồ sơ/tệp đã kiểm tra.';
  $('productionTaskOutput').value='';$('productionJobField').hidden=editing.stage!=='video';
  $('productionTaskJob').innerHTML='<option value="">Chọn video job…</option>'+board.jobs.filter(j=>j.scene_id===editing.scene_id&&j.status==='done'&&j.output).map(j=>`<option value="${j.id}">${esc(j.id)}</option>`).join('');
  $('productionTaskDialog').showModal();$('productionTaskOutput').focus();
}
export function initProduction(callbacks) {
  host=callbacks;
  initAgentUI({...callbacks,reload:()=>loadProduction()});
  setInterval(()=>{if(!$('productionView').hidden&&(board?.agent_state?.running||board?.agent_state?.runs.some(r=>r.status==='running')))loadProduction();},3000);
  $('productionRefresh').onclick=()=>loadProduction();
  $('productionProjects').onclick=e=>{const b=e.target.closest('[data-production-project]');if(b){stageFilter='';loadProduction(b.dataset.productionProject);}};
  $('productionPickVideo').onclick=()=>run(async()=>{const r=await host.api('/api/production/pick-video',{});if(r.path){sourceVideo=r.path;$('productionVideoPath').textContent=r.path;}});
  $('productionCreate').onsubmit=e=>{e.preventDefault();run(async()=>{const data=await host.api('/api/production/create',{name:$('productionName').value,source_url:$('productionSource').value,brief:$('productionBrief').value,source_video:sourceVideo,target_seconds:Number($('productionTarget').value)});$('productionCreate').reset();sourceVideo='';$('productionVideoPath').textContent='Chưa chọn video gốc';stageFilter='';await loadProduction(data.project.id);host.toast('Đã tạo dự án. Bắt đầu từ 01 · Phân tích video gốc.');});};
  $('productionEnable').onclick=()=>run(async()=>{const id=$('productionExisting').value;if(!id)throw Error('Chọn dự án trước.');await host.api('/api/production/enable',{project_id:id});stageFilter='';await loadProduction(id);});
  $('productionBoard').onclick=e=>run(async()=>{
    const b=e.target.closest('button');if(!b)return;
    if(await handleAgentClick(b))return;
    if(b.hasAttribute('data-production-stage')){stageFilter=b.dataset.productionStage;render();}
    if(b.hasAttribute('data-production-show-all')){showAll=!showAll;render();}
    if(b.dataset.productionControl){board=await host.api('/api/production/control',{project_id:selected,action:b.dataset.productionControl});await loadProduction();}
    if(b.dataset.productionStart){board=await host.api('/api/production/task',{task_id:b.dataset.productionStart,action:'start'});render();openResult(b.dataset.productionStart);}
    if(b.dataset.productionResult)openResult(b.dataset.productionResult);
    if(b.dataset.productionRetry){board=await host.api('/api/production/task',{task_id:b.dataset.productionRetry,action:'retry'});render();}
    if(b.dataset.productionJob)host.openJob(b.dataset.productionJob);
    if(b.dataset.productionQueue){const scene=board.scenes.find(s=>s.id===b.dataset.productionQueue),task=board.tasks.find(t=>t.stage==='video'&&t.scene_id===scene.id);await host.api('/api/jobs',{prompts:[scene.prompt],cost:1,batch:'production-'+task.id+'-'+task.attempts,scene_ids:[scene.id],options:{duration:10,aspect:'16:9'}});host.toast('Đã thêm 1 cảnh vào Studio · ngân sách dự kiến 1 credit. Kiểm tra ảnh nhân vật và thiết lập trước khi chạy.');await loadProduction();}
    if(b.hasAttribute('data-production-export')){const fresh=await get('/api/production/'+selected),blob=new Blob([JSON.stringify(fresh,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='wildlife-'+selected+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  });
  $('productionTaskClose').onclick=()=>$('productionTaskDialog').close();
  async function save(action){if(!$('productionTaskOutput').value.trim())throw Error('Cần nhập kết quả hoặc lý do lỗi.');board=await host.api('/api/production/task',{task_id:editing.id,action,output:$('productionTaskOutput').value,job_id:$('productionTaskJob').value||null});$('productionTaskDialog').close();await loadProduction();host.toast(action==='complete'?'Đã lưu kết quả và mở bước kế tiếp.':'Đã lưu lỗi. Thử lại công đoạn khi đã xử lý nguyên nhân.');}
  $('productionTaskForm').onsubmit=e=>{e.preventDefault();run(()=>save('complete'));};
  $('productionTaskFail').onclick=()=>run(()=>save('fail'));
}
