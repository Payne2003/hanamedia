const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const providers={gemini:'Gemini',chatgpt:'ChatGPT / OpenAI',claude:'Claude / Anthropic'};
const transports={browser:'Trình duyệt trên máy này',api:'API riêng (khóa của bạn)',gateway:'Máy chủ dùng chung'};
const transportList=()=>Object.fromEntries(Object.entries(transports).filter(([id])=>id!=='gateway'||current?.agent_state?.gateway_enabled));
const stageProblems=(board,stage)=>board.agent_state?.readiness?.stages?.[stage]||[];
let host,current;
const labels={running:'Đang chạy',review:'Chờ bạn kiểm tra',done:'Đã áp dụng',rejected:'Đã trả lại',superseded:'Đã thay thế do làm lại'};
export function taskAgentButtons(board,task){
 const state=board.agent_state;if(!state)return '';
 const active=state.runs.find(r=>r.task_id===task.id&&['running','review'].includes(r.status));
 if(active)return `<small>${esc(labels[active.status])} · ${esc(active.phase)}</small>`;
 if(task.stage==='video'&&['waiting','working'].includes(task.status)&&board.jobs.some(j=>j.scene_id===task.scene_id&&j.status==='done'&&j.created>=task.take_after))return `<button type="button" data-agent-sync="${task.id}">Đồng bộ take Muse</button>`;
 if(task.ready&&task.stage!=='video'){const problems=stageProblems(board,task.stage);const label=task.stage==='delivery'?'Đóng gói hồ sơ':'✦ Giao agent';
  if(problems.length)return `<button type="button" class="accent-outline" disabled title="${esc(problems.join(' '))}">${label}</button><small class="production-error">${esc(problems[0])}</small>`;
  return `<button type="button" class="accent-outline" data-agent-run="${task.id}">${label}</button>`;}
 return '';
}
export function taskHasAgent(board,task){return board.agent_state?.runs.some(r=>r.task_id===task.id&&['running','review'].includes(r.status));}
export function agentPanel(board){
 current=board;
 const state=board.agent_state;if(!state)return '';
 const paused=board.project.production_status==='paused';
 return `<section class="card production-work"><div class="production-heading"><div><div class="eyebrow">AI AGENTS · HỘI Ý TRONG TOOL</div><h2>Phòng điều phối AI</h2></div><div class="production-actions"><button type="button" data-agent-connections>Kết nối AI</button><button type="button" data-agent-config>Cấu hình phòng ban</button><button type="button" data-agent-stop>Dừng agent</button></div></div><p class="detail">${state.config.discussion_rounds} vòng phản biện · tối đa ${state.config.max_calls} lượt AI mỗi tác vụ (dựng phim: mỗi cảnh). Đăng nhập AI trong Cài đặt.</p>${Object.values(state.config.agents).some(a=>(a.transport||'browser')==='browser'&&[a.provider,a.reviewer,a.consultant].includes('claude'))&&state.providers.claude?'<button type="button" data-agent-login="claude">Đăng nhập Claude</button>':''}
 <div class="production-agent-runs">${state.runs.length?state.runs.map(run=>{const task=board.tasks.find(t=>t.id===run.task_id),result=run.result||{},stage=task?.stage;return `<article class="production-agent-run"><div class="production-heading"><b>${esc(board.stages.find(s=>s.id===stage)?.name||stage)} ${task?.scene_id?'· '+esc(board.scenes.find(s=>s.id===task.scene_id)?.code):''}</b><span class="production-status ${run.status==='done'?'done':run.status==='running'?'working':''}">${esc(labels[run.status]||run.status)}</span></div><p>${esc(run.phase)}</p>${run.error?`<p class="production-error">${esc(run.error)}</p>`:''}${result.passed===false?'<p class="production-error">QC không đạt. Xem bằng chứng và xử lý lỗi trước khi chạy lại.</p>':''}
 ${Object.keys(result).length?`<details data-agent-details="${run.id}-result"><summary>Kết quả agent</summary><pre>${esc(JSON.stringify(result,null,2))}</pre></details>`:''}
 ${result.evidence?.frames?`<div class="production-evidence">${result.evidence.frames.map((f,i)=>`<figure><img src="/api/production/run/${run.id}/frame/${i+1}" alt="Khung hình bằng chứng tại ${f.time} giây" loading="lazy"><figcaption>${f.time}s</figcaption></figure>`).join('')}</div>`:''}
 ${result.render?`<video controls preload="metadata" src="/api/production/run/${run.id}/film"></video><p class="detail">Bản dựng giữ âm thanh nguồn; chưa tự xác nhận không có nhạc/lời thoại. Cảnh không có âm thanh được chèn khoảng lặng.</p>`:''}
 ${result.manifest?`<a class="button-link" href="/api/production/run/${run.id}/manifest">⇩ Tải hồ sơ bàn giao</a>`:''}
 ${run.calls.length?`<details data-agent-details="${run.id}-calls"><summary>Hội ý · ${run.calls.length} lượt</summary>${run.calls.map(call=>`<article class="production-agent-message"><b>${esc(providers[call.provider])} · ${esc(transports[call.transport]||'Trình duyệt')} · ${esc(call.role)}</b><small>${esc(call.status)}${call.model?' · '+esc(call.model):''}${call.usage&&call.usage!=='{}'?' · token: '+esc(call.usage):''}</small><pre>${esc(call.answer||'Chưa nhận được câu trả lời; kiểm tra trang AI trước khi thử lại.')}</pre></article>`).join('')}</details>`:''}
 ${run.status==='review'?`<div class="production-actions">${!run.error&&Object.keys(result).length&&result.passed!==false?`<button type="button" class="primary" data-agent-approve="${run.id}" ${paused?'disabled':''}>${stage==='edit'&&result.render_pending?'Duyệt timeline · Bắt đầu render':stage==='edit'?'Duyệt bản dựng xem trước':'Duyệt kết quả'}</button>`:''}<button type="button" data-agent-reject="${run.id}">Trả lại tác vụ</button>${stage==='shot_qc'?`<button type="button" data-agent-rework="${run.id}" data-target="prompt">Sửa lại prompt cảnh này</button><button type="button" data-agent-rework="${run.id}" data-target="video">Yêu cầu take mới</button>`:stage==='film_qc'?`<button type="button" data-agent-rework="${run.id}" data-target="edit">Dựng lại phim</button>`:''}</div>${stage==='film_qc'?`<label class="check"><input type="checkbox" data-agent-audio="${run.id}"><span>Tôi đã xem/nghe toàn bộ phim, xác nhận chỉ có âm thanh tự nhiên, không lời thoại, nhạc hay phụ đề.</span></label>`:''}`:''}</article>`;}).join(''):'<p class="detail">Bấm “Giao agent” ở một công đoạn sẵn sàng để bắt đầu. Mỗi tác vụ có lịch sử riêng.</p>'}</div></section>`;
}
function configDialog(){
 const config=current.agent_state.config;
 $('productionAgentSettings').innerHTML=Object.entries(config.agents).map(([stage,a])=>`<fieldset data-agent-setting="${stage}"><legend>${esc(current.stages.find(s=>s.id===stage)?.name||stage)}</legend><label>Cách kết nối<select data-agent-transport>${Object.entries(transportList()).map(([id,n])=>`<option value="${id}" ${id===(a.transport||'browser')?'selected':''}>${esc(n)}</option>`).join('')}</select></label><label>Agent chính<select data-agent-primary>${Object.entries(providers).map(([id,n])=>`<option value="${id}" ${id===a.provider?'selected':''}>${esc(n)}</option>`).join('')}</select></label><label>Agent phản biện<select data-agent-reviewer><option value="">Không phản biện</option>${Object.entries(providers).map(([id,n])=>`<option value="${id}" ${id===a.reviewer?'selected':''}>${esc(n)}</option>`).join('')}</select></label><label>Agent phản biện thứ hai (tùy chọn)<select data-agent-consultant><option value="">Không dùng</option>${Object.entries(providers).map(([id,n])=>`<option value="${id}" ${id===a.consultant?'selected':''}>${esc(n)}</option>`).join('')}</select></label><label>Model riêng cho agent chính (tùy chọn)<input data-agent-model maxlength="80" value="${esc(a.model||'')}" placeholder="Để trống: dùng model cấu hình theo kết nối"></label></fieldset>`).join('');
 $('productionAgentRounds').value=config.discussion_rounds;$('productionAgentMaxCalls').value=config.max_calls;
 $('productionAgentDialog').showModal();
}
export function initAgentUI(callbacks){
 host=callbacks;
 $('productionConnectionClose').onclick=()=>$('productionConnectionDialog').close();
 $('productionGatewayForm').onsubmit=async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{await host.api('/api/production/agent/gateway-save',{url:$('productionGatewayURL').value,token:$('productionGatewayToken').value||null});$('productionGatewayToken').value='';await host.reload();connectionDialog();host.toast('Đã lưu địa chỉ và mã truy cập trên máy này.');}catch(e){host.toast(e.message,true);}finally{button.disabled=false;}};
 $('productionGatewayInfo').onclick=async()=>{try{const info=await host.api('/api/production/agent/gateway-info',{}),q=info.quota;$('productionGatewayStatus').textContent=`${q.name}: còn ${q.remaining_tokens.toLocaleString('vi-VN')} token dự trù và ${q.remaining_requests} yêu cầu trong ngày (${q.timezone}).`;}catch(e){host.toast(e.message,true);}};
 $('productionGatewayRemove').onclick=async()=>{try{await host.api('/api/production/agent/gateway-save',{url:$('productionGatewayURL').value,remove:true});await host.reload();connectionDialog();}catch(e){host.toast(e.message,true);}};
 $('productionAPISettings').onclick=async e=>{const button=e.target.closest('[data-api-save],[data-api-remove],[data-api-test]');if(!button)return;button.disabled=true;try{const row=button.closest('[data-api-provider]'),provider=row.dataset.apiProvider;if(button.hasAttribute('data-api-test')){const result=await host.api('/api/production/agent/api-test',{provider});host.toast('API trả lời thành công · '+(result.model||provider));}else{await host.api('/api/production/agent/api-save',{provider,key:row.querySelector('[data-api-key]').value||null,model:row.querySelector('[data-api-model]').value,max_output_tokens:Number(row.querySelector('[data-api-tokens]').value),remove:button.hasAttribute('data-api-remove')});row.querySelector('[data-api-key]').value='';await host.reload();connectionDialog();host.toast('Đã cập nhật cấu hình API.');}}catch(e){host.toast(e.message,true);}finally{button.disabled=false;}};
 $('productionAgentUseBrowser').onclick=()=>document.querySelectorAll('[data-agent-transport]').forEach(e=>{e.value='browser';});
 $('productionAgentClose').onclick=()=>$('productionAgentDialog').close();
 $('productionAgentForm').onsubmit=async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{const agents={};document.querySelectorAll('[data-agent-setting]').forEach(row=>{agents[row.dataset.agentSetting]={transport:row.querySelector('[data-agent-transport]').value,provider:row.querySelector('[data-agent-primary]').value,reviewer:row.querySelector('[data-agent-reviewer]').value,consultant:row.querySelector('[data-agent-consultant]').value,model:row.querySelector('[data-agent-model]').value.trim()};});await host.api('/api/production/agent/configure',{project_id:current.project.id,config:{agents,discussion_rounds:Number($('productionAgentRounds').value),max_calls:Number($('productionAgentMaxCalls').value)}});$('productionAgentDialog').close();await host.reload();host.toast('Đã lưu phân công agent.');}catch(e){host.toast(e.message,true);}finally{button.disabled=false;}};
}
export async function handleAgentClick(button){
 let path,body;
 if(button.hasAttribute('data-agent-connections')){connectionDialog();return true;}
 if(button.hasAttribute('data-agent-config')){configDialog();return true;}
 if(button.hasAttribute('data-agent-stop')){path='stop';body={};}
 if(button.dataset.agentLogin){path='login';body={provider:button.dataset.agentLogin};}
 if(button.dataset.agentRun){path='run';body={task_id:button.dataset.agentRun};}
 if(button.dataset.agentSync){path='sync-muse';body={task_id:button.dataset.agentSync};}
 if(button.dataset.agentApprove){path='approve';body={run_id:button.dataset.agentApprove,audio_confirmed:Boolean(document.querySelector(`[data-agent-audio="${button.dataset.agentApprove}"]`)?.checked)};}
 if(button.dataset.agentRework){path='rework';body={run_id:button.dataset.agentRework,target:button.dataset.target};}
 if(button.dataset.agentReject){path='reject';body={run_id:button.dataset.agentReject};}
 if(!path)return false;
 await host.api('/api/production/agent/'+path,body);await host.reload();
 host.toast(path==='run'?'Agent đã nhận việc qua kết nối đã cấu hình.':path==='stop'?'Đã yêu cầu dừng agent. Kết quả đã gửi vẫn được giữ để kiểm tra.':'Đã cập nhật công đoạn.');
 return true;
}

