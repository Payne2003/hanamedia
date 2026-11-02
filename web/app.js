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
function syncSettings(){if(!state)return;$('folderPath').textContent=state.engine.output_folder||'Chưa chọn thư mục lưu';$('accountCount').textContent=`${state.accounts.length}/10`;$('pause').disabled=!state.engine.enabled;$('run').disabled=state.engine.enabled;$('flowStatus').textContent=state.engine.flow_status||'Ảnh Flow tải xuống được lưu vào thư viện riêng của tool.';}
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
    return `<tr class="${job.status==='running'?'is-running':''}"><td class="select-col"><input class="job-select" data-select-job="${job.id}" type="checkbox" aria-label="Chọn ${esc(job.name)}" ${selectedJobs.has(job.id)?'checked':''} ${job.status==='running'?'disabled':''}></td><td>${esc(job.name.replace('Prompt ','')||'—')}</td><td class="thumb-col"><div class="job-thumb">${thumbnail}</div></td><td class="prompt-cell"><strong>${esc(job.name)}</strong>${job.status==='done'&&job.output?`<span class="job-filename">${esc(job.output.split(/[\\\\/]/).pop())}</span>`:''}<div class="prompt-text" title="${esc(job.prompt)}">${esc(job.prompt)}</div></td><td><span class="type-label">${job.generation_mode==='image'?'▧ Ảnh → video':'▣ Video'}</span></td><td><span class="account-chip">${esc(accounts.get(assigned)||'Chờ tài khoản')}</span></td><td><span class="badge ${esc(job.status)}">${esc(statusNames[job.status])}</span><div class="job-progress ${esc(job.status)}" role="progressbar" aria-label="Tiến độ ${esc(job.name)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}"><span style="width:${progress}%"></span></div><div class="progress-caption"><b>${estimated?'~':''}${progress}%</b><span class="detail ${job.error?'error':''}" title="${esc(job.error||job.phase)}">${esc(progressLabel)}</span></div></td><td><div class="row-actions"><button data-copy="${job.id}" title="Chép prompt" aria-label="Chép prompt">⧉</button>${job.status==='error'&&!job.submitted?`<button data-retry="${job.id}" title="Thử lại" aria-label="Thử lại">↻</button>`:''}${job.status==='done'?`<button data-play="${job.id}" title="Xem video" aria-label="Xem video">◉</button><a href="/api/video/${job.id}" title="Tải video" aria-label="Tải video">⇩</a>`:''}${job.account_id&&job.status==='error'?`<button data-review="${job.account_id}" title="Mở phiên Muse" aria-label="Mở phiên Muse">↗</button>`:''}</div></td></tr>`;
  }).join('');
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
function renderAccounts(){if(!state)return;const active=document.activeElement;if(active&&$('accountList').contains(active))return;$('accountList').innerHTML=state.accounts.length?state.accounts.map(a=>`<div class="account-row" data-account="${a.id}"><div class="account-top"><strong>${esc(a.email)}</strong><span class="badge ${a.state==='busy'?'running':a.state==='ready'?'done':a.state==='attention'?'error':''}">${esc(accountNames[a.state])}</span></div><div class="detail">${esc(a.detail||'Phiên Chrome riêng cho tài khoản này')}</div><div class="account-controls"><label><input data-enabled type="checkbox" ${a.enabled?'checked':''} ${['busy','authenticating','reviewing'].includes(a.state)?'disabled':''}> Sử dụng</label><label>Credit dự kiến <input data-budget type="number" min="0" step="0.01" value="${Number(a.budget).toFixed(2)}" ${['busy','authenticating','reviewing'].includes(a.state)?'disabled':''}></label><button data-save="${a.id}" ${['busy','authenticating','reviewing'].includes(a.state)?'disabled':''}>Lưu</button><button data-login="${a.id}" ${['busy','authenticating','reviewing'].includes(a.state)?'disabled':''}>Đăng nhập</button>${a.state==='reviewing'?`<button data-close-review="${a.id}">Đóng phiên</button>`:`<button data-review="${a.id}" ${['busy','authenticating'].includes(a.state)?'disabled':''}>Mở phiên Muse</button>`}</div></div>`).join(''):'<div class="notice">Thêm email đầu tiên để tạo phiên Chrome riêng.</div>';}
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
    await api('/api/jobs',{prompts,cost,mode:generationMode,batch:pendingBatch.id,images});
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
$('accountForm').onsubmit=e=>{e.preventDefault();action(async()=>{await api('/api/accounts',{email:$('email').value.trim(),mailbox:$('mailbox').value.trim(),budget:Number($('budget').value)});$('email').value='';$('mailbox').value='';toast('Đã thêm tài khoản. Bấm Đăng nhập để kiểm tra phiên.');});};
$('accountList').onclick=e=>{const save=e.target.closest('[data-save]'),login=e.target.closest('[data-login]');if(save){const row=save.closest('[data-account]');action(async()=>{await api('/api/accounts/update',{id:save.dataset.save,enabled:row.querySelector('[data-enabled]').checked,budget:Number(row.querySelector('[data-budget]').value)});save.blur();toast('Đã lưu ngân sách dự kiến.');});}if(login)action(async()=>{await api('/api/login',{id:login.dataset.login});login.blur();toast('Đang mở phiên Chrome. Nếu hộp thư lỗi, nhập OTP trực tiếp trên Muse.');});};
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
// ---- Viết content: Prompt + link video gốc → Gemini → cảnh → hàng loạt prompt ----
const sceneFields=[['setting','Bối cảnh'],['camera','Góc quay'],['behavior','Hành vi nhân vật'],['action','Nội dung hành động nhân vật']];
const defaultBrief='Phân tích video gốc và chia thành từng cảnh theo đúng thứ tự thời gian. Mỗi cảnh khoảng 8 giây, mô tả đủ chi tiết để tạo lại bằng AI video. Giữ nhân vật, trang phục và bối cảnh nhất quán giữa các cảnh.';
let content={brief:defaultBrief,video:'',language:'vi',script:'',scenes:[],issues:[]},contentBusy=false;
try{content={...content,...JSON.parse(localStorage.getItem('hana-content')||'{}')};}catch{}
const saveContent=()=>{try{localStorage.setItem('hana-content',JSON.stringify(content));}catch{}};
const oneLine=value=>String(value||'').replace(/\s+/g,' ').trim();
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
function renderGeminiStatus(){
  const info=state?.content;if(!info)return;
  $('geminiStatus').textContent=info.configured?`Đã có khóa · ${info.model}`:'Chưa có khóa API';
  $('geminiStatus').dataset.ok=String(info.configured);
  if(!$('geminiModel').value)$('geminiModel').value=info.model;
}
async function geminiCall(path,body,label){
  if(contentBusy)return null;
  contentBusy=true;const buttons=[$('runContent'),$('reviewContent')],old=buttons.map(b=>b.textContent);
  buttons.forEach(b=>b.disabled=true);$(path.endsWith('review')?'reviewContent':'runContent').textContent=label;
  try{return await api(path,body);}catch(error){toast(error.message,true);if(/khóa API/.test(error.message))$('geminiSettings').open=true;return null;}
  finally{contentBusy=false;buttons.forEach((b,i)=>{b.disabled=false;b.textContent=old[i];});}
}
function showView(view){
  const isContent=view==='content';
  $('studioView').hidden=isContent;$('contentView').hidden=!isContent;
  document.querySelectorAll('[data-nav]').forEach(b=>b.classList.toggle('active',b.dataset.nav===view));
  if(isContent){$('contentBrief').value=content.brief;$('contentVideo').value=content.video;$('contentLanguage').value=content.language;renderGeminiStatus();if(state?.content&&!state.content.configured)$('geminiSettings').open=true;renderScenes();}
  const hash=isContent?'#content':'';if(location.hash!==hash)history.replaceState(null,'',location.pathname+hash);
  window.scrollTo(0,0);
}
document.querySelectorAll('[data-nav]').forEach(b=>b.onclick=()=>showView(b.dataset.nav));
$('closeContent').onclick=()=>showView('studio');
window.addEventListener('hashchange',()=>showView(location.hash==='#content'?'content':'studio'));
$('contentBrief').oninput=()=>{content.brief=$('contentBrief').value;saveContent();};
$('contentVideo').oninput=()=>{content.video=$('contentVideo').value.trim();saveContent();};
$('contentLanguage').onchange=()=>{content.language=$('contentLanguage').value;saveContent();};
$('contentScript').oninput=()=>{content.script=$('contentScript').value;saveContent();};
$('saveGemini').onclick=()=>action(async()=>{await api('/api/content/settings',{api_key:$('geminiKey').value.trim(),model:$('geminiModel').value.trim()});$('geminiKey').value='';$('geminiSettings').open=false;toast('Đã lưu cài đặt Gemini.');}).then(renderGeminiStatus);
$('clearGemini').onclick=()=>action(async()=>{await api('/api/content/settings',{clear_key:true,model:$('geminiModel').value.trim()});toast('Đã xóa khóa API Gemini khỏi máy.');}).then(renderGeminiStatus);
$('runContent').onclick=async()=>{
  if(!content.brief.trim())return toast('Nhập yêu cầu (prompt) cho Gemini.',true);
  if(content.scenes.length&&!confirm('Phân tích lại sẽ thay thế danh sách cảnh hiện tại. Tiếp tục?'))return;
  const result=await geminiCall('/api/content/generate',{brief:content.brief,video_url:content.video,language:content.language},content.video?'Gemini đang xem video… (có thể 1–3 phút)':'Gemini đang viết…');
  if(!result)return;
  content.script=result.original_script;content.scenes=result.scenes;content.issues=[];renderScenes();
  toast(`Gemini đã chia ${result.scenes.length} cảnh. Bấm “Kiểm tra thiếu cảnh / lỗi” để rà lại.`);
};
$('reviewContent').onclick=async()=>{
  const result=await geminiCall('/api/content/review',{brief:content.brief,video_url:content.video,language:content.language,scenes:content.scenes},'Gemini đang kiểm tra…');
  if(!result)return;
  const before=content.scenes.length;content.scenes=result.scenes;content.issues=result.issues;renderScenes();
  toast(result.issues.length?`Đã sửa ${result.issues.length} vấn đề · ${before} → ${result.scenes.length} cảnh.`:'Không thấy thiếu cảnh hay lỗi.');
};
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

