const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const providers={gemini:'Gemini',chatgpt:'ChatGPT / OpenAI',claude:'Claude / Anthropic'};
const transports={browser:'Trình duyệt trên máy này',api:'API riêng (khóa của bạn)',gateway:'Máy chủ dùng chung'};
const roles={research:'01 · Reference Analyst (phân tích video gốc)',script:'02 · Story Director (kịch bản toàn phim)',bible:'03 · Character Agent (cá thể & môi trường)',prompt:'03 · Prompt Agent (prompt từng cảnh)',shot_qc:'05 · Visual QC (QC cảnh & nối cảnh)',edit:'06 · Editor Agent (chọn đoạn, điểm vào/ra)',sound:'06 · Sound Agent (lớp tiếng tự nhiên)',film_qc:'07 · Film Reviewer (QC toàn phim)'};
const transportList=()=>Object.fromEntries(Object.entries(transports).filter(([id])=>id!=='gateway'||current?.agent_state?.gateway_enabled));
const stageProblems=(board,stage)=>board.agent_state?.readiness?.stages?.[stage]||[];
const sec=v=>{v=Number(v)||0;const m=Math.floor(v/60),s=v-m*60;return m?`${m}:${s.toFixed(1).padStart(4,'0')}`:`${s.toFixed(1)}s`;};
let host,current;
const labels={running:'Đang chạy',review:'Chờ bạn kiểm tra',done:'Đã áp dụng',rejected:'Đã trả lại',superseded:'Đã thay thế do làm lại'};
export function taskAgentButtons(board,task){
 const state=board.agent_state;if(!state)return '';
 const active=state.runs.find(r=>r.task_id===task.id&&['running','review'].includes(r.status));
 if(active)return `<small>${esc(labels[active.status])} · ${esc(active.phase)}</small>`;
 if(task.stage==='video'&&['waiting','working'].includes(task.status)&&board.jobs.some(j=>j.scene_id===task.scene_id&&j.status==='done'&&j.created>=task.take_after))return `<button type="button" data-agent-sync="${task.id}">Đồng bộ take Muse</button>`;
 if(task.ready&&task.stage!=='video'){const problems=stageProblems(board,task.stage);const label=task.stage==='delivery'?'Kiểm tra & đóng gói':task.stage==='pilot'?'Tổng hợp tạo thử':'✦ Giao agent';
  if(problems.length)return `<button type="button" class="accent-outline" disabled title="${esc(problems.join(' '))}">${label}</button><small class="production-error">${esc(problems[0])}</small>`;
  return `<button type="button" class="accent-outline" data-agent-run="${task.id}">${label}</button>`;}
 return '';
}
export function taskHasAgent(board,task){return board.agent_state?.runs.some(r=>r.task_id===task.id&&['running','review'].includes(r.status));}

