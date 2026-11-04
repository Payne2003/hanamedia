const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state=null,key='',filter='all',busy=false,pendingBatch=null,toastTimer,visibleJobs=[],mode='text',libraryImages=[];
const selectedJobs=new Set(),promptImages=new Map();
const thumbnailVideos=new Map();
const thumbnailObserver='IntersectionObserver' in window?new IntersectionObserver(entries=>{for(const entry of entries){if(!entry.isIntersecting)continue;const video=entry.target;thumbnailObserver.unobserve(video);video.src=video.dataset.videoSrc;video.load();}},{rootMargin:'120px'}):null;
const statusNames={waiting:'Chờ',running:'Đang chạy',done:'Xong',error:'Lỗi'};
const accountNames={new:'Chưa kiểm tra',authenticating:'Đang đăng nhập',ready:'Sẵn sàng',busy:'Đang chạy',attention:'Cần kiểm tra',reviewing:'Đang mở kiểm tra'};
function toast(message,error=false){$('toast').textContent=message;$('toast').hidden=false;$('toast').dataset.error=String(error);clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,error?12000:5500);}
async function api(path,body){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Runner-Key':key},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(data.error||'Yêu cầu thất bại.');return data;}
function splitPrompts(){const value=$('prompts').value;const mode=$('separator').value;return value.split(mode==='blank'?/\r?\n\s*\r?\n/:mode==='custom'?/^\s*---\s*$/m:/\r?\n/).map(x=>x.trim()).filter(Boolean);}
function countPrompts(){const count=mode==='image_generate'?Number(Boolean($('prompts').value.trim())):splitPrompts().length;$('promptCount').textContent=`${count} ${mode==='image_generate'?'ảnh':'tác vụ'}`;try{localStorage.setItem('huy-runner-draft',$('prompts').value);}catch{}renderImageAssignments();}
function renderMode(){const imageVideo=mode==='image_video',imageGenerate=mode==='image_generate';document.querySelectorAll('.creation-tabs [data-mode]').forEach(button=>button.classList.toggle('active',imageGenerate?button.dataset.mode==='image_generate':button.dataset.mode!=='image_generate'));document.querySelectorAll('.video-submodes [data-mode]').forEach(button=>button.classList.toggle('active',button.dataset.mode===mode));$('composerTitle').textContent=imageGenerate?'Tạo ảnh':imageVideo?'Ảnh → video':'Tạo video';$('importButton').hidden=imageGenerate;document.querySelectorAll('.video-only').forEach(element=>element.hidden=imageGenerate);$('flowPanel').hidden=!imageGenerate;document.querySelector('.image-assignment').hidden=!imageVideo;$('prompts').placeholder=imageGenerate?'Mô tả ảnh bạn muốn tạo trong Google Flow…':'Mỗi dòng là một prompt. Viết mô tả video bạn muốn tạo…';countPrompts();}
function syncSettings(){if(!state)return;$('folderPath').textContent=state.engine.output_folder||'Chưa chọn thư mục lưu';$('accountCount').textContent=String(state.accounts.length);$('loginAllStatus').textContent=state.engine.login_all||'';$('loginAll').disabled=Boolean(state.engine.login_all_running);$('pause').disabled=!state.engine.enabled;$('run').disabled=state.engine.enabled;$('flowStatus').textContent=state.engine.flow_status||'Ảnh Flow tải xuống được lưu vào thư viện riêng của tool.';}
function jobProgress(job,now=Date.now()){
  if(job.status==='done')return 100;
  if(job.status!=='running')return Number(job.progress)||0;
  const progress=Number(job.progress)||0;
  if(job.phase==='Muse đang tạo video'){
    const elapsed=Math.max(0,now/1000-Number(job.updated||now/1000));
    return Math.min(90,Math.max(progress,20+Math.floor(elapsed*.2)));
  }
  return Math.min(99,progress);
}
function renderJobs(){
  if(!state)return;
  const totals=state.totals,all=Object.values(totals).reduce((a,b)=>a+b,0);
  $('total').textContent=all;$('n-all').textContent=all;
  $('topTotal').textContent=all;$('topFiles').textContent=totals.done;$('topRunning').textContent=state.accounts.filter(account=>account.state==='busy').length;$('headerRuns').textContent=$('topRunning').textContent;
  for(const status of Object.keys(statusNames))$(`n-${status}`).textContent=totals[status];
  const query=$('search').value.toLowerCase();
  const jobs=state.jobs.filter(job=>(filter==='all'||job.status===filter)&&job.prompt.toLowerCase().includes(query));
  visibleJobs=jobs;
  for(const id of selectedJobs){
    const job=state.jobs.find(item=>item.id===id);
    if(!job||job.status==='running')selectedJobs.delete(id);
  }
  $('empty').hidden=jobs.length>0;
  $('empty').querySelector('h3').textContent=all?'Chưa có tác vụ phù hợp':'Một ý tưởng, một video';
  $('empty').querySelector('p').textContent=all?'Chọn Tất cả hoặc thay đổi từ khóa tìm kiếm.':'Nhập prompt ở khung bên phải để bắt đầu.';
  const accounts=new Map(state.accounts.map((account,index)=>[account.id,`Tài khoản ${index+1}`]));
  $('rows').innerHTML=jobs.map(job=>{
    const assigned=job.account_id||job.assigned_account_id;
    const progress=jobProgress(job),estimated=job.status==='running'&&job.phase==='Muse đang tạo video';
    const progressLabel=job.status==='done'?'Hoàn thành':job.status==='error'?'Dừng khi gặp lỗi':job.phase;
    const thumbnail=job.status==='done'&&job.output
      ?`<button class="video-thumb" type="button" data-play="${job.id}" aria-label="Xem video ${esc(job.name)}"><video data-thumb-job="${job.id}" data-video-src="/api/media/${job.id}" muted playsinline preload="none"></video><span class="thumb-play">▶</span><span class="thumb-placeholder">▶</span></button>`
      :job.image_count?`<img src="/api/image/${job.id}/0" alt="Ảnh tham chiếu" loading="lazy">`:'<span class="thumb-placeholder">▣</span>';
    return `<tr class="${job.status==='running'?'is-running':''}"><td class="select-col"><input class="job-select" data-select-job="${job.id}" type="checkbox" aria-label="Chọn ${esc(job.name)}" ${selectedJobs.has(job.id)?'checked':''} ${job.status==='running'?'disabled':''}></td><td>${esc(job.name.replace('Prompt ','')||'—')}</td><td class="thumb-col"><div class="job-thumb">${thumbnail}</div></td><td class="prompt-cell"><strong>${esc(job.name)}</strong>${job.status==='done'&&job.output?`<span class="job-filename">${esc(job.output.split(/[\\\\/]/).pop())}</span>`:''}<div class="prompt-text" title="${esc(job.prompt)}">${esc(job.prompt)}</div></td><td><span class="type-label">${job.generation_mode==='image'?'▧ Ảnh → video':'▣ Video'}</span></td><td><span class="account-chip">${esc(accounts.get(assigned)||(assigned&&job.status!=='waiting'?'TK đã xóa':'Chờ tài khoản'))}</span></td><td><span class="badge ${esc(job.status)}">${esc(statusNames[job.status])}</span><div class="job-progress ${esc(job.status)}" role="progressbar" aria-label="Tiến độ ${esc(job.name)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}"><span data-progress="${progress}"></span></div><div class="progress-caption"><b>${estimated?'~':''}${progress}%</b><span class="detail ${job.error?'error':''}" title="${esc(job.error||job.phase)}">${esc(progressLabel)}</span></div></td><td><div class="row-actions"><button data-copy="${job.id}" title="Chép prompt" aria-label="Chép prompt">⧉</button>${job.status==='error'&&!job.submitted?`<button data-retry="${job.id}" title="Thử lại" aria-label="Thử lại">↻</button>`:''}${job.status==='done'?`<button data-play="${job.id}" title="Xem video" aria-label="Xem video">◉</button><a href="/api/video/${job.id}" title="Tải video" aria-label="Tải video">⇩</a>`:''}${job.account_id&&job.status==='error'?`<button data-review="${job.account_id}" title="Mở phiên Muse" aria-label="Mở phiên Muse">↗</button>`:''}</div></td></tr>`;
  }).join('');
  // Chính sách bảo mật (CSP) chặn style="..." trong HTML: đặt độ rộng thanh tiến độ bằng JS.
  $('rows').querySelectorAll('[data-progress]').forEach(bar=>bar.style.width=`${bar.dataset.progress}%`);
  attachVideoThumbnails();
  $('shown').textContent=`Hiển thị ${jobs.length}/${all} tác vụ${all>2000?' · 2.000 tác vụ gần nhất':''}`;
  $('queueHint').textContent=state.engine.enabled
    ?(totals.running?`${totals.running} tài khoản đang xử lý · ${totals.waiting} tác vụ đang chờ`:totals.waiting?'Đợi tài khoản sẵn sàng và đủ ngân sách. Kiểm tra mục Tài khoản.':'Đã hết hàng đợi · sẵn sàng nhận prompt mới')
    :(totals.running?'Đang hoàn tất tác vụ hiện tại; đã dừng nhận thêm':'Đã dừng nhận thêm · hàng đợi được giữ lại');
  updateSelectionControls();
}
function attachVideoThumbnails(){
  const videos=[...$('rows').querySelectorAll('video[data-thumb-job]')],visible=new Set(videos.map(video=>video.dataset.thumbJob));
  for(const video of videos){
    const id=video.dataset.thumbJob,cached=thumbnailVideos.get(id);
    if(cached&&cached!==video){video.replaceWith(cached);continue;}
    thumbnailVideos.set(id,video);
    if(video.dataset.thumbnailReady)continue;
    video.dataset.thumbnailReady='true';
    video.addEventListener('loadedmetadata',()=>{
      if(Number.isFinite(video.duration)&&video.duration>0)video.currentTime=Math.min(.5,video.duration/2);
    });
    video.addEventListener('loadeddata',()=>video.classList.add('has-frame'),{once:true});
    video.addEventListener('seeked',()=>video.classList.add('has-frame'),{once:true});
    video.addEventListener('error',()=>video.classList.add('thumbnail-failed'),{once:true});
    if(thumbnailObserver)thumbnailObserver.observe(video);
    else{video.src=video.dataset.videoSrc;video.load();}
  }
  for(const [id,video] of thumbnailVideos){
    if(visible.has(id))continue;
    thumbnailObserver?.unobserve(video);
    video.pause();video.removeAttribute('src');video.load();thumbnailVideos.delete(id);
  }
}
function renderImageAssignments(){
  const prompts=splitPrompts(),select=$('imagePromptIndex'),previous=select.value,imageMode=mode==='image_video';
  select.innerHTML=prompts.length?prompts.map((prompt,index)=>`<option value="${index}">${index+1}. ${esc(prompt.slice(0,55))}${prompt.length>55?'…':''}</option>`).join(''):'<option value="">Nhập prompt trước</option>';
  if(previous&&Number(previous)<prompts.length)select.value=previous;
  $('chooseImages').disabled=!prompts.length||!imageMode;
  $('chooseLibrary').disabled=!prompts.length||!imageMode;
  select.disabled=!prompts.length||!imageMode;
  const index=Number(select.value),entry=promptImages.get(index);
  $('assignedImages').innerHTML=entry?`${entry.prompt!==prompts[index]?'<div class="detail error">Prompt đã đổi; hãy gắn lại ảnh tham chiếu.</div>':''}${entry.assets.map((asset,fileIndex)=>`<span class="image-chip" title="${esc(asset.name)}"><img src="/api/library/image/${asset.token}" alt=""><span>${esc(asset.name)}</span><button type="button" data-remove-image="${index}:${fileIndex}" aria-label="Bỏ ảnh ${esc(asset.name)}">×</button></span>`).join('')}`:'';
}
function updateSelectionControls(){if(!state)return;const selected=[...selectedJobs].filter(id=>state.jobs.some(job=>job.id===id&&job.status!=='running'));$('selectedCount').textContent=selected.length;$('deleteSelected').disabled=!selected.length;const selectable=visibleJobs.filter(job=>job.status!=='running'),count=selectable.filter(job=>selectedJobs.has(job.id)).length;const allBox=$('selectVisible');allBox.checked=selectable.length>0&&count===selectable.length;allBox.indeterminate=count>0&&count<selectable.length;}
async function uploadImage(file){const response=await fetch('/api/library/image-upload',{method:'POST',headers:{'Content-Type':file.type||'application/octet-stream','X-Image-Name':encodeURIComponent(file.name),'X-Runner-Key':key},body:file});const data=await response.json();if(!response.ok)throw Error(data.error||'Không tải được ảnh lên thư viện.');return {...data,name:file.name};}
async function refreshLibrary(){const response=await fetch('/api/library');if(!response.ok)throw Error('Không tải được thư viện ảnh.');libraryImages=(await response.json()).images;return libraryImages;}
function renderLibrary(){const grid=$('imageLibraryGrid');grid.innerHTML=libraryImages.length?libraryImages.map(image=>`<article class="library-image"><img src="/api/library/image/${image.token}" alt="${esc(image.name)}"><div title="${esc(image.name)}">${esc(image.name)}</div><button type="button" data-use-library="${image.token}">Gắn vào prompt</button><button type="button" data-delete-library="${image.token}">Xóa khỏi thư viện</button></article>`).join(''):'<div class="notice">Thư viện chưa có ảnh. Tạo ảnh trong Flow, tải ảnh xuống rồi bấm “Nhập ảnh Flow đã tải”.</div>';}
function safeMuse(url){try{const u=new URL(url);return u.protocol==='https:'&&u.hostname==='muse.ai';}catch{return false;}}
function renderAccounts(){if(!state)return;const active=document.activeElement;if(active&&$('accountList').contains(active))return;$('accountList').innerHTML=state.accounts.length?state.accounts.map(a=>`<div class="account-row" data-account="${a.id}"><div class="account-top"><strong>${esc(a.email)}</strong><span class="badge ${a.state==='busy'?'running':a.state==='ready'?'done':a.state==='attention'?'error':''}">${esc(accountNames[a.state])}</span></div><div class="detail">${esc(a.detail||'Phiên Chrome riêng cho tài khoản này')}</div><div class="account-controls"><label><input data-enabled type="checkbox" ${a.enabled?'checked':''} ${['busy','authenticating','reviewing'].includes(a.state)?'disabled':''}> Sử dụng</label><label>Credit dự kiến <input data-budget type="number" min="0" step="0.01" value="${Number(a.budget).toFixed(2)}" ${['busy','authenticating','reviewing'].includes(a.state)?'disabled':''}></label><button data-save="${a.id}" ${['busy','authenticating','reviewing'].includes(a.state)?'disabled':''}>Lưu</button><button data-login="${a.id}" ${['busy','authenticating','reviewing'].includes(a.state)?'disabled':''}>Đăng nhập</button>${a.state==='reviewing'?`<button data-close-review="${a.id}">Đóng phiên</button>`:`<button data-review="${a.id}" ${['busy','authenticating'].includes(a.state)?'disabled':''}>Mở phiên Muse</button>`}<button class="danger-outline" data-delete-account="${a.id}" data-email="${esc(a.email)}" ${['busy','authenticating','reviewing'].includes(a.state)?'disabled':''} title="Xóa tài khoản khỏi tool">✕ Xóa</button></div></div>`).join(''):'<div class="notice">Thêm email đầu tiên để tạo phiên Chrome riêng.</div>';}
async function refresh(){try{const response=await fetch('/api/state');if(!response.ok)throw Error();state=await response.json();key=state.key;$('connection').textContent='● Bộ chạy đã kết nối';syncSettings();renderJobs();renderAccounts();}catch{$('connection').textContent='● Mất kết nối bộ chạy';}}
async function action(fn){try{await fn();await refresh();}catch(e){toast(e.message,true);}}
async function addJobs(run){
  if(busy)return;
  const prompts=splitPrompts(),cost=Number($('cost').value),generationMode=mode==='image_video'?'image':'text';
  if(mode==='image_generate')return toast('Chuyển sang Prompt → video hoặc Ảnh → video để tạo video.',true);
  if(!prompts.length)return toast('Hãy nhập ít nhất một prompt.',true);
  if(prompts.length>200)return toast('Mỗi lần thêm tối đa 200 prompt.',true);
  if(!(cost>0))return toast('Nhập credit dự kiến lớn hơn 0.',true);
  if(run&&(!state?.engine.output_folder||!state.accounts.length))return toast('Thêm tài khoản và chọn thư mục lưu trước khi chạy.',true);
  const imagesByPrompt=prompts.map((prompt,index)=>generationMode==='image'?(promptImages.get(index)?.assets||[]):[]);
  for(let index=0;index<prompts.length;index++){
    const entry=promptImages.get(index);
    if(entry&&entry.prompt!==prompts[index])return toast(`Prompt số ${index+1} đã đổi. Hãy gắn lại ảnh nhân vật.`,true);
  }
  if(imagesByPrompt.some(images=>images.length>10))return toast('Mỗi prompt tối đa 10 ảnh.',true);
  if(generationMode==='image'&&imagesByPrompt.some(images=>!images.length))return toast('Chế độ Ảnh → video yêu cầu chọn ít nhất một ảnh cho từng prompt.',true);
  busy=true;$('create').disabled=$('add').disabled=true;
  try{
    const signature=JSON.stringify([prompts,cost,generationMode,imagesByPrompt.map(images=>images.map(image=>image.token))]);
    if(pendingBatch?.signature!==signature)pendingBatch={signature,id:crypto.randomUUID()};
    try{localStorage.setItem('huy-runner-pending',JSON.stringify(pendingBatch));}catch{}
    const images=imagesByPrompt.map(assets=>assets.map(asset=>asset.token));
    await api('/api/jobs',{prompts,cost,mode:generationMode,batch:pendingBatch.id,images,sources:prompts.map(prompt=>promptSources[prompt]||''),scene_ids:prompts.map(prompt=>promptScenes[prompt]||null)});
    pendingBatch=null;
    try{localStorage.removeItem('huy-runner-pending');}catch{}
    $('prompts').value='';promptImages.clear();countPrompts();
    if(run)await api('/api/start',{parallel:Number($('parallel').value),headless:$('headless').checked,automatic:$('operation').value==='auto'});
    toast(`Đã thêm ${prompts.length} tác vụ${run?' và bật bộ chạy':''}.`);
  }catch(e){toast(e.message,true);}
  finally{busy=false;$('create').disabled=$('add').disabled=false;await refresh();}
}
$('create').onclick=()=>addJobs(true);$('add').onclick=()=>addJobs(false);
$('createFlowImage').onclick=async()=>{
  const prompt=$('prompts').value.trim();
  if(!prompt)return toast('Hãy nhập mô tả ảnh trước.',true);
  try{await api('/api/flow/create-image',{prompt});toast('Đã mở Flow và điền prompt. Chọn Hình ảnh, tạo ảnh rồi tải ảnh xuống.');await refresh();}catch(error){toast(error.message,true);}
};
$('importFlowImages').onclick=async()=>{
  try{const result=await api('/api/library/import-flow',{});await refreshLibrary();renderLibrary();$('flowStatus').textContent=state?.engine.flow_status||'Đã quét thư mục tải Flow.';toast(result.imported?`Đã thêm ${result.imported} ảnh Flow vào thư viện.`:'Chưa thấy ảnh mới trong thư mục tải Flow.');}
  catch(error){toast(error.message,true);}
};
$('run').onclick=()=>action(async()=>{await api('/api/start',{parallel:Number($('parallel').value),headless:$('headless').checked,automatic:$('operation').value==='auto'});toast('Đã bật bộ chạy.');});
$('pause').onclick=()=>action(async()=>{await api('/api/pause',{});toast('Đã dừng nhận thêm. Tác vụ đang chạy vẫn tiếp tục tải video.');});
$('pickFolder').onclick=()=>action(async()=>{$('pickFolder').disabled=true;toast('Chọn thư mục trong cửa sổ Windows vừa mở.');try{await api('/api/folder',{});}finally{$('pickFolder').disabled=false;}});
$('openFolder').onclick=()=>action(()=>api('/api/open-folder',{}));
$('openFolderSecondary').onclick=()=>$('openFolder').click();
$('openLibrary').onclick=async()=>{try{await refreshLibrary();renderLibrary();$('imageLibrary').showModal();}catch(error){toast(error.message,true);}};
$('chooseImagesDrop').onclick=()=>$('characterImages').click();
$('accountsButton').onclick=()=>{$('accounts').showModal();renderAccounts();};$('closeAccounts').onclick=()=>$('accounts').close();
$('accountForm').onsubmit=e=>{e.preventDefault();action(async()=>{const emails=$('email').value.split(/[\s,;]+/).filter(Boolean),budget=Number($('budget').value);if(emails.length===1){await api('/api/accounts',{email:emails[0],mailbox:$('mailbox').value.trim(),budget});toast('Đã thêm tài khoản. Bấm Đăng nhập để kiểm tra phiên.');}else{const r=await api('/api/accounts',{emails:emails.join('\n'),budget});toast(`Đã thêm ${r.added} tài khoản`+(r.duplicate?` · bỏ qua ${r.duplicate} email trùng`:'')+(r.invalid_count?` · ${r.invalid_count} email sai: ${r.invalid.join(', ')}`:''));}$('email').value='';$('mailbox').value='';});};
$('loginAll').onclick=()=>action(async()=>{const r=await api('/api/accounts/login-all',{parallel:Number($('loginParallel').value)});$('loginAll').blur();toast(`Đang đăng nhập ${r.count} tài khoản. Tool tự lấy OTP từ hộp thư Tuan89.`);});
$('accountList').onclick=e=>{const save=e.target.closest('[data-save]'),login=e.target.closest('[data-login]');if(save){const row=save.closest('[data-account]');action(async()=>{await api('/api/accounts/update',{id:save.dataset.save,enabled:row.querySelector('[data-enabled]').checked,budget:Number(row.querySelector('[data-budget]').value)});save.blur();toast('Đã lưu ngân sách dự kiến.');});}if(login)action(async()=>{await api('/api/login',{id:login.dataset.login});login.blur();toast('Đang mở phiên Chrome. Nếu hộp thư lỗi, nhập OTP trực tiếp trên Muse.');});const remove=e.target.closest('[data-delete-account]');if(remove){if(!confirm(`Xóa tài khoản ${remove.dataset.email}?\n\nTool đóng phiên Chrome và xóa phiên đăng nhập của tài khoản này trên máy. Prompt đang chờ sẽ được chia cho tài khoản khác. Video đã tải vẫn giữ nguyên.`))return;remove.blur();action(async()=>{const {email}=await api('/api/accounts/delete',{id:remove.dataset.deleteAccount});toast(`Đã xóa tài khoản ${email}.`);});}};
$('rows').onclick=e=>{const button=e.target.closest('[data-retry]');if(button)action(()=>api('/api/retry',{id:button.dataset.retry}));};
document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(t=>t.classList.toggle('active',t===b));renderJobs();});
$('search').oninput=renderJobs;$('prompts').oninput=countPrompts;$('separator').onchange=countPrompts;
document.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>{mode=button.dataset.mode;renderMode();});
document.querySelectorAll('[data-coming]').forEach(button=>button.onclick=()=>toast('Chức năng này sẽ được phát triển ở bước tiếp theo.'));
$('imagePromptIndex').onchange=renderImageAssignments;
$('chooseImages').onclick=()=>$('characterImages').click();
$('chooseLibrary').onclick=async()=>{try{await refreshLibrary();renderLibrary();$('imageLibrary').showModal();}catch(error){toast(error.message,true);}};
$('closeImageLibrary').onclick=()=>$('imageLibrary').close();
$('imageLibraryGrid').onclick=e=>{
  const use=e.target.closest('[data-use-library]');
  if(use){
    const prompts=splitPrompts(),index=Number($('imagePromptIndex').value),image=libraryImages.find(item=>item.token===use.dataset.useLibrary);
    if(!image||!prompts[index])return;
    let entry=promptImages.get(index);
    if(entry&&entry.prompt!==prompts[index])entry=null;
    const assets=entry?.assets||[];
    if(assets.some(item=>item.token===image.token))return toast('Ảnh này đã được gắn cho prompt.');
    if(assets.length>=10)return toast('Mỗi prompt tối đa 10 ảnh.',true);
    promptImages.set(index,{prompt:prompts[index],assets:[...assets,{token:image.token,name:image.name,type:image.type}]});
    renderImageAssignments();$('imageLibrary').close();
    return;
  }
  const remove=e.target.closest('[data-delete-library]');
  if(remove&&confirm('Xóa ảnh này khỏi thư viện? Ảnh đang được prompt đã lưu sử dụng sẽ được giữ lại.')){
    action(async()=>{await api('/api/library/delete',{tokens:[remove.dataset.deleteLibrary]});await refreshLibrary();renderLibrary();});
  }
};
$('characterImages').onchange=async e=>{
  const index=Number($('imagePromptIndex').value),prompts=splitPrompts(),files=[...e.target.files];
  e.target.value='';
  if(!prompts.length)return toast('Nhập prompt trước khi chọn ảnh.',true);
  if(files.some(file=>!['image/png','image/jpeg','image/gif','image/webp'].includes(file.type)))return toast('Chỉ chọn ảnh PNG, JPG, GIF hoặc WebP.',true);
  if(files.some(file=>file.size>10_000_000))return toast('Mỗi ảnh tối đa 10 MB.',true);
  let entry=promptImages.get(index);
  if(entry&&entry.prompt!==prompts[index])entry=null;
  if((entry?.assets.length||0)+files.length>10)return toast('Mỗi prompt tối đa 10 ảnh.',true);
  try{
    const added=[];
    for(const file of files)added.push(await uploadImage(file));
    promptImages.set(index,{prompt:prompts[index],assets:[...(entry?.assets||[]),...added]});
    await refreshLibrary();renderImageAssignments();toast(`Đã lưu ${added.length} ảnh vào thư viện cục bộ.`);
  }catch(error){toast(error.message,true);}
};
$('assignedImages').onclick=e=>{
  const button=e.target.closest('[data-remove-image]');
  if(!button)return;
  const [index,fileIndex]=button.dataset.removeImage.split(':').map(Number),entry=promptImages.get(index);
  if(!entry)return;
  entry.assets.splice(fileIndex,1);
  if(!entry.assets.length)promptImages.delete(index);
  renderImageAssignments();
};
$('rows').addEventListener('change',e=>{
  const checkbox=e.target.closest('[data-select-job]');
  if(!checkbox)return;
  if(checkbox.checked)selectedJobs.add(checkbox.dataset.selectJob);else selectedJobs.delete(checkbox.dataset.selectJob);
  updateSelectionControls();
});
$('selectVisible').onchange=()=>{
  for(const job of visibleJobs){
    if(job.status==='running')continue;
    if($('selectVisible').checked)selectedJobs.add(job.id);else selectedJobs.delete(job.id);
  }
  renderJobs();
};
$('deleteSelected').onclick=async()=>{
  const ids=[...selectedJobs].filter(id=>state.jobs.some(job=>job.id===id&&job.status!=='running'));
  if(!ids.length)return toast('Chọn ít nhất một prompt không chạy để xóa.',true);
  if(!confirm(`Xóa ${ids.length} prompt đã chọn khỏi hàng đợi? File video đã tải sẽ được giữ nguyên trong thư mục lưu.`))return;
  try{await api('/api/jobs/delete',{ids});ids.forEach(id=>selectedJobs.delete(id));toast(`Đã xóa ${ids.length} prompt khỏi hàng đợi.`);await refresh();}
  catch(error){toast(error.message,true);}
};
$('importButton').onclick=()=>$('importFile').click();$('importFile').onchange=async e=>{const file=e.target.files[0];if(!file)return;try{if(file.size>5000000)throw Error('File tối đa 5 MB.');const text=await file.text();if(file.name.toLowerCase().endsWith('.json')){const data=JSON.parse(text);if(data.format!=='huy-studio-project'||!Array.isArray(data.jobs))throw Error('Hãy chọn file JSON dự án được xuất từ phiên bản Huy Studio cũ.');const jobs=data.jobs.filter(j=>j.mode==='text'&&!j.video);if(!jobs.length)throw Error('File không có prompt → video chưa có kết quả.');$('prompts').value=jobs.map(j=>[j.prompt,j.common,`Thời lượng mong muốn: ${j.duration} giây. Tỷ lệ: ${j.ratio}.`].filter(Boolean).join('\n')).join('\n---\n');$('separator').value='custom';}else $('prompts').value=text;countPrompts();toast('Đã nhập prompt. Kiểm tra trước khi tạo.');}catch(error){toast(error.message,true);}e.target.value='';};
// ---- Viết content: Prompt + link video gốc → Gemini Pro (tự động trên gemini.google.com) → cảnh → hàng loạt prompt ----
const sceneFields=[['setting','Bối cảnh'],['camera','Góc quay'],['behavior','Hành vi nhân vật'],['action','Nội dung hành động nhân vật']];
const defaultBrief='Phân tích video gốc và chia thành từng cảnh theo đúng thứ tự thời gian. Mỗi cảnh khoảng 15 giây, mô tả đủ chi tiết để tạo lại bằng AI video. Giữ nhân vật, trang phục và bối cảnh nhất quán giữa các cảnh.';
let content={brief:defaultBrief,video:'',language:'vi',script:'',scenes:[],issues:[],englishInput:'',englishStyle:'',englishAspect:'16:9',englishStructured:true,englishSeconds:15,english:'',englishMissing:[],englishEngine:'chatgpt',englishChatgpt:'',englishChatgptMissing:[]};
try{const saved=JSON.parse(localStorage.getItem('hana-content')||'{}');content={...content,brief:saved.brief??content.brief,video:saved.video||'',language:saved.language||'vi',script:saved.script||'',scenes:Array.isArray(saved.scenes)?saved.scenes:[],issues:Array.isArray(saved.issues)?saved.issues:[],englishInput:saved.englishInput||'',englishStyle:saved.englishStyle||'',englishAspect:['16:9','9:16','1:1'].includes(saved.englishAspect)?saved.englishAspect:'16:9',englishStructured:saved.englishStructured!==false,englishSeconds:[5,8,10,15].includes(Number(saved.englishSeconds))?Number(saved.englishSeconds):15,english:saved.english||'',englishMissing:Array.isArray(saved.englishMissing)?saved.englishMissing:[],englishEngine:'chatgpt',englishChatgpt:saved.englishChatgpt||'',englishChatgptMissing:Array.isArray(saved.englishChatgptMissing)?saved.englishChatgptMissing:[],englishSources:Array.isArray(saved.englishSources)?saved.englishSources:null,englishChatgptSources:Array.isArray(saved.englishChatgptSources)?saved.englishChatgptSources:null};}catch{}
// Bản nháp còn đúng câu mẫu cũ (8 giây) thì đổi sang câu mẫu mới (15 giây).
if(content.brief==='Phân tích video gốc và chia thành từng cảnh theo đúng thứ tự thời gian. Mỗi cảnh khoảng 8 giây, mô tả đủ chi tiết để tạo lại bằng AI video. Giữ nhân vật, trang phục và bối cảnh nhất quán giữa các cảnh.')content.brief=defaultBrief;
const saveContent=()=>{try{localStorage.setItem('hana-content',JSON.stringify(content));}catch{}};
const oneLine=value=>String(value||'').replace(/\s+/g,' ').trim().replace(/[\s.,;]+$/,'');
const contentPrompts=()=>content.scenes.map((s,i)=>`Bối cảnh ${i+1}: ${oneLine(s.setting)}, góc quay: ${oneLine(s.camera)}, hành vi nhân vật: ${oneLine(s.behavior)}, Nội dung hành động nhân vật: ${oneLine(s.action)};`);
function localSceneIssues(){return content.scenes.flatMap((s,i)=>{const missing=sceneFields.filter(([f])=>!oneLine(s[f])).map(([,n])=>n.toLowerCase());return missing.length?[`Cảnh ${i+1} thiếu: ${missing.join(', ')}.`]:[];});}
function renderContentOutput(){
  const has=content.scenes.length>0;
  $('contentResult').hidden=!has&&!content.script;$('contentEmpty').hidden=has||Boolean(content.script);$('contentOutput').hidden=!has;
  $('sceneCount').textContent=`${content.scenes.length} cảnh`;
  $('contentPrompts').value=contentPrompts().join('\n');
  const issues=[...content.issues,...localSceneIssues()];
  $('contentIssues').hidden=!issues.length;
  $('contentIssues').innerHTML=issues.length?`<b>Cần xem lại</b><ul>${issues.map(i=>`<li>${esc(i)}</li>`).join('')}</ul>`:'';
  saveContent();
}
function renderScenes(){
  $('contentScript').value=content.script||'';
  $('sceneList').innerHTML=content.scenes.map((scene,index)=>`<article class="scene-card"><div class="scene-top"><b>Cảnh ${index+1}</b><span class="bulk-spacer"></span><button type="button" data-scene-move="${index}" data-dir="-1" ${index?'':'disabled'} aria-label="Đưa cảnh ${index+1} lên">↑</button><button type="button" data-scene-move="${index}" data-dir="1" ${index<content.scenes.length-1?'':'disabled'} aria-label="Đưa cảnh ${index+1} xuống">↓</button><button type="button" class="danger-outline" data-scene-delete="${index}" aria-label="Xóa cảnh ${index+1}">✕</button></div><div class="scene-fields">${sceneFields.map(([field,name])=>`<label>${name}<textarea rows="2" data-scene="${index}" data-field="${field}">${esc(scene[field])}</textarea></label>`).join('')}</div></article>`).join('');
  renderContentOutput();
}
const CONTENT_VERSION='content-19';
function renderRunnerVersion(){if(state)$('runnerOutdated').hidden=state.content?.version===CONTENT_VERSION;}
function showView(view){
  const isContent=view==='content',isSettings=view==='settings';
  $('jobView').hidden=true;$('studioView').hidden=isContent||isSettings;$('contentView').hidden=!isContent;$('settingsView').hidden=!isSettings;
  if(isSettings){applyAutoSettings(true);renderSettings(true);}
  document.querySelectorAll('[data-nav]').forEach(b=>b.classList.toggle('active',b.dataset.nav===view));
  if(isContent){$('contentBrief').value=content.brief;$('contentVideo').value=content.video;$('contentLanguage').value=content.language;renderRunnerVersion();applyAutoSettings(true);renderAuto();renderScenes();$('englishInput').value=content.englishInput;$('englishStyle').value=content.englishStyle;$('englishAspect').value=content.englishAspect;$('englishStructured').checked=content.englishStructured;$('englishSeconds').value=String(content.englishSeconds);renderEnglishEngine();openCharacters();}
  const hash=isContent?'#content':isSettings?'#settings':'';if(location.hash!==hash)history.replaceState(null,'',location.pathname+hash);
  window.scrollTo(0,0);markSidebar();
}
document.querySelectorAll('[data-nav]').forEach(b=>b.onclick=()=>showView(b.dataset.nav));
$('closeContent').onclick=()=>showView('studio');
window.addEventListener('hashchange',()=>{if(/^#job=/.test(location.hash))return;showView(location.hash==='#content'?'content':location.hash==='#settings'?'settings':'studio');});
$('contentBrief').oninput=()=>{content.brief=$('contentBrief').value;saveContent();};
$('contentVideo').oninput=()=>{content.video=$('contentVideo').value.trim();saveContent();};
$('contentLanguage').onchange=()=>{content.language=$('contentLanguage').value;saveContent();};
$('contentScript').oninput=()=>{content.script=$('contentScript').value;saveContent();};
// Gemini Pro tự động: tool điều khiển Chrome đã đăng nhập tài khoản Google.
let autoJob=null;
function applyAutoSettings(force){const web=state?.content?.web;if(!web)return;if(force||document.activeElement!==$('autoEmail'))$('autoEmail').value=web.email||'';if(force||document.activeElement!==$('autoModel'))$('autoModel').value=web.model||'';}
async function saveAutoSettings(){await api('/api/content/web-settings',{email:$('autoEmail').value.trim(),model:$('autoModel').value.trim()});}
$('autoEmail').onchange=()=>saveAutoSettings().then(refresh).then(()=>renderSettings(false)).catch(error=>toast(error.message,true));
$('autoModel').onchange=()=>saveAutoSettings().then(refresh).then(()=>renderSettings(false)).catch(error=>toast(error.message,true));
async function runAuto(kind){
  if(autoJob)return;
  if(kind==='review'&&!content.scenes.length)return;
  if(kind!=='review'&&!content.brief.trim())return toast('Nhập yêu cầu (prompt) cho Gemini.',true);
  if(kind!=='review'&&content.scenes.length&&!confirm('Chạy lại sẽ thay thế danh sách cảnh hiện tại. Tiếp tục?'))return;
  try{
    await saveAutoSettings();
    const {id}=await api('/api/content/web-run',{kind,brief:content.brief,video_url:content.video,language:content.language,scenes:kind==='review'?content.scenes:undefined});
    autoJob={id,kind};renderAuto();refresh();
  }catch(error){toast(error.message,true);}
}
function renderAuto(){
  const web=state?.content?.web,running=Boolean(autoJob),english=running&&!['generate','review'].includes(autoJob.kind);
  const text=running?(web&&web.id===autoJob.id?(web.status||''):'Đang mở Gemini…'):'';
  $('autoProgress').hidden=!running||english;
  for(const id of ['runAuto','loginAuto','reviewContent'])$(id).disabled=running;
  $('runAuto').textContent=running&&autoJob.kind==='generate'?'Gemini đang chạy…':'✦ Chạy tự động trên Gemini';
  $('reviewContent').textContent=running&&autoJob.kind==='review'?'Gemini đang kiểm tra…':'↻ Kiểm tra thiếu cảnh / lỗi';
  if(running&&!english)$('autoStatus').textContent=text;
  renderEnglishRun();
}
function pollAuto(){
  const web=state?.content?.web;applyAutoSettings();renderRunnerVersion();
  if(!autoJob||!web){renderAuto();return;}
  if(web.id!==autoJob.id||web.busy){renderAuto();return;}
  const job=autoJob;autoJob=null;renderAuto();
  if(job.kind==='characters')return finishCharacters('gemini',job.id,web);
  if(web.error)return toast(web.error,true);
  const result=web.result;if(!result)return;
  if(job.kind==='english')return finishEnglish('gemini',result);
  if(job.kind==='review'){content.scenes=result.scenes;content.issues=result.issues;}
  else{content.scenes=result.scenes;content.script=result.original_script;content.issues=result.issues||[];}
  saveContent();renderScenes();if(job.kind!=='review')setStep(2);
  toast(job.kind==='review'?`Đã kiểm tra: ${result.scenes.length} cảnh · ${result.issues.length} vấn đề.`:`Gemini đã viết ${result.scenes.length} cảnh (${result.rounds} lượt trả lời). Bấm “Kiểm tra thiếu cảnh / lỗi” để rà lại.`);
}
$('runAuto').onclick=()=>runAuto('generate');
$('reviewContent').onclick=()=>runAuto('review');
$('loginAuto').onclick=async()=>{try{await saveAutoSettings();await api('/api/content/web-login',{});await refresh();toast('Đã mở Chrome. Đăng nhập Google, thấy trang Gemini thì đóng hẳn cửa sổ đó rồi bấm Chạy.');}catch(error){toast(error.message,true);}};
$('takeAuto').onclick=()=>api('/api/content/web-take',{engine:'gemini'}).then(()=>$('autoStatus').textContent='Đang đọc câu trả lời trên trang…').catch(error=>toast(error.message,true));
$('stopAuto').onclick=()=>api('/api/content/web-stop',{}).then(()=>$('autoStatus').textContent='Đang dừng…').catch(error=>toast(error.message,true));
setInterval(pollAuto,1000);
$('addScene').onclick=()=>{content.scenes.push({setting:'',camera:'',behavior:'',action:''});renderScenes();$('sceneList').lastElementChild?.querySelector('textarea')?.focus();};
$('sceneList').addEventListener('input',e=>{const t=e.target.closest('[data-scene]');if(!t)return;content.scenes[Number(t.dataset.scene)][t.dataset.field]=t.value;renderContentOutput();});
$('sceneList').addEventListener('click',e=>{
  const del=e.target.closest('[data-scene-delete]'),move=e.target.closest('[data-scene-move]');
  if(del){content.scenes.splice(Number(del.dataset.sceneDelete),1);renderScenes();}
  if(move){const i=Number(move.dataset.sceneMove),j=i+Number(move.dataset.dir);[content.scenes[i],content.scenes[j]]=[content.scenes[j],content.scenes[i]];renderScenes();}
});
$('useContent').onclick=()=>{
  const prompts=contentPrompts();if(!prompts.length)return;
  if($('prompts').value.trim()&&!confirm('Thay nội dung đang có trong khung prompt bằng các prompt này?'))return;
  mode='text';$('separator').value='line';$('prompts').value=prompts.join('\n');renderMode();showView('studio');$('prompts').focus();
  toast(`Đã đưa ${prompts.length} prompt vào Studio. Bấm “Tạo & chạy” hoặc “Chỉ thêm vào hàng đợi”.`);
};
$('copyContent').onclick=()=>navigator.clipboard.writeText($('contentPrompts').value).then(()=>toast('Đã chép hàng loạt prompt.')).catch(()=>toast('Không chép được. Chọn và sao chép trong ô bên dưới.',true));
$('downloadContent').onclick=()=>{const blob=new Blob([[content.script?'KỊCH BẢN VIDEO GỐC\n'+content.script+'\n\nPROMPT\n':'',$('contentPrompts').value].join('')],{type:'text/plain;charset=utf-8'});const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download='content-prompt.txt';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);};



// ---- Prompt tiếng Anh (Gemini Pro hoặc ChatGPT; kết quả mỗi bên lưu riêng để so sánh) ----
const engineNames={gemini:'Gemini',chatgpt:'ChatGPT'};
let chatJob=null;
const englishKey=engine=>engine==='chatgpt'?['englishChatgpt','englishChatgptMissing']:['english','englishMissing'];
const englishLines=()=>$('englishOutput').value.split('\n');
function englishJob(engine){return engine==='chatgpt'?(chatJob&&chatJob.kind!=='characters'?chatJob:null):(autoJob&&autoJob.kind==='english'?autoJob:null);}
function renderEnglish(){
  const [textKey,missingKey]=englishKey(content.englishEngine);
  const lines=englishLines(),filled=lines.filter(line=>line.trim()).length,missing=content[missingKey]||[];
  $('englishCount').textContent=content[textKey]?`${filled} prompt · ${engineNames[content.englishEngine]}`:'';
  $('englishMissing').hidden=!missing.length;
  $('englishMissing').innerHTML=missing.length?`<b>Còn thiếu hoặc chưa phải tiếng Anh</b><ul><li>Cảnh ${missing.join(', ')} — dòng tương ứng đang để trống hoặc còn tiếng Việt. Bấm viết lại hoặc tự sửa.</li></ul>`:'';
}
function renderEnglishRun(){
  const engine=content.englishEngine,job=englishJob(engine),web=state?.content?.[engine==='chatgpt'?'chatgpt':'web'];
  const blocked=engine==='gemini'?Boolean(autoJob):Boolean(chatJob);
  $('englishProgress').hidden=!job;$('runEnglish').disabled=blocked;$('loginChatgpt').disabled=Boolean(chatJob);
  $('runEnglish').textContent=job?`${engineNames[engine]} đang viết prompt tiếng Anh…`:blocked?'Gemini đang bận ở bước trên…':`✦ Viết prompt tiếng Anh bằng ${engineNames[engine]}`;
  if(job)$('englishStatus').textContent=web&&web.id===job.id?(web.status||''):`Đang mở ${engineNames[engine]}…`;
}
function renderEnglishEngine(){
  const engine=content.englishEngine,[textKey]=englishKey(engine);
  document.querySelectorAll('[data-english-engine]').forEach(b=>b.classList.toggle('active',b.dataset.englishEngine===engine));
  $('chatgptOptions').hidden=engine!=='chatgpt';
  $('englishOutput').value=content[textKey]||'';
  if(document.activeElement!==$('chatgptModel'))$('chatgptModel').value=state?.content?.chatgpt?.model||'';
  renderEnglish();renderEnglishRun();
}
function finishEnglish(engine,result){
  const [textKey,missingKey]=englishKey(engine);
  content[textKey]=result.english.join('\n');content[missingKey]=result.missing;content[textKey+'Sources']=result.lines||splitSceneLines(content.englishInput);saveContent();
  if(content.englishEngine===engine)renderEnglishEngine();
  const name=engineNames[engine];
  toast(result.missing.length?`${name} viết ${result.total-result.missing.length}/${result.total} prompt; còn thiếu cảnh ${result.missing.join(', ')}. Bấm chạy lại hoặc tự sửa.`:`${name}: đủ ${result.total} prompt tiếng Anh (${result.rounds} lượt trả lời).`,Boolean(result.missing.length));
}
function pollChat(){
  const web=state?.content?.chatgpt;
  if(!chatJob||!web||web.id!==chatJob.id||web.busy){renderEnglishRun();return;}
  const job=chatJob;chatJob=null;renderEnglishRun();
  if(job.kind==='characters')return finishCharacters('chatgpt',job.id,web);
  if(web.error)return toast(web.error,true);
  if(web.result)finishEnglish('chatgpt',web.result);
}
setInterval(pollChat,1000);
document.querySelectorAll('[data-english-engine]').forEach(b=>b.onclick=()=>{content.englishEngine=b.dataset.englishEngine;saveContent();renderEnglishEngine();});
$('englishFromScenes').onclick=()=>{const prompts=contentPrompts();if(!prompts.length)return toast('Chưa có danh sách cảnh ở phía trên.',true);$('englishInput').value=prompts.join('\n');content.englishInput=$('englishInput').value;saveContent();};
$('englishInput').oninput=()=>{content.englishInput=$('englishInput').value;saveContent();};
$('englishStyle').oninput=()=>{content.englishStyle=$('englishStyle').value;saveContent();};
$('englishAspect').onchange=()=>{content.englishAspect=$('englishAspect').value;saveContent();};
$('englishSeconds').onchange=()=>{content.englishSeconds=Number($('englishSeconds').value);saveContent();};
$('englishStructured').onchange=()=>{content.englishStructured=$('englishStructured').checked;saveContent();};
$('englishOutput').oninput=()=>{content[englishKey(content.englishEngine)[0]]=$('englishOutput').value;saveContent();renderEnglish();};
$('chatgptModel').onchange=()=>api('/api/content/chatgpt-settings',{model:$('chatgptModel').value.trim()}).then(refresh).catch(error=>toast(error.message,true));
$('loginChatgpt').onclick=async()=>{try{await api('/api/content/web-login',{engine:'chatgpt'});await refresh();toast('Đã mở Chrome. Đăng nhập ChatGPT, thấy ô nhắn tin thì đóng hẳn cửa sổ đó rồi bấm Viết prompt.');}catch(error){toast(error.message,true);}};
$('runEnglish').onclick=async()=>{
  const engine=content.englishEngine,[textKey]=englishKey(engine);
  if(engine==='gemini'?autoJob:chatJob)return;
  if(!$('englishInput').value.trim()&&contentPrompts().length)$('englishFromScenes').click();
  if(!$('englishInput').value.trim())return toast('Dán danh sách “Bối cảnh 1: …” hoặc bấm Lấy từ danh sách cảnh.',true);
  if((content[textKey]||'').trim()&&!confirm(`Viết lại sẽ thay kết quả ${engineNames[engine]} hiện tại. Tiếp tục?`))return;
  try{
    if(engine==='gemini')await saveAutoSettings();
    else await api('/api/content/chatgpt-settings',{model:$('chatgptModel').value.trim()});
    const {id,total}=await api('/api/content/english-run',{engine,text:$('englishInput').value,style:$('englishStyle').value,aspect:$('englishAspect').value,structured:$('englishStructured').checked,seconds:Number($('englishSeconds').value)});
    if(engine==='gemini'){autoJob={id,kind:'english'};renderAuto();}else{chatJob={id};renderEnglishRun();}
    refresh();toast(`Đã gửi ${total} cảnh cho ${engineNames[engine]}.`);
  }catch(error){toast(error.message,true);}
};
$('takeEnglish').onclick=()=>api('/api/content/web-take',{engine:content.englishEngine}).then(()=>$('englishStatus').textContent='Đang đọc câu trả lời trên trang…').catch(error=>toast(error.message,true));
$('stopEnglish').onclick=()=>api('/api/content/web-stop',{engine:content.englishEngine}).then(()=>$('englishStatus').textContent='Đang dừng…').catch(error=>toast(error.message,true));
// Tách đầu vào giống máy chủ (content.split_scene_lines): mỗi dòng một cảnh, hoặc "[...; Bối cảnh 2: ...]".
function splitSceneLines(text){
  return String(text||'').replace(/\r/g,'').trim().replace(/^\s*(input\s*:)?\s*\[/i,'').replace(/\]\s*$/,'')
    .replace(/;\s*(?=b[ốo]i\s*c[ảa]nh\s*\d)/gi,';\n').split('\n').map(line=>line.replace(/\s+/g,' ').trim()).filter(Boolean);
}
// Prompt tiếng Anh số N đi kèm dòng cảnh tiếng Việt số N, để Prompt Studio có đủ Bối cảnh / Góc quay / Hành vi / Hành động.
let promptSources={},promptScenes={};
try{promptSources=JSON.parse(localStorage.getItem('hana-prompt-sources')||'{}')||{};promptScenes=JSON.parse(localStorage.getItem('hana-prompt-scenes')||'{}')||{};}catch{}
function rememberSources(pairs){promptSources={};promptScenes={};for(const [prompt,source,scene] of pairs){if(source)promptSources[prompt]=source;if(scene)promptScenes[prompt]=scene;}try{localStorage.setItem('hana-prompt-sources',JSON.stringify(promptSources));localStorage.setItem('hana-prompt-scenes',JSON.stringify(promptScenes));}catch{}}
$('useEnglish').onclick=()=>{
  const [textKey]=englishKey(content.englishEngine);
  const sources=content[textKey+'Sources']||splitSceneLines(content.englishInput);
  const lines=englishLines(),sceneIds=project&&project.scenes.length===lines.filter(l=>l.trim()).length?project.scenes.map(x=>x.id):[];
  let n=0;const pairs=lines.map((line,index)=>[line.trim(),sources[index]||'',line.trim()?sceneIds[n++]||'':'']).filter(([line])=>line);
  const prompts=pairs.map(([line])=>line);if(!prompts.length)return toast('Chưa có prompt tiếng Anh.',true);
  if($('prompts').value.trim()&&!confirm('Thay nội dung đang có trong khung prompt bằng các prompt tiếng Anh?'))return;
  rememberSources(pairs);
  mode='text';$('separator').value='line';$('prompts').value=prompts.join('\n');renderMode();showView('studio');$('prompts').focus();
  const linked=pairs.filter(([,source])=>source).length;
  toast(`Đã đưa ${prompts.length} prompt tiếng Anh (${engineNames[content.englishEngine]}) vào Studio; ${linked} prompt kèm đủ Bối cảnh, Góc quay, Hành vi, Hành động (lấy từ khung chuẩn trong prompt, hoặc từ dòng cảnh tiếng Việt).`);
};
$('copyEnglish').onclick=()=>navigator.clipboard.writeText($('englishOutput').value).then(()=>toast('Đã chép prompt tiếng Anh.')).catch(()=>toast('Không chép được. Chọn và sao chép trong ô kết quả.',true));
$('downloadEnglish').onclick=()=>{const blob=new Blob([$('englishOutput').value],{type:'text/plain;charset=utf-8'});const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download=`prompt-tieng-anh-${content.englishEngine}.txt`;link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);};

// ---- Prompt Studio: bấm vào một prompt trong hàng đợi để xem và sửa chi tiết ----
let studioJob=null,studioEditing=false,studioImages=[],studioMode='text';
const statusLabel={waiting:'Chờ',running:'Đang chạy',done:'Xong',error:'Lỗi'};
function jobViewShown(){return !$('jobView').hidden;}
function setJobView(show){$('jobView').hidden=!show;if(show){$('studioView').hidden=true;$('contentView').hidden=true;document.querySelectorAll('[data-nav]').forEach(b=>b.classList.toggle('active',b.dataset.nav==='studio'));window.scrollTo(0,0);}}
async function openJob(id){
  try{
    const response=await fetch('/api/job/'+id+(charPrefs.projectId?'?project='+charPrefs.projectId:''));const data=await response.json();if(!response.ok)throw Error(data.error||'Không mở được prompt.');
    studioJob=data;studioEditing=data.status==='waiting'||data.status==='error';studioImages=[...data.images];studioMode=data.generation_mode==='image'?'image':'text';
    fillJob();setJobView(true);const hash='#job='+id;if(location.hash!==hash)history.replaceState(null,'',location.pathname+hash);
  }catch(error){toast(error.message,true);}
}
function closeJob(){studioJob=null;$('jobView').hidden=true;showView('studio');}
function fillJob(){
  const job=studioJob;if(!job)return;
  $('jobName').textContent=`${job.name} · ${statusLabel[job.status]||job.status}`;
  $('jobPrompt').value=job.prompt;$('jobSetting').value=job.details.setting;$('jobBehavior').value=job.details.behavior;$('jobAction').value=job.details.action;$('jobCamera').value=job.details.camera;
  const accounts=state?.accounts||[];
  $('jobAccount').innerHTML=`<option value="">Chờ tài khoản (tool tự chia)</option>`+accounts.map((a,i)=>`<option value="${a.id}">Tài khoản ${i+1} · ${esc(a.email)}${a.enabled?'':' (đang tắt)'}</option>`).join('');
  $('jobAccount').value=job.assigned_account_id&&accounts.some(a=>a.id===job.assigned_account_id)?job.assigned_account_id:'';
  const canRerun=job.status==='done'||job.status==='error';
  $('jobStatus').innerHTML=`<option value="${job.status}">${statusLabel[job.status]}${job.status==='error'&&job.error?' – '+esc(job.error.slice(0,80)):''}</option>`+(canRerun?'<option value="rerun">Chờ (chạy lại prompt này)</option>':'');
  $('jobStatus').disabled=!canRerun;
  $('jobInfo').textContent=job.status==='running'?'Prompt đang chạy: chỉ xem, chưa sửa được.':job.status==='done'?'Đã có video. Sửa rồi bấm Chạy lại để tạo video mới; video cũ vẫn giữ trong thư mục lưu.':job.status==='error'?(job.submitted?'Lỗi sau khi đã gửi lên Muse: kiểm tra Muse trước khi chạy lại.':'Lỗi trước khi gửi: sửa rồi bấm Chạy lại.'):'Đang chờ chạy: sửa xong bấm Lưu.';
  $('jobNotice').hidden=true;renderJobImages();renderJobEditing();renderJobCharacters();
}
const jobCharStates={attached:'✓ Đã gắn ảnh nhân vật vào prompt',not_attached:'Chưa gắn ảnh nhân vật vào prompt',missing_master:'Thiếu ảnh Master đã duyệt',none:'Prompt chưa có nhân vật'};
function renderJobCharacters(){
  const info=studioJob?.characters;if(!info){$('jobCharacters').innerHTML='';return;}
  const running=studioJob.status==='running',selected=new Set(info.selected.map(c=>c.id));
  const where=info.source==='prompt'?'chọn riêng cho prompt này':info.source==='scene'?`theo Cảnh ${info.scene_number} của dự án`:'chưa liên kết cảnh nào';
  const options=info.options.length?info.options:info.selected;
  const chips=options.map(c=>`<label class="chip"><input type="checkbox" value="${c.id}" ${selected.has(c.id)?'checked':''} ${running?'disabled':''}> ${esc(c.code)}</label>`).join('');
  const refs=info.selected.map(c=>`<figure class="job-ref${c.master_token?'':' missing'}">${c.master_token?`<img src="/api/library/image/${c.master_token}" alt="Ảnh Master ${esc(c.name)}">`:`<span>${esc(initials(c.name))}</span>`}<figcaption><b>${esc(c.code)}</b><small>${c.master_token?'Ảnh Master đã duyệt':'Chưa có ảnh Master'}</small></figcaption></figure>`).join('');
  const manual=studioJob.images.length&&info.images_origin==='manual'&&info.state!=='attached';
  $('jobCharacters').innerHTML=`<div class="job-char-head"><label>Nhân vật <small>(${esc(where)}${info.project?' · '+esc(info.project.name):''})</small></label></div>
${options.length?`<div class="chips">${chips}</div>`:'<p class="detail">Chưa có nhân vật để chọn. Mở dự án ở Content Studio → bước Nhân vật, rồi mở lại prompt này.</p>'}
<label>Ảnh nhân vật tham chiếu</label>
<div class="job-refs">${refs||'<p class="detail">Chọn nhân vật để xem ảnh Master tương ứng.</p>'}</div>
<p class="job-char-state ${info.state}">${esc(jobCharStates[info.state])}${info.state==='attached'?` · ${info.attached} ảnh · chế độ Ảnh → video`:''}${manual?' · ảnh hiện tại do bạn tự gắn':''}</p>
<div class="cd-actions"><button type="button" id="jobCharSave" ${running||!options.length?'disabled':''}>Lưu nhân vật</button><button type="button" id="jobCharAttach" class="primary" ${running||info.state==='none'||info.state==='missing_master'?'disabled':''}>🔗 Gắn ảnh nhân vật vào prompt</button></div>
<p class="detail">Muse nhận tối đa ${info.limit} ảnh tham chiếu mỗi prompt. Chỉ gắn ảnh của nhân vật có trong prompt; không tạo ảnh mới cho từng cảnh.</p>`;
}
$('jobCharacters').addEventListener('click',async e=>{
  if(!studioJob)return;
  if(e.target.id==='jobCharSave'){
    const ids=[...$('jobCharacters').querySelectorAll('input[type=checkbox]:checked')].map(x=>x.value);
    try{const {job}=await api('/api/jobs/characters',{id:studioJob.id,character_ids:ids});studioJob.characters=job.characters;renderJobCharacters();toast(`Đã lưu ${ids.length} nhân vật cho ${studioJob.name}.`);}catch(error){toast(error.message,true);}
  }
  if(e.target.id==='jobCharAttach'){
    if(jobChanged()&&!(await saveJob()))return;
    try{
      let result=await api('/api/jobs/attach-characters',{id:studioJob.id});
      if(result.confirm){if(!confirm(result.confirm))return;result=await api('/api/jobs/attach-characters',{id:studioJob.id,confirm:true});}
      const job=result.job;studioJob=job;studioImages=[...job.images];studioMode=job.generation_mode==='image'?'image':'text';fillJob();refresh();
      toast(`Đã gắn ${job.images.length} ảnh nhân vật vào ${job.name}.`);
    }catch(error){toast(error.message,true);}
  }
});
function renderJobImages(){
  $('jobImages').innerHTML=studioImages.map((token,i)=>`<figure><img src="/api/library/image/${token}" alt="Ảnh tham chiếu ${i+1}"><button type="button" data-remove-job-image="${i}" ${studioEditing?'':'hidden'} aria-label="Bỏ ảnh ${i+1}">✕</button></figure>`).join('')||'<p class="detail">Chưa có ảnh tham chiếu.</p>';
}
function renderJobEditing(){
  const running=studioJob?.status==='running';
  document.querySelectorAll('#jobView [data-job-field]').forEach(e=>{if(e.tagName==='TEXTAREA'||e.tagName==='INPUT')e.readOnly=!studioEditing;else e.disabled=!studioEditing;});
  document.querySelectorAll('[data-job-type]').forEach(b=>b.classList.toggle('active',b.dataset.jobType===studioMode));
  $('jobDrop').classList.toggle('disabled',!studioEditing);
  $('jobEdit').hidden=studioEditing||running;$('jobSave').disabled=!studioEditing;$('jobCancel').disabled=!studioEditing;
  $('jobRerun').disabled=running||studioJob?.status==='waiting';
  renderJobImages();
}
function jobPayload(){return {id:studioJob.id,prompt:$('jobPrompt').value,details:{setting:$('jobSetting').value,behavior:$('jobBehavior').value,action:$('jobAction').value,camera:$('jobCamera').value},images:studioImages,mode:studioMode,account_id:$('jobAccount').value||null};}
function jobChanged(){if(!studioJob||!studioEditing)return false;const p=jobPayload(),j=studioJob;return p.prompt.trim()!==j.prompt||JSON.stringify(Object.values(p.details).map(v=>v.trim()))!==JSON.stringify([j.details.setting,j.details.behavior,j.details.action,j.details.camera])||JSON.stringify(p.images)!==JSON.stringify(j.images)||p.mode!==(j.generation_mode==='image'?'image':'text')||(p.account_id||'')!==(j.assigned_account_id&&state?.accounts?.some(a=>a.id===j.assigned_account_id)?j.assigned_account_id:'');}
async function saveJob(){
  if(!$('jobPrompt').value.trim()){$('jobNotice').innerHTML='<b>Prompt không được để trống.</b>';$('jobNotice').hidden=false;$('jobPrompt').focus();return false;}
  try{const {job}=await api('/api/jobs/update',jobPayload());studioJob=job;await refresh();return true;}
  catch(error){$('jobNotice').innerHTML=`<b>${esc(error.message)}</b>`;$('jobNotice').hidden=false;return false;}
}
$('rows').addEventListener('click',e=>{if(e.target.closest('button,a,input,video,.video-thumb'))return;const cell=e.target.closest('.prompt-cell');if(!cell)return;const row=cell.closest('tr');const box=row&&row.querySelector('[data-select-job]');if(box)openJob(box.dataset.selectJob);});
$('jobBack').onclick=()=>{if(jobChanged()&&!confirm('Bỏ các thay đổi chưa lưu?'))return;closeJob();};
$('jobEdit').onclick=()=>{studioEditing=true;renderJobEditing();$('jobPrompt').focus();};
$('jobCancel').onclick=()=>{if(jobChanged()&&!confirm('Bỏ các thay đổi chưa lưu?'))return;openJob(studioJob.id);};
$('jobSave').onclick=async()=>{if(await saveJob()){toast(`Đã lưu ${studioJob.name}.`);closeJob();}};
$('jobRerun').onclick=async()=>{
  if(!studioJob)return;
  if(studioEditing&&jobChanged()&&!(await saveJob()))return;
  const sent=studioJob.status==='error'&&studioJob.submitted;
  if(!confirm(sent?'Prompt này có thể đã được gửi lên Muse (có thể đã tốn credit). Kiểm tra Muse trước. Vẫn chạy lại?':`Đưa ${studioJob.name} về Chờ để chạy lại?`))return;
  try{await api('/api/jobs/rerun',{id:studioJob.id,confirmed:Boolean(sent)});await refresh();toast(`${studioJob.name} đã về hàng Chờ. Bấm Chạy để tạo lại.`);closeJob();}catch(error){toast(error.message,true);}
};
$('jobStatus').onchange=()=>{if($('jobStatus').value==='rerun'){$('jobStatus').value=studioJob.status;$('jobRerun').click();}};
document.querySelectorAll('[data-job-type]').forEach(b=>b.onclick=()=>{studioMode=b.dataset.jobType;renderJobEditing();});
$('jobChoose').onclick=()=>$('jobFiles').click();
async function addJobFiles(files){
  if(!studioEditing)return;
  for(const file of [...files]){if(studioImages.length>=10){toast('Tối đa 10 ảnh mỗi prompt.',true);break;}try{const image=await uploadImage(file);studioImages.push(image.token);}catch(error){toast(error.message,true);}}
  renderJobImages();
}
$('jobFiles').onchange=()=>{addJobFiles($('jobFiles').files);$('jobFiles').value='';};
$('jobDrop').addEventListener('dragover',e=>{e.preventDefault();$('jobDrop').classList.add('over');});
$('jobDrop').addEventListener('dragleave',()=>$('jobDrop').classList.remove('over'));
$('jobDrop').addEventListener('drop',e=>{e.preventDefault();$('jobDrop').classList.remove('over');addJobFiles(e.dataTransfer.files);});
$('jobImages').onclick=e=>{const b=e.target.closest('[data-remove-job-image]');if(!b)return;studioImages.splice(Number(b.dataset.removeJobImage),1);renderJobImages();};
window.addEventListener('hashchange',()=>{const m=location.hash.match(/^#job=([a-f0-9]{32})$/);if(m)openJob(m[1]);else if(jobViewShown())closeJob();});
try{$('prompts').value=localStorage.getItem('huy-runner-draft')||'';const pending=JSON.parse(localStorage.getItem('huy-runner-pending')||'null');if(pending&&typeof pending.id==='string'&&typeof pending.signature==='string')pendingBatch=pending;}catch{}
// ---------- Character Library (Character Master · giai đoạn 2) ----------
let project=null,charJob=null,selectedCharacter='';
const initials=name=>String(name||'?').split(/\s+/).filter(Boolean).slice(0,2).map(w=>w[0]).join('').toUpperCase()||'?';
const masterImage=c=>(c.assets||[]).find(a=>a.id===c.approved_asset_id)?.image_token||'';
function statusChip(c){return `<span class="status-chip ${c.status}">${c.status==='approved'?'✓ ':''}${esc(characterStates[c.status]||c.status)}${c.locked?' · 🔒':''}</span>`;}
function renderCharacterDetail(counts){
  const c=project?.characters.find(x=>x.id===selectedCharacter);
  if(!c){$('characterDetail').innerHTML='<div class="empty"><span>☺</span><h3>Chưa chọn nhân vật</h3><p>Danh sách nhân vật hiện ở bên trái sau khi AI phân tích kịch bản.</p></div>';return;}
  const token=masterImage(c),lock=c.locked?'disabled':'';
  const thumbs=(c.assets||[]).map(a=>`<button type="button" class="cd-thumb${a.id===c.approved_asset_id?' active':''}" data-approve-asset="${a.id}" ${c.locked?'disabled':''} title="${a.id===c.approved_asset_id?'Ảnh Master đang dùng':'Bấm để chọn làm ảnh Master'}"><img src="/api/library/image/${a.image_token}" alt=""></button>`).join('');
  $('characterDetail').innerHTML=`<article class="cd-layout" data-character="${c.id}">
<div class="cd-media"><div class="cd-image">${token?`<img src="/api/library/image/${token}" alt="Ảnh Master ${esc(c.name)}">`:`<div class="cd-placeholder"><b>${esc(initials(c.name))}</b><span>Chưa có ảnh Master</span><small>Bấm “Mở Flow & điền prompt” để tạo ảnh bằng Nano Banana, hoặc “Thay ảnh” để chọn ảnh có sẵn</small></div>`}</div>
<div class="cd-thumbs">${thumbs||'<small class="detail">Ảnh của nhân vật hiện ở đây; bấm một ảnh để chọn làm ảnh Master.</small>'}</div></div>
<div class="cd-info"><div class="cd-top"><code>${esc(c.code)}</code>${statusChip(c)}<span class="badge count">${counts[c.id]||0} cảnh</span></div>
<input class="cd-name" data-char-name maxlength="200" value="${esc(c.name)}" aria-label="Tên nhân vật" ${lock}>
<label class="cd-box">Mô tả nhân vật <small>(giữ cố định ở mọi cảnh)</small><textarea data-char-desc rows="4" maxlength="5000" ${lock}>${esc(c.description)}</textarea></label>
<label class="cd-box">Prompt tạo ảnh (Nano Banana)<textarea data-char-prompt rows="4" maxlength="5000" ${lock}>${esc(c.image_prompt)}</textarea></label>
<div class="cd-actions"><button type="button" data-char-flow="${c.id}" ${lock} title="Mở Google Flow, điền prompt tạo ảnh của nhân vật này">✦ ${token?'Tạo lại':'Mở Flow & điền prompt'}</button><button type="button" data-char-pick="${c.id}" ${lock}>⇄ Thay ảnh</button><button type="button" class="primary" data-char-lock="${c.id}" data-locked="${c.locked?1:0}" ${c.approved_asset_id?'':'disabled title="Cần có ảnh Master đã duyệt"'}>${c.locked?'🔓 Mở khóa':'🔒 Duyệt / Khóa'}</button></div>
<div id="characterPicker" class="character-picker" hidden></div>
<p class="detail">Tạo ảnh bằng Flow: tool mở Flow và điền prompt; bạn chọn <b>Hình ảnh</b>, mô hình Nano Banana, bấm tạo rồi tải ảnh xuống. Sau đó bấm <b>Thay ảnh → Lấy ảnh mới tải từ Flow</b> và chọn ảnh làm Master.</p>
<div class="cd-actions"><button type="button" data-char-save="${c.id}" ${lock}>Lưu thay đổi</button><button type="button" class="danger-outline" data-char-delete="${c.id}" ${lock}>✕ Xóa nhân vật</button></div></div></article>`;
}
let charPrefs={projectId:'',engine:'gemini'};
try{const saved=JSON.parse(localStorage.getItem('hana-characters')||'{}');charPrefs={projectId:/^[a-f0-9]{32}$/.test(saved.projectId||'')?saved.projectId:'',engine:saved.engine==='chatgpt'?'chatgpt':'gemini'};}catch{}
function saveCharPrefs(){try{localStorage.setItem('hana-characters',JSON.stringify(charPrefs));}catch{}}
const characterStates={draft:'Bản nháp',generating:'Đang tạo ảnh',review:'Chờ duyệt ảnh',approved:'Đã duyệt'};
async function loadProjects(){
  try{const response=await fetch('/api/projects');if(!response.ok)return;const {projects}=await response.json();
    $('projectSelect').innerHTML='<option value="">— Chưa lưu dự án —</option>'+projects.map(x=>`<option value="${x.id}">${esc(x.name)} · ${x.scenes} cảnh · ${x.characters} nhân vật</option>`).join('');
    $('projectSelect').value=project?.id||'';}catch{}
}
$('projectSelect').onchange=()=>loadProject($('projectSelect').value).catch(error=>toast(error.message,true));
async function loadProject(id){
  if(!id){project=null;renderCharacters();return;}
  const response=await fetch('/api/project/'+id);
  if(!response.ok){project=null;charPrefs.projectId='';saveCharPrefs();renderCharacters();return;}
  project=await response.json();charPrefs.projectId=project.id;saveCharPrefs();renderCharacters();loadProjects();
}
function openCharacters(){renderCharacterEngine();loadProjects();setStep(currentStep,false);if(charPrefs.projectId&&(!project||project.id!==charPrefs.projectId))loadProject(charPrefs.projectId).catch(()=>{});else renderCharacters();}
function renderCharacterEngine(){document.querySelectorAll('[data-character-engine]').forEach(b=>b.classList.toggle('active',b.dataset.characterEngine===charPrefs.engine));renderCharacterRun();}
function renderCharacterRun(){
  const web=charJob?state?.content?.[charJob.engine==='chatgpt'?'chatgpt':'web']:null;
  const busy=charPrefs.engine==='gemini'?Boolean(autoJob):Boolean(chatJob);
  $('characterProgress').hidden=!charJob;
  if(charJob)$('characterStatus').textContent=web&&web.id===charJob.id?(web.status||''):`Đang mở ${engineNames[charJob.engine]}…`;
  $('extractCharacters').disabled=!project||busy||Boolean(charJob);
  $('extractCharacters').textContent=charJob?`${engineNames[charJob.engine]} đang tìm nhân vật…`:busy?`${engineNames[charPrefs.engine]} đang bận việc khác…`:project?.characters.length?`↻ Tìm lại nhân vật bằng ${engineNames[charPrefs.engine]}`:`✦ Tìm nhân vật bằng ${engineNames[charPrefs.engine]}`;
  $('saveProject').disabled=Boolean(charJob);
}
function renderCharacters(){
  const scenesNow=content.scenes.length;
  if(!project){
    $('projectInfo').textContent=scenesNow?`Có ${scenesNow} cảnh ở bước 2. Bấm Lưu thành dự án để tìm nhân vật.`:'Chưa có dự án. Viết cảnh ở bước 2 rồi bấm Lưu thành dự án.';
    $('characterList').innerHTML='';$('projectScenes').innerHTML='';$('projectSceneHint').textContent='';$('characterNotice').hidden=true;
    document.querySelector('.character-add').hidden=true;renderCharacterRun();return;
  }
  document.querySelector('.character-add').hidden=false;
  const counts={};for(const scene of project.scenes)for(const id of scene.character_ids)counts[id]=(counts[id]||0)+1;
  $('projectInfo').textContent=`Dự án “${project.name}” · ${project.scenes.length} cảnh · ${project.characters.length} nhân vật`+(scenesNow&&scenesNow!==project.scenes.length?` · bước 2 đang có ${scenesNow} cảnh (khác dự án): bấm Lưu thành dự án để tạo dự án mới`:'');
  if(!project.characters.some(c=>c.id===selectedCharacter))selectedCharacter=project.characters[0]?.id||'';
  $('characterTotal').textContent=String(project.characters.length);
  $('characterList').innerHTML=project.characters.length?project.characters.map(c=>`<button type="button" class="character-row${c.id===selectedCharacter?' active':''}" data-select-character="${c.id}"><span class="cr-thumb">${masterImage(c)?`<img src="/api/library/image/${masterImage(c)}" alt="">`:esc(initials(c.name))}</span><span class="cr-text"><small>${esc(c.code)}</small><b>${esc(c.name)}</b>${statusChip(c)}</span><span class="cr-scenes">${counts[c.id]||0} cảnh</span></button>`).join(''):'<div class="notice">Chưa có nhân vật. Bấm “Tìm nhân vật bằng AI” để đọc kịch bản, hoặc thêm thủ công.</div>';
  renderCharacterDetail(counts);
  const approved=project.characters.filter(c=>c.approved_asset_id).length;
  $('characterSummary').textContent=`${project.characters.length} nhân vật · Đã duyệt: ${approved} · Cần tạo ảnh: ${project.characters.length-approved}`;
  $('projectSceneHint').textContent=project.characters.length?' · tick nhân vật xuất hiện trong cảnh rồi bấm Lưu; cảnh đã chỉnh tay sẽ không bị AI ghi đè.':'';
  $('projectScenes').innerHTML=project.scenes.map((scene,index)=>{
    const count=scene.character_ids.length;
    return `<div class="project-scene" data-project-scene="${scene.id}"><div class="scene-top"><b>Cảnh ${index+1}</b><span class="badge count">${count} nhân vật</span>${scene.manual?'<small class="detail">đã chỉnh tay</small>':''}<span class="bulk-spacer"></span>${project.characters.length?`<button type="button" data-scene-characters="${scene.id}">Lưu</button>`:''}</div>
<p class="detail">${esc([scene.setting,scene.action].filter(Boolean).join(' · ')||scene.source||scene.prompt)}</p>
${project.characters.length?`<div class="chips">${project.characters.map(c=>`<label class="chip"><input type="checkbox" value="${c.id}" ${scene.character_ids.includes(c.id)?'checked':''}> ${esc(c.code)}</label>`).join('')}</div>`:''}</div>`;}).join('');
  renderCharacterRun();
}
function finishCharacters(engine,id,web){
  charJob=null;renderCharacterRun();
  if(web.error)return toast(web.error,true);
  api('/api/characters/apply',{engine,job_id:id}).then(({summary,missing,unknown,project:data})=>{
    project=data;selectedCharacter='';renderCharacters();loadProjects();
    const notes=[];
    if(missing.length)notes.push(`AI chưa ghi nhân vật cho cảnh ${missing.join(', ')}. Tick tay ở danh sách cảnh bên dưới.`);
    if(unknown.length)notes.push(`AI nhắc tới Character ID chưa liệt kê: ${unknown.join(', ')} (đã bỏ qua).`);
    if(summary.kept_manual)notes.push(`${summary.kept_manual} cảnh bạn đã chỉnh tay được giữ nguyên.`);
    $('characterNotice').hidden=!notes.length;$('characterNotice').innerHTML=notes.length?`<b>Cần xem lại</b><ul>${notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul>`:'';
    toast(`${engineNames[engine]}: ${project.characters.length} nhân vật (thêm ${summary.added}), gán cho ${summary.linked} cảnh.`,Boolean(missing.length));
  }).catch(error=>toast(error.message,true));
}
document.querySelectorAll('[data-character-engine]').forEach(b=>b.onclick=()=>{charPrefs.engine=b.dataset.characterEngine;saveCharPrefs();renderCharacterEngine();});
$('saveProject').onclick=()=>action(async()=>{
  if(!content.scenes.length)return toast('Chưa có danh sách cảnh ở bước 2.',true);
  if(project&&!confirm(`Tạo dự án mới từ ${content.scenes.length} cảnh hiện tại? Dự án “${project.name}” vẫn được giữ.`))return;
  const name=(content.brief||'').split('\n').map(x=>x.trim()).find(Boolean)||'';
  const {project:data}=await api('/api/projects/create',{name:name.slice(0,80),brief:content.brief,video_url:content.video,script:content.script,scenes:content.scenes});
  project=data;charPrefs.projectId=data.id;saveCharPrefs();$('characterNotice').hidden=true;renderCharacters();loadProjects();$('saveProject').blur();
  toast(`Đã lưu dự án với ${data.scenes.length} cảnh. Bấm Tìm nhân vật.`);
});
$('extractCharacters').onclick=()=>action(async()=>{
  if(!project)return;
  const engine=charPrefs.engine;
  if(project.characters.length&&!confirm('AI sẽ tìm lại nhân vật. Nhân vật đã duyệt/khóa và cảnh đã chỉnh tay được giữ nguyên. Tiếp tục?'))return;
  if(engine==='gemini')await saveAutoSettings();else await api('/api/content/chatgpt-settings',{model:$('chatgptModel').value.trim()});
  const {id,total}=await api('/api/characters/extract',{project_id:project.id,engine});
  charJob={id,engine};
  if(engine==='gemini'){autoJob={id,kind:'characters'};renderAuto();}else{chatJob={id,kind:'characters'};renderEnglishRun();}
  renderCharacterRun();refresh();toast(`Đã gửi ${total} cảnh cho ${engineNames[engine]} để tìm nhân vật.`);
});
$('takeCharacters').onclick=()=>charJob&&api('/api/content/web-take',{engine:charJob.engine}).then(()=>$('characterStatus').textContent='Đang đọc câu trả lời trên trang…').catch(error=>toast(error.message,true));
$('stopCharacters').onclick=()=>charJob&&api('/api/content/web-stop',{engine:charJob.engine}).then(()=>$('characterStatus').textContent='Đang dừng…').catch(error=>toast(error.message,true));
$('addCharacter').onclick=()=>action(async()=>{
  if(!project)return;
  const name=$('newCharacterName').value.trim();if(!name)return toast('Nhập tên nhân vật.',true);
  const sent={newCharacterName:$('newCharacterName').value,newCharacterDesc:$('newCharacterDesc').value,newCharacterPrompt:$('newCharacterPrompt').value};
  $('addCharacter').disabled=true;
  try{
    const {code,id}=await api('/api/characters/add',{project_id:project.id,name,description:sent.newCharacterDesc,image_prompt:sent.newCharacterPrompt});
    for(const [field,value] of Object.entries(sent))if($(field).value===value)$(field).value='';  // không xóa nội dung vừa gõ thêm
    selectedCharacter=id;await loadProject(project.id);toast(`Đã thêm nhân vật ${code}.`);
  }finally{$('addCharacter').disabled=false;}
});
$('characterList').onclick=e=>{const row=e.target.closest('[data-select-character]');if(!row)return;selectedCharacter=row.dataset.selectCharacter;renderCharacters();};
async function renderCharacterPicker(id){
  const box=$('characterPicker');if(!box)return;
  try{await refreshLibrary();}catch{}
  box.hidden=false;
  box.innerHTML=`<div class="picker-head"><b>Chọn ảnh Master</b><span class="bulk-spacer"></span><button type="button" data-picker-flow>⇩ Lấy ảnh mới tải từ Flow</button><label class="picker-upload">⇪ Tải ảnh lên<input type="file" accept="image/png,image/jpeg,image/webp,image/gif" data-picker-upload hidden></label><button type="button" data-picker-close>✕</button></div>
<div class="picker-grid">${libraryImages.length?libraryImages.map(img=>`<button type="button" data-picker-token="${img.token}" title="${esc(img.name)}"><img src="/api/library/image/${img.token}" alt="${esc(img.name)}"></button>`).join(''):'<p class="detail">Thư viện ảnh đang trống. Tải ảnh lên hoặc lấy ảnh vừa tải từ Flow.</p>'}</div>`;
  box.dataset.character=id;
}
async function useCharacterImage(id,token,origin){
  await api('/api/characters/asset',{character_id:id,token,origin,approve:true});
  await loadProject(project.id);toast('Đã đặt ảnh Master cho nhân vật. Kiểm tra rồi bấm Duyệt / Khóa.');
}
$('characterDetail').addEventListener('change',e=>{
  const input=e.target.closest('[data-picker-upload]');if(!input||!input.files[0])return;
  const id=$('characterPicker').dataset.character;
  action(async()=>{const {token}=await uploadImage(input.files[0]);await useCharacterImage(id,token,'upload');});
});
$('characterDetail').onclick=e=>{
  const flow=e.target.closest('[data-char-flow]'),pick=e.target.closest('[data-char-pick]'),approveAsset=e.target.closest('[data-approve-asset]');
  if(flow){const card=flow.closest('[data-character]'),prompt=card.querySelector('[data-char-prompt]').value.trim();if(!prompt)return toast('Nhân vật chưa có prompt tạo ảnh.',true);action(async()=>{await api('/api/flow/create-image',{prompt});flow.blur();toast('Đã mở Google Flow và điền prompt. Chọn Hình ảnh · Nano Banana, tạo rồi tải ảnh xuống; sau đó bấm Thay ảnh → Lấy ảnh mới tải từ Flow.');});return;}
  if(pick){renderCharacterPicker(pick.dataset.charPick);return;}
  if(e.target.closest('[data-picker-close]')){$('characterPicker').hidden=true;return;}
  if(e.target.closest('[data-picker-flow]')){action(async()=>{const r=await api('/api/library/import-flow',{});await renderCharacterPicker($('characterPicker').dataset.character);toast(r.imported?`Đã lấy ${r.imported} ảnh mới từ Flow. Bấm ảnh để chọn làm Master.`:'Chưa thấy ảnh mới trong thư mục tải của Flow.',!r.imported);});return;}
  const token=e.target.closest('[data-picker-token]');
  if(token){const id=$('characterPicker').dataset.character;action(()=>useCharacterImage(id,token.dataset.pickerToken,'library'));return;}
  if(approveAsset){action(async()=>{await api('/api/characters/approve',{asset_id:approveAsset.dataset.approveAsset});await loadProject(project.id);toast('Đã chọn ảnh Master.');});return;}
  const save=e.target.closest('[data-char-save]'),remove=e.target.closest('[data-char-delete]'),lock=e.target.closest('[data-char-lock]');
  if(save){const card=save.closest('[data-character]');action(async()=>{await api('/api/characters/update',{id:save.dataset.charSave,name:card.querySelector('[data-char-name]').value,description:card.querySelector('[data-char-desc]').value,image_prompt:card.querySelector('[data-char-prompt]').value});save.blur();await loadProject(project.id);toast('Đã lưu nhân vật.');});}
  if(lock){const locked=lock.dataset.locked!=='1';action(async()=>{await api('/api/characters/lock',{id:lock.dataset.charLock,locked});lock.blur();await loadProject(project.id);toast(locked?'Đã duyệt và khóa nhân vật.':'Đã mở khóa nhân vật.');});}
  if(remove){const card=remove.closest('[data-character]');if(!confirm(`Xóa nhân vật ${card.querySelector('code').textContent}? Nhân vật cũng được bỏ khỏi các cảnh.`))return;action(async()=>{await api('/api/characters/delete',{id:remove.dataset.charDelete});selectedCharacter='';await loadProject(project.id);toast('Đã xóa nhân vật.');});}
};
$('projectScenes').onclick=e=>{
  const save=e.target.closest('[data-scene-characters]');if(!save)return;
  const row=save.closest('[data-project-scene]'),ids=[...row.querySelectorAll('input[type=checkbox]:checked')].map(x=>x.value);
  action(async()=>{await api('/api/scenes/characters',{scene_id:save.dataset.sceneCharacters,character_ids:ids});save.blur();await loadProject(project.id);toast(`Đã lưu ${ids.length} nhân vật cho cảnh.`);});
};
setInterval(()=>{if(!$('contentView').hidden)renderCharacterRun();},1000);
// ---------- Thanh 5 bước của Content Studio ----------
let currentStep=1;try{currentStep=Math.min(5,Math.max(1,Number(localStorage.getItem('hana-step'))||1));}catch{}
function stepDone(n){return n===1?Boolean(content.brief.trim()):n===2?content.scenes.length>0:n===3?Boolean(project?.characters.length):n===4?Boolean((content.english||content.englishChatgpt||'').trim()):false;}
function setStep(n,scroll=true){
  currentStep=n;try{localStorage.setItem('hana-step',String(n));}catch{}
  document.querySelectorAll('[data-step-panel]').forEach(el=>el.hidden=Number(el.dataset.stepPanel)!==n);
  document.querySelectorAll('.stepper [data-step]').forEach(b=>{const k=Number(b.dataset.step);b.classList.toggle('active',k===n);b.classList.toggle('done',k!==n&&stepDone(k));});
  if(n===5)renderOverview();
  markSidebar();if(scroll)window.scrollTo({top:0,behavior:'smooth'});
}
function renderOverview(){
  const english=(content.english||'').split('\n').filter(x=>x.trim()).length,chat=(content.englishChatgpt||'').split('\n').filter(x=>x.trim()).length;
  const chars=project?.characters||[],approved=chars.filter(c=>c.approved_asset_id).length;
  const tiles=[['Cảnh',content.scenes.length,'bước 2'],['Dự án',project?project.name:'Chưa lưu','bước 3'],['Nhân vật',chars.length,`${approved} đã duyệt · ${chars.length-approved} cần tạo ảnh`],['Prompt tiếng Anh · ChatGPT',chat,'bước 4']];
  $('overviewStats').innerHTML=tiles.map(([label,value,note])=>`<div class="overview-tile"><small>${esc(label)}</small><b>${esc(String(value))}</b><span>${esc(note)}</span></div>`).join('');
}
function markSidebar(){
  const content=!$('contentView').hidden,settings=!$('settingsView').hidden;
  $('navStudio').classList.toggle('active',!content&&!settings);$('navSettings').classList.toggle('active',settings);$('openContent').classList.toggle('active',content&&currentStep!==3);$('navCharacters').classList.toggle('active',content&&currentStep===3);
}
document.querySelectorAll('.stepper [data-step]').forEach(b=>b.onclick=()=>setStep(Number(b.dataset.step)));
document.addEventListener('click',e=>{const go=e.target.closest('[data-goto-step]');if(go)setStep(Number(go.dataset.gotoStep));});
$('navCharacters').onclick=()=>{showView('content');setStep(3);};
setInterval(()=>{if(state?.app_version)$('appVersion').textContent='v'+state.app_version;const runs=$('headerRuns').textContent;$('sideRuns').hidden=!Number(runs);$('sideRuns').textContent=runs;},1000);
// ---------- Cài đặt: đăng nhập các tài khoản tool cần ----------
function renderSettings(force){
  if(!state)return;
  const web=state.content?.web||{},chat=state.content?.chatgpt||{},engine=state.engine||{};
  const email=web.email||'',model=web.model||'';
  const badge=(id,account,name)=>{
    const a=account||{};let text,kind;
    if(a.logged_in===true){text='✓ Đã đăng nhập'+(a.email?` · ${a.email}`:'');kind='approved';}
    else if(a.logged_in===false){text='Chưa đăng nhập';kind='error';}
    else if(a.profile_used){text='Đã mở đăng nhập · kiểm tra ở lần chạy đầu';kind='review';}
    else{text='Chưa đăng nhập';kind='error';}
    $(id).textContent=text;$(id).className='status-chip '+kind;
    $(id).title=a.checked?`${name}: kiểm tra lần cuối ${new Date(a.checked*1000).toLocaleString('vi-VN')}`:`${name}: tool chưa mở trang lần nào`;
  };
  badge('geminiBadge',web.account,'Gemini');badge('chatgptBadge',chat.account,'ChatGPT');
  $('flowBadge').textContent=engine.flow_profile_used?'Đã mở đăng nhập trên máy này':'Chưa đăng nhập';$('flowBadge').className='status-chip '+(engine.flow_profile_used?'approved':'error');
  const seen=web.account?.logged_in?(web.account.email||'đã đăng nhập'):'';
  $('geminiAccountLine').innerHTML=`Tài khoản Gemini: <b>${esc(email||seen||'chưa đăng nhập')}</b>${model?` · model ${esc(model)}`:''} · <button type="button" class="text-button" data-open-settings>⚙ Đổi trong Cài đặt</button>`;
  if(force||document.activeElement!==$('chatgptModel'))$('chatgptModel').value=chat.model||'';
  $('flowProfile').textContent=engine.flow_profile||'';
  $('settingsFlowStatus').textContent=engine.flow_status||'';$('loginFlow').disabled=Boolean(engine.flow_busy);
  const accounts=state.accounts||[],ready=accounts.filter(a=>a.state==='ready').length,attention=accounts.filter(a=>a.state==='attention').length;
  $('museBadge').textContent=`${accounts.length} tài khoản`;
  $('museSummary').textContent=accounts.length?`${ready} sẵn sàng · ${attention} cần kiểm tra · ${accounts.length-ready-attention} khác`:'Chưa có tài khoản Muse. Thêm email rồi bấm Đăng nhập.';
  $('settingsFolder').textContent=engine.output_folder||'Chưa chọn thư mục lưu';
}
document.addEventListener('click',e=>{
  if(e.target.closest('[data-open-settings]'))showView('settings');
  if(e.target.closest('[data-open-accounts]'))$('accountsButton').click();
  if(e.target.closest('[data-pick-folder]'))$('pickFolder').click();
});
$('loginFlow').onclick=()=>action(async()=>{await api('/api/flow/login',{});$('loginFlow').blur();toast('Đang mở Chrome thường cho Google Flow. Đăng nhập xong thì đóng hẳn cửa sổ đó.');});
setInterval(()=>{renderSettings(false);},1500);
renderMode();await refresh();if(state?.content?.web?.busy)autoJob={id:state.content.web.id,kind:state.content.web.kind||'generate'};if(state?.content?.chatgpt?.busy)chatJob={id:state.content.chatgpt.id,kind:state.content.chatgpt.kind};for(const [engine,web] of [['gemini',state?.content?.web],['chatgpt',state?.content?.chatgpt]])if(web?.busy&&web.kind==='characters')charJob={id:web.id,engine};if(location.hash==='#content')showView('content');else if(location.hash==='#settings')showView('settings');{const m=location.hash.match(/^#job=([a-f0-9]{32})$/);if(m)openJob(m[1]);}setInterval(refresh,2000);

document.addEventListener('click',e=>{const b=e.target.closest('[data-review],[data-close-review]');if(!b)return;action(async()=>{await api(b.dataset.review?'/api/review':'/api/review-close',{id:b.dataset.review||b.dataset.closeReview});b.blur();toast(b.dataset.review?'Đang mở đúng phiên tài khoản. Đóng phiên trong mục Tài khoản khi kiểm tra xong.':'Đang đóng phiên kiểm tra.');});});

$('operation').onchange=()=>{const manual=$('operation').value==='assisted';$('headless').disabled=manual;if(manual)$('headless').checked=false;};
document.addEventListener('click',e=>{const b=e.target.closest('[data-copy]');if(!b)return;const job=state.jobs.find(j=>j.id===b.dataset.copy);if(!job)return;const request='Tạo video theo yêu cầu sau và trả về file video để tải xuống:\n\n'+job.prompt+'\n\nMã tác vụ: '+job.id;navigator.clipboard.writeText(request).then(()=>toast('Đã chép prompt. Dán vào đúng cửa sổ tác vụ đang chạy.')).catch(()=>toast('Không chép được. Chọn và sao chép prompt trong bảng.',true));});

document.addEventListener('click',e=>{const b=e.target.closest('[data-play]');if(!b)return;const job=state.jobs.find(j=>j.id===b.dataset.play);if(!job)return;$('videoPlayer').src='/api/media/'+job.id;$('playerPrompt').textContent=job.prompt;$('playerTitle').textContent='Video cảnh '+job.id.slice(0,12);$('player').showModal();});$('closePlayer').onclick=()=>$('player').close();$('player').onclose=()=>{$('videoPlayer').pause();$('videoPlayer').removeAttribute('src');$('videoPlayer').load();};