function connectionDialog(){
 const state=current.agent_state;
 $('productionGatewayForm').hidden=!state.gateway_enabled;$('productionAPIDetails').open=!state.gateway_enabled;
 $('productionGatewayURL').value=state.gateway?.url||'';$('productionGatewayToken').value='';
 $('productionGatewayStatus').textContent=state.gateway?.configured?'Đã lưu mã truy cập. Để trống để giữ nguyên.':'Chưa có kết nối hoàn chỉnh. Nhận URL và mã từ chủ máy chủ.';
 $('productionAPISettings').innerHTML=Object.entries(state.api||{}).map(([id,p])=>`<fieldset data-api-provider="${id}"><legend>${esc(p.name)} · ${p.configured?'Đã cấu hình ('+esc(p.source)+')':'Chưa có khóa'}</legend><label>Khóa mới<input type="password" autocomplete="off" data-api-key placeholder="Để trống để giữ khóa đã lưu"></label><label>Model API<input data-api-model value="${esc(p.model)}" maxlength="120"></label><label>Giới hạn token đầu ra mỗi lần<input data-api-tokens type="number" min="64" max="8192" value="${p.max_output_tokens}"></label><div class="production-actions"><button type="button" data-api-save>Lưu</button><button type="button" data-api-test>Kiểm tra API (có thể tính phí)</button><button type="button" data-api-remove>Xóa khóa cục bộ</button></div></fieldset>`).join('');
 if(!$('productionConnectionDialog').open)$('productionConnectionDialog').showModal();
}