function managerBlock(board){
 const state=board.agent_state,b=state.budget||{},cfg=state.config,paused=board.project.production_status==='paused';
 const reviews=state.runs.filter(r=>r.status==='review');
 const count=(stage,fn)=>board.tasks.filter(t=>t.stage===stage&&fn(t)).length;
 const readyVideo=board.tasks.filter(t=>t.stage==='video'&&(t.ready||t.status==='working')&&!board.jobs.some(j=>j.scene_id===t.scene_id&&j.created>=t.take_after)&&board.scenes.find(s=>s.id===t.scene_id)?.prompt).length;
 const syncable=board.tasks.filter(t=>t.stage==='video'&&['waiting','working'].includes(t.status)&&board.jobs.some(j=>j.scene_id===t.scene_id&&j.status==='done'&&j.created>=t.take_after)).length;
 const reviewOf=stage=>reviews.filter(r=>board.tasks.find(t=>t.id===r.task_id)?.stage===stage&&!r.error&&r.result?.passed!==false).length;
 const next=board.tasks.find(t=>t.ready);
 return `<div class="production-manager"><div class="production-heading"><div><b>AI Manager</b><p class="detail">Giữ yêu cầu, giao việc theo thứ tự, theo dõi ngân sách và dừng ở mỗi điểm duyệt.</p></div>
  <label class="check"><input type="checkbox" data-agent-auto ${cfg.auto_run?'checked':''} ${paused?'disabled':''}><span>Tự chạy tới điểm duyệt</span></label></div>
  <div class="production-stats"><div><b>${b.calls||0}/${b.max_calls||0}</b><span>Lượt AI đã dùng</span></div><div><b>${(b.api_tokens||0).toLocaleString('vi-VN')}</b><span>Token API (nếu dùng API)</span></div><div><b>${reviews.length}</b><span>Kết quả chờ bạn duyệt</span></div><div><b>${state.running?'Đang chạy':'Rảnh'}</b><span>${next?'Kế tiếp: '+esc(board.stages.find(s=>s.id===next.stage)?.name||next.stage):'Chưa có việc sẵn sàng'}</span></div></div>
  <div class="production-actions">${readyVideo?`<button type="button" data-production-queue-all>Giao Muse ${readyVideo} cảnh sẵn sàng · dự kiến ${readyVideo} credit</button>`:''}${syncable?`<button type="button" data-agent-sync-all>Đồng bộ ${syncable} take Muse đã xong</button>`:''}${reviewOf('prompt')?`<button type="button" data-agent-approve-batch="prompt">Duyệt ${reviewOf('prompt')} prompt</button>`:''}${reviewOf('shot_qc')?`<button type="button" data-agent-approve-batch="shot_qc">Duyệt ${reviewOf('shot_qc')} QC cảnh đạt</button>`:''}</div>
  <p class="detail">${cfg.discussion_rounds} vòng phản biện · tối đa ${cfg.max_calls} lượt AI mỗi phần. Đăng nhập AI trong Cài đặt. Video Muse và bước tạo thử cảnh khó luôn cần bạn bấm.</p>${Object.values(cfg.agents).some(a=>(a.transport||'browser')==='browser'&&[a.provider,a.reviewer,a.consultant].includes('claude'))&&state.providers.claude?'<button type="button" data-agent-login="claude">Đăng nhập Claude</button>':''}</div>`;
}

function soundBlock(board){
 const sounds=board.agent_state.sounds||[];
 return `<details class="production-sounds" ${sounds.length?'':'open'}><summary>Thư viện âm thanh tự nhiên · ${sounds.length} file</summary><p class="detail">Sound Agent chỉ chọn trong thư viện này (gió, nước, côn trùng, bước chân, tiếng động vật…). Không dùng nhạc hay lời thoại. Chưa có file thì bản dựng giữ âm thanh gốc của take.</p>
  <label class="button-link">＋ Thêm file âm thanh<input type="file" accept=".wav,.mp3,.m4a,.aac,.ogg,.flac" multiple data-sound-upload hidden></label>
  <ul class="production-sound-list">${sounds.map(s=>`<li><audio controls preload="none" src="/api/production/sound/${s.id}"></audio><input data-sound-name="${s.id}" value="${esc(s.name)}" maxlength="120" aria-label="Tên âm thanh"><input data-sound-tags="${s.id}" value="${esc(s.tags)}" maxlength="300" placeholder="Nhãn: gió, núi, bước chân" aria-label="Nhãn"><small>${sec(s.duration)}</small><button type="button" data-sound-save="${s.id}">Lưu</button><button type="button" data-sound-delete="${s.id}">Xóa</button></li>`).join('')}</ul></details>`;
}

function frames(run,list){return list?.length?`<div class="production-evidence">${list.map(f=>`<figure><img src="/api/production/run/${run.id}/frame/${esc(f.name)}" alt="Khung hình tại ${f.time} giây" loading="lazy"><figcaption>${sec(f.time)}</figcaption></figure>`).join('')}</div>`:'';}