try{$('prompts').value=localStorage.getItem('huy-runner-draft')||'';const pending=JSON.parse(localStorage.getItem('huy-runner-pending')||'null');if(pending&&typeof pending.id==='string'&&typeof pending.signature==='string')pendingBatch=pending;}catch{}
renderMode();await refresh();if(location.hash==='#content')showView('content');setInterval(refresh,2000);

document.addEventListener('click',e=>{const b=e.target.closest('[data-review],[data-close-review]');if(!b)return;action(async()=>{await api(b.dataset.review?'/api/review':'/api/review-close',{id:b.dataset.review||b.dataset.closeReview});b.blur();toast(b.dataset.review?'Đang mở đúng phiên tài khoản. Đóng phiên trong mục Tài khoản khi kiểm tra xong.':'Đang đóng phiên kiểm tra.');});});

$('operation').onchange=()=>{const manual=$('operation').value==='assisted';$('headless').disabled=manual;if(manual)$('headless').checked=false;};
document.addEventListener('click',e=>{const b=e.target.closest('[data-copy]');if(!b)return;const job=state.jobs.find(j=>j.id===b.dataset.copy);if(!job)return;const request='Tạo video theo yêu cầu sau và trả về file video để tải xuống:\n\n'+job.prompt+'\n\nMã tác vụ: '+job.id;navigator.clipboard.writeText(request).then(()=>toast('Đã chép prompt. Dán vào đúng cửa sổ tác vụ đang chạy.')).catch(()=>toast('Không chép được. Chọn và sao chép prompt trong bảng.',true));});

document.addEventListener('click',e=>{const b=e.target.closest('[data-play]');if(!b)return;const job=state.jobs.find(j=>j.id===b.dataset.play);if(!job)return;$('videoPlayer').src='/api/media/'+job.id;$('playerPrompt').textContent=job.prompt;$('playerTitle').textContent='Video cảnh '+job.id.slice(0,12);$('player').showModal();});$('closePlayer').onclick=()=>$('player').close();$('player').onclose=()=>{$('videoPlayer').pause();$('videoPlayer').removeAttribute('src');$('videoPlayer').load();};