function resultView(run,stage,board){
 const r=run.result||{};
 if(stage==='research'&&r.shots&&r.counts){const c=r.counts,v=r.verification||{},st=r.story||{};return `<p class="detail">MASTER PROMPT V3.0 · ${sec(c.duration)} · ${v.width}×${v.height} · ${v.fps||'?'} fps · ${v.audio?'có audio':'không audio'} · ${c.shots} shot (${c.verified} đã phân tích, ${c.unverified} chưa xác minh) · ${c.chapters} chương · ${c.rows} hàng Excel</p>
  <p>${esc(r.summary)}</p>${r.qc&&!r.qc.ok?`<p class="production-error">QC timeline: ${esc(r.qc.issues.join(' '))}</p>`:''}
  <details><summary>5W1H</summary><table class="production-table"><tbody>${['who','what','where','when','why','how'].map(k=>`<tr><td>${k.toUpperCase()}</td><td>${esc(st[k]||'Chưa xác minh')}</td></tr>`).join('')}</tbody></table></details>
  <details><summary>${(st.chapters||[]).length} chương · cấu trúc & nhịp dựng</summary><p class="detail">${esc(st.structure||'')}</p><p class="detail">${esc(st.editing||'')}</p><table class="production-table"><thead><tr><th>Chương</th><th>Thời gian</th><th>Diễn biến</th><th>Shot</th></tr></thead><tbody>${(st.chapters||[]).map(ch=>`<tr><td>${esc(ch.name)}</td><td>${sec(ch.start)}–${sec(ch.end)}</td><td>${esc(ch.summary||'')}${ch.climax?`<small>Cao trào: ${esc(ch.climax)}</small>`:''}</td><td>${esc((ch.shots||[]).join(', '))}</td></tr>`).join('')}</tbody></table></details>
  <details><summary>Shot by shot · ${r.shots.length} shot</summary><table class="production-table"><thead><tr><th>Shot</th><th>Hành động (mở đầu → kết thúc)</th><th>Camera</th><th>Điểm cắt · xác minh</th></tr></thead><tbody>${r.shots.map(x=>{const a=x.analysis||{},ac=a.action||{},cam=a.camera||{};return `<tr><td>${x.id}<small>${sec(x.start)}–${sec(x.end)}</small></td><td>${esc(ac.opening||'Chưa xác minh')}<small>→ ${esc(ac.ending||'Chưa xác minh')}</small></td><td>${esc([cam.shot_size,cam.angle,cam.movement].filter(Boolean).join(' · ')||'Chưa xác minh')}</td><td>${esc(x.cut_confidence)}<small>${esc(a.verification||'')} · ${esc(a.ai_difficulty||'')}</small></td></tr>`;}).join('')}</tbody></table></details>
  <details><summary>Cần kiểm tra lại · ${(r.qc_items||[]).length} mục</summary><ul>${(r.qc_items||[]).slice(0,80).map(q=>`<li><b>${esc(q.category)}</b> · ${esc(q.where)} — ${esc(q.detail)} <small>(${esc(q.status)})</small></li>`).join('')}</ul></details>
  <p class="detail">Giới hạn: ${esc((r.limitations||[]).join(' '))}</p>
  <div class="production-actions"><a class="button-link" href="/api/production/run/${run.id}/report.xlsx">⇩ Tải Excel phân tích (5 sheet)</a></div>${frames(run,r.evidence?.frames)}
  <p class="detail">Theo Master Prompt: dừng tại đây. Duyệt kết quả = yêu cầu bước sáng tạo kịch bản AI mới (02).</p>`;}
 if(stage==='research'&&r.timeline)return `<p>${esc(r.summary)}</p>${r.reference_video_verified?'':'<p class="production-error">Chưa phân tích video gốc thật (không có file). Kết quả chỉ là phác thảo từ yêu cầu.</p>'}<table class="production-table"><thead><tr><th>Thời gian</th><th>Diễn biến</th><th>Góc máy</th><th>Bằng chứng</th></tr></thead><tbody>${r.timeline.map(i=>`<tr class="${i.uncertain?'uncertain':''}"><td>${sec(i.start)}–${sec(i.end)}</td><td>${esc(i.event)}${i.action?`<small>${esc(i.action)}</small>`:''}${i.uncertain?'<small>⚠ chưa rõ</small>':''}</td><td>${esc(i.camera||'')}</td><td>${(i.evidence||[]).map(sec).join(', ')}</td></tr>`).join('')}</tbody></table>${r.audio_notes?`<p class="detail">Âm thanh (đoán từ hình): ${esc(r.audio_notes)}</p>`:''}${frames(run,r.evidence?.frames)}`;
 if(stage==='script'&&r.scenes)return `<p><b>${esc(r.title)}</b> — ${esc(r.logline)}</p><p class="detail">${r.scenes.length} cảnh · tổng ${sec(r.total_seconds)} / mục tiêu ${sec(board.project.target_seconds)} · ${r.scenes.filter(s=>s.difficult).length} cảnh khó sẽ tạo thử trước. Duyệt sẽ tạo danh sách cảnh SCENE_001….</p><table class="production-table"><thead><tr><th>Mã</th><th>Giây</th><th>Mục đích</th><th>Hành động</th></tr></thead><tbody>${r.scenes.map((s,i)=>`<tr><td>SCENE_${String(i+1).padStart(3,'0')}${s.difficult?' <small>khó</small>':''}</td><td>${s.duration}</td><td>${esc(s.purpose)}</td><td>${esc(s.action)}</td></tr>`).join('')}</tbody></table>`;
 if(stage==='bible'&&r.subjects)return `<ul>${r.subjects.map(s=>`<li><b>${esc(s.id)}</b> · ${esc(s.species)} — ${esc(s.appearance)} ${esc(s.distinguishing_marks||'')}</li>`).join('')}</ul><p class="detail">Môi trường: ${esc(Object.values(r.environment||{}).filter(Boolean).join(' · '))}</p>`;
 if(stage==='edit'&&r.rows)return `<p class="detail">Tổng ${sec(r.total_seconds)} · ${(r.checks||[]).map(c=>`${c.ok?'✓':'⚠'} ${esc(c.name)}: ${esc(c.detail)}`).join(' · ')}</p><table class="production-table"><thead><tr><th>#</th><th>File nguồn</th><th>Phần dùng</th><th>Vị trí trên phim</th><th>Âm thanh</th></tr></thead><tbody>${r.rows.map(x=>`<tr><td>${x.order}</td><td>${esc(x.file)}<small>${esc(x.scene)}</small></td><td>${sec(x.source_in)}–${sec(x.source_out)}</td><td>${sec(x.film_start)}–${sec(x.film_end)}</td><td>${esc(x.audio)}</td></tr>`).join('')}</tbody></table><div class="production-actions"><a class="button-link" href="/api/production/run/${run.id}/plan.csv">⇩ Kế hoạch dựng (CSV cho CapCut)</a><a class="button-link" href="/api/production/run/${run.id}/plan.json">⇩ JSON</a></div>`;
 if(stage==='pilot')return `<p>${esc(r.note||'')}</p>${(r.difficult_scenes||[]).map(s=>`<p><b>${esc(s.scene)}</b> <small>${esc(String(s.qc||'').slice(0,300))}</small></p>`).join('')}`;
 if(stage==='delivery'&&r.checks)return `<p class="detail">${r.checks.map(c=>`${c.ok?'✓':'⚠'} ${esc(c.name)}: ${esc(c.detail)}`).join(' · ')}</p>`;
 return '';
}

export function agentPanel(board){
 current=board;
 const state=board.agent_state;if(!state)return '';
 const paused=board.project.production_status==='paused';
 return `<section class="card production-work"><div class="production-heading"><div><div class="eyebrow">AI AGENTS · HỘI Ý TRONG TOOL</div><h2>Phòng điều phối AI</h2></div><div class="production-actions"><button type="button" data-agent-connections>Kết nối AI</button><button type="button" data-agent-config>Cấu hình phòng ban</button><button type="button" data-agent-stop>Dừng agent</button></div></div>
 ${managerBlock(board)}${soundBlock(board)}
 <div class="production-agent-runs">${state.runs.length?state.runs.map(run=>{const task=board.tasks.find(t=>t.id===run.task_id),result=run.result||{},stage=task?.stage;return `<article class="production-agent-run"><div class="production-heading"><b>${esc(board.stages.find(s=>s.id===stage)?.name||stage)} ${task?.scene_id?'· '+esc(board.scenes.find(s=>s.id===task.scene_id)?.code):''}</b><span class="production-status ${run.status==='done'?'done':run.status==='running'?'working':''}">${esc(labels[run.status]||run.status)}</span></div><p>${esc(run.phase)}</p>${run.error?`<p class="production-error">${esc(run.error)}</p>`:''}${result.passed===false?'<p class="production-error">QC không đạt. Xem bằng chứng và xử lý lỗi trước khi chạy lại.</p>':''}
 ${resultView(run,stage,board)}
 ${Object.keys(result).length?`<details data-agent-details="${run.id}-result"><summary>Kết quả agent (JSON)</summary><pre>${esc(JSON.stringify(result,null,2))}</pre></details>`:''}
 ${['shot_qc','film_qc'].includes(stage)?frames(run,result.evidence?.frames):''}
 ${result.render?`<video controls preload="metadata" src="/api/production/run/${run.id}/film"></video><p class="detail">Bản nháp dựng bằng FFmpeg theo kế hoạch: giữ hoặc tắt âm thanh gốc và trộn các lớp tiếng tự nhiên đã chọn. Bản cuối dựng trong CapCut theo file kế hoạch.</p>`:''}
 ${result.manifest?`<a class="button-link" href="/api/production/run/${run.id}/manifest">⇩ Tải hồ sơ bàn giao</a>`:''}
 ${run.calls.length?`<details data-agent-details="${run.id}-calls"><summary>Hội ý · ${run.calls.length} lượt</summary>${run.calls.map(call=>`<article class="production-agent-message"><b>${esc(providers[call.provider])} · ${esc(transports[call.transport]||'Trình duyệt')} · ${esc(call.role)}</b><small>${esc(call.status)}${call.model?' · '+esc(call.model):''}${call.usage&&call.usage!=='{}'?' · token: '+esc(call.usage):''}</small><pre>${esc(call.answer||'Chưa nhận được câu trả lời; kiểm tra trang AI trước khi thử lại.')}</pre></article>`).join('')}</details>`:''}
 ${run.status==='review'?`<div class="production-actions">${!run.error&&Object.keys(result).length&&result.passed!==false?`<button type="button" class="primary" data-agent-approve="${run.id}" ${paused?'disabled':''}>${stage==='edit'&&result.render_pending?'Duyệt kế hoạch dựng · Dựng bản nháp':stage==='edit'?'Duyệt bản nháp':stage==='script'?'Duyệt kịch bản · Tạo danh sách cảnh':stage==='pilot'?'Duyệt tạo thử · Chạy hàng loạt':'Duyệt kết quả'}</button>`:''}<button type="button" data-agent-reject="${run.id}">Trả lại tác vụ</button>${stage==='shot_qc'?`<button type="button" data-agent-rework="${run.id}" data-target="prompt">Lỗi prompt · sửa prompt cảnh này</button><button type="button" data-agent-rework="${run.id}" data-target="video">Lỗi video · tạo take mới</button>`:stage==='film_qc'?`<button type="button" data-agent-rework="${run.id}" data-target="edit">Sửa dựng hoặc âm thanh</button><select data-rework-scene="${run.id}" aria-label="Cảnh nguồn có lỗi"><option value="">Cảnh nguồn có lỗi…</option>${board.scenes.map(s=>`<option value="${s.id}">${esc(s.code)}</option>`).join('')}</select><button type="button" data-agent-rework="${run.id}" data-target="video">Làm lại cảnh đã chọn</button>`:''}</div>${stage==='film_qc'?`<label class="check"><input type="checkbox" data-agent-audio="${run.id}"><span>Tôi đã xem/nghe toàn bộ phim, xác nhận chỉ có âm thanh tự nhiên, không lời thoại, nhạc hay phụ đề.</span></label>`:''}`:''}</article>`;}).join(''):'<p class="detail">Bắt đầu từ “01 · Phân tích video gốc”: bấm Giao agent hoặc bật Tự chạy. Mỗi tác vụ có lịch sử riêng.</p>'}</div></section>`;
}
function configDialog(){
 const config=current.agent_state.config;
 $('productionAgentSettings').innerHTML=Object.entries(roles).map(([role,name])=>{const a=config.agents[role]||{};return `<fieldset data-agent-setting="${role}"><legend>${esc(name)}</legend><label>Cách kết nối<select data-agent-transport>${Object.entries(transportList()).map(([id,n])=>`<option value="${id}" ${id===(a.transport||'browser')?'selected':''}>${esc(n)}</option>`).join('')}</select></label><label>Agent chính<select data-agent-primary>${Object.entries(providers).map(([id,n])=>`<option value="${id}" ${id===a.provider?'selected':''}>${esc(n)}</option>`).join('')}</select></label><label>Agent phản biện<select data-agent-reviewer><option value="">Không phản biện</option>${Object.entries(providers).map(([id,n])=>`<option value="${id}" ${id===a.reviewer?'selected':''}>${esc(n)}</option>`).join('')}</select></label><label>Agent phản biện thứ hai (tùy chọn)<select data-agent-consultant><option value="">Không dùng</option>${Object.entries(providers).map(([id,n])=>`<option value="${id}" ${id===a.consultant?'selected':''}>${esc(n)}</option>`).join('')}</select></label><label>Model riêng cho agent chính (tùy chọn)<input data-agent-model maxlength="80" value="${esc(a.model||'')}" placeholder="Để trống: dùng model cấu hình theo kết nối"></label></fieldset>`;}).join('');
 $('productionAgentRounds').value=config.discussion_rounds;$('productionAgentMaxCalls').value=config.max_calls;$('productionAgentBudget').value=config.max_project_calls;
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
 $('productionAgentForm').onsubmit=async e=>{e.preventDefault();const button=e.submitter;button.disabled=true;try{const agents={};document.querySelectorAll('[data-agent-setting]').forEach(row=>{agents[row.dataset.agentSetting]={transport:row.querySelector('[data-agent-transport]').value,provider:row.querySelector('[data-agent-primary]').value,reviewer:row.querySelector('[data-agent-reviewer]').value,consultant:row.querySelector('[data-agent-consultant]').value,model:row.querySelector('[data-agent-model]').value.trim()};});await host.api('/api/production/agent/configure',{project_id:current.project.id,config:{agents,discussion_rounds:Number($('productionAgentRounds').value),max_calls:Number($('productionAgentMaxCalls').value),max_project_calls:Number($('productionAgentBudget').value)}});$('productionAgentDialog').close();await host.reload();host.toast('Đã lưu phân công agent.');}catch(e){host.toast(e.message,true);}finally{button.disabled=false;}};
 $('productionBoard').addEventListener('change',async e=>{
  const auto=e.target.closest('[data-agent-auto]'),upload=e.target.closest('[data-sound-upload]');
  try{
   if(auto){await host.api('/api/production/agent/auto',{project_id:current.project.id,enabled:auto.checked});await host.reload();host.toast(auto.checked?'AI Manager sẽ tự giao việc tới điểm cần bạn duyệt.':'Đã tắt tự chạy.');}
   if(upload){let added=0;for(const file of upload.files){const r=await fetch('/api/production/sounds/upload',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Sound-Name':encodeURIComponent(file.name),'X-Runner-Key':host.key()},body:file});const data=await r.json();if(!r.ok)throw Error(data.error||'Không thêm được '+file.name);added++;}upload.value='';await host.reload();host.toast(`Đã thêm ${added} file âm thanh tự nhiên.`);}
  }catch(error){if(auto)auto.checked=!auto.checked;host.toast(error.message,true);}
 });
}
export async function handleAgentClick(button){
 let path,body,message;
 if(button.hasAttribute('data-agent-connections')){connectionDialog();return true;}
 if(button.hasAttribute('data-agent-config')){configDialog();return true;}
 if(button.hasAttribute('data-agent-stop')){path='stop';body={};}
 if(button.dataset.agentLogin){path='login';body={provider:button.dataset.agentLogin};}
 if(button.dataset.agentRun){path='run';body={task_id:button.dataset.agentRun};}
 if(button.dataset.agentSync){path='sync-muse';body={task_id:button.dataset.agentSync};}
 if(button.hasAttribute('data-agent-sync-all')){path='sync-all';body={project_id:current.project.id};}
 if(button.dataset.agentApproveBatch){path='approve-batch';body={project_id:current.project.id,stage:button.dataset.agentApproveBatch};}
 if(button.dataset.agentApprove){path='approve';body={run_id:button.dataset.agentApprove,audio_confirmed:Boolean(document.querySelector(`[data-agent-audio="${button.dataset.agentApprove}"]`)?.checked)};}
 if(button.dataset.agentRework){path='rework';body={run_id:button.dataset.agentRework,target:button.dataset.target};
  const select=document.querySelector(`[data-rework-scene="${button.dataset.agentRework}"]`);
  if(button.dataset.target==='video'&&select){if(!select.value)throw Error('Chọn cảnh nguồn có lỗi trước.');body.scene_id=select.value;}}
 if(button.dataset.agentReject){path='reject';body={run_id:button.dataset.agentReject};}
 if(button.hasAttribute('data-production-queue-all')){const r=await host.api('/api/production/queue-muse',{project_id:current.project.id});await host.reload();host.toast(`Đã giao ${r.queued} cảnh lên Studio · ngân sách dự kiến ${r.queued} credit. Kiểm tra ảnh nhân vật trước khi chạy.`);return true;}
 if(button.dataset.soundSave){await host.api('/api/production/sounds/update',{id:button.dataset.soundSave,name:document.querySelector(`[data-sound-name="${button.dataset.soundSave}"]`).value,tags:document.querySelector(`[data-sound-tags="${button.dataset.soundSave}"]`).value});await host.reload();host.toast('Đã lưu tên và nhãn âm thanh.');return true;}
 if(button.dataset.soundDelete){if(!confirm('Xóa file âm thanh này khỏi thư viện?'))return true;await host.api('/api/production/sounds/delete',{id:button.dataset.soundDelete});await host.reload();return true;}
 if(!path)return false;
 const result=await host.api('/api/production/agent/'+path,body);await host.reload();
 message={run:'Agent đã nhận việc qua kết nối đã cấu hình.',stop:'Đã yêu cầu dừng agent. Kết quả đã gửi vẫn được giữ để kiểm tra.',
  'sync-all':`Đã đồng bộ ${result.synced} take${result.ambiguous?` · ${result.ambiguous} cảnh có nhiều take, chọn tay`:''}.`,
  'approve-batch':`Đã duyệt ${result.approved} kết quả${result.skipped?` · bỏ qua ${result.skipped}: ${(result.reasons||[]).join(' ')}`:''}.`}[path]||'Đã cập nhật công đoạn.';
 host.toast(message);
 return true;
}

function connectionDialog(){
 const state=current.agent_state;
 $('productionGatewayForm').hidden=!state.gateway_enabled;$('productionAPIDetails').open=!state.gateway_enabled;
 $('productionGatewayURL').value=state.gateway?.url||'';$('productionGatewayToken').value='';
 $('productionGatewayStatus').textContent=state.gateway?.configured?'Đã lưu mã truy cập. Để trống để giữ nguyên.':'Chưa có kết nối hoàn chỉnh. Nhận URL và mã từ chủ máy chủ.';
 $('productionAPISettings').innerHTML=Object.entries(state.api||{}).map(([id,p])=>`<fieldset data-api-provider="${id}"><legend>${esc(p.name)} · ${p.configured?'Đã cấu hình ('+esc(p.source)+')':'Chưa có khóa'}</legend><label>Khóa mới<input type="password" autocomplete="off" data-api-key placeholder="Để trống để giữ khóa đã lưu"></label><label>Model API<input data-api-model value="${esc(p.model)}" maxlength="120"></label><label>Giới hạn token đầu ra mỗi lần<input data-api-tokens type="number" min="64" max="32768" value="${p.max_output_tokens}"></label><div class="production-actions"><button type="button" data-api-save>Lưu</button><button type="button" data-api-test>Kiểm tra API (có thể tính phí)</button><button type="button" data-api-remove>Xóa khóa cục bộ</button></div></fieldset>`).join('');
 if(!$('productionConnectionDialog').open)$('productionConnectionDialog').showModal();
}
