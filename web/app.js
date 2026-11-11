import { initProduction, loadProduction } from '/production.js';
const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const apiModels={},apiChoice={};  // Cài đặt → API key (Gemini, ChatGPT)
const API_CARDS={gemini:{p:'geminiApi',state:'api',badge:'geminiBadge',label:'Gemini API',web:'Gemini trên Chrome',use:'Phân tích video'}};
let state=null,key='',filter='all',busy=false,pendingBatch=null,toastTimer,visibleJobs=[],mode='text',libraryImages=[];
const selectedJobs=new Set(),promptImages=new Map();
let licenseData=null,licenseEditing=null,licensePlan='1y';
const licenseStates={not_enforced:['Chưa bật khóa bản quyền','approved'],owner:['Super Admin','approved'],active:['Đang hoạt động','approved'],expiring:['Sắp hết hạn','review'],expired:['Hết hạn','error'],missing:['Chưa kích hoạt','error'],invalid:['Key không hợp lệ','error'],wrong_device:['Sai máy','error'],clock:['Sai đồng hồ','error'],not_started:['Chưa tới ngày','review'],revoked:['Đã bị thu hồi','error'],offline:['Cần kết nối mạng','error']};
const accountStates={active:['Đang hoạt động','approved'],expiring:['Sắp hết hạn','review'],expired:['Hết hạn','error'],locked:['Đã khóa','error']};
const vnd=n=>Number(n||0).toLocaleString('vi-VN')+' đ';
const dmy=d=>d?d.split('-').reverse().join('/'):'—';
let communitySig='';
const VIDEO_KEY='hana-video-settings';
let fixedCharacters=[];
const postLabel=p=>!p?'':p==='running'?'⏳ đang nâng':p.startsWith('upscaled:')?`✓ đã nâng ${p.slice(9)}`:p.startsWith('ok:')?`✓ đủ ${p.slice(3)}`:p.startsWith('error:')?'⚠ lỗi nâng':'';
const stitchNames={'':'Chờ đủ cảnh',running:'⏳ Đang ghép',done:'✓ Đã ghép',error:'⚠ Lỗi ghép'};
let mediaItems=[],mediaLimits={max_file_mb:20,max_batch:50},mediaType='',mediaCategory='',previewIndex=-1,pickerTarget='composer',pickerItems=[],mediaSearchTimer;
const mediaSelected=new Set(),pickerSelected=new Set();
const categoryNames={character:'Nhân vật',product:'Sản phẩm',reference:'Tham chiếu',other:'Khác'};
const sourceNames={uploaded:'Tải lên',generated:'Do tool tạo',imported:'Nhập từ thư mục'};
const formatBytes=n=>n>=1048576?`${(n/1048576).toFixed(1)} MB`:`${Math.max(1,Math.round(n/1024))} KB`;
const formatDate=t=>new Date(t*1000).toLocaleString('vi-VN',{dateStyle:'short',timeStyle:'short'});
const mediaSize=i=>i.media_type==='video'?'Video MP4':i.width?`${i.width}×${i.height} px`:'Ảnh';
const thumbnailVideos=new Map();
const thumbnailObserver='IntersectionObserver' in window?new IntersectionObserver(entries=>{for(const entry of entries){if(!entry.isIntersecting)continue;const video=entry.target;thumbnailObserver.unobserve(video);video.src=video.dataset.videoSrc;video.load();}},{rootMargin:'120px'}):null;
const statusNames={waiting:'Chờ',running:'Đang chạy',done:'Xong',error:'Lỗi'};
const accountNames={new:'Chưa kiểm tra',authenticating:'Đang đăng nhập',ready:'Sẵn sàng',busy:'Đang chạy',attention:'Cần kiểm tra',limited:'Hết hạn mức',reviewing:'Đang mở kiểm tra',checking:'Đang kiểm tra phiên'};
const healthNames={LIVE:'LIVE · phiên OK',CHECKING:'Đang kiểm tra',LOGIN_REQUIRED:'Cần đăng nhập',EXPIRED:'Phiên hết hạn',BUSY:'Đang bận',NETWORK_ERROR:'Lỗi mạng',RATE_LIMITED:'Bị giới hạn (429)',UNKNOWN:'Chưa rõ'};
const healthClass=h=>h==='LIVE'?'done':['LOGIN_REQUIRED','EXPIRED'].includes(h)?'error':['NETWORK_ERROR','RATE_LIMITED'].includes(h)?'warn':h==='CHECKING'?'running':'';
const ago=t=>{if(!t)return'chưa kiểm tra';const s=Math.max(0,Math.round(Date.now()/1000-t));return s<60?'vừa xong':s<3600?`${Math.floor(s/60)} phút trước`:s<86400?`${Math.floor(s/3600)} giờ trước`:new Date(t*1000).toLocaleString('vi-VN');};
function accountHealth(a){const h=['busy','authenticating','reviewing'].includes(a.state)&&a.health!=='CHECKING'?'BUSY':(a.health||'UNKNOWN');return h;}
function renderAccountStats(){const list=state.accounts,count=f=>list.filter(f).length;const stats=[['Tổng',list.length,''],['Sẵn sàng',count(a=>a.state==='ready'||(a.state==='new'&&a.health==='LIVE')),'done'],['Cần đăng nhập',count(a=>['LOGIN_REQUIRED','EXPIRED'].includes(a.health)||a.state==='attention'),'error'],['Đang bận',count(a=>['busy','authenticating','reviewing','checking'].includes(a.state)),'running']];$('accountStats').innerHTML=stats.map(([label,value,cls])=>`<div class="health-stat ${cls}"><b>${value}</b><span>${label}</span></div>`).join('');const h=state.engine.health||{};$('checkStatus').textContent=h.checking||h.queued?`Đang kiểm tra ${h.checking||0} · chờ ${h.queued||0}`:'';$('checkAll').disabled=!list.length;}
function toast(message,error=false){$('toast').textContent=message;$('toast').hidden=false;$('toast').dataset.error=String(error);clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,error?12000:5500);}
async function api(path,body){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Runner-Key':key},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(data.error||'Yêu cầu thất bại.');return data;}
function splitPrompts(){const value=$('prompts').value;const mode=$('separator').value;return value.split(mode==='blank'?/\r?\n\s*\r?\n/:mode==='custom'?/^\s*---\s*$/m:/\r?\n/).map(x=>x.trim()).filter(Boolean);}
function countPrompts(){const count=mode==='image_generate'?Number(Boolean($('prompts').value.trim())):splitPrompts().length;$('promptCount').textContent=`${count} ${mode==='image_generate'?'ảnh':'tác vụ'}`;try{localStorage.setItem('huy-runner-draft',$('prompts').value);}catch{}renderImageAssignments();}
function renderMode(){const imageVideo=mode==='image_video',imageGenerate=mode==='image_generate';document.querySelectorAll('.creation-tabs [data-mode]').forEach(button=>button.classList.toggle('active',imageGenerate?button.dataset.mode==='image_generate':button.dataset.mode!=='image_generate'));document.querySelectorAll('.video-submodes [data-mode]').forEach(button=>button.classList.toggle('active',button.dataset.mode===mode));$('composerTitle').textContent=imageGenerate?'Tạo ảnh':imageVideo?'Ảnh → video':'Tạo video';$('importButton').hidden=imageGenerate;document.querySelectorAll('.video-only').forEach(element=>element.hidden=imageGenerate);document.querySelector('.image-assignment').hidden=!imageVideo;$('prompts').placeholder=imageGenerate?'Mô tả ảnh bạn muốn tạo trong Google Flow…':'Mỗi dòng là một prompt. Viết mô tả video bạn muốn tạo…';countPrompts();}
function syncSettings(){if(!state)return;$('folderPath').textContent=state.engine.output_folder||'Chưa chọn thư mục lưu';$('accountCount').textContent=String(state.accounts.length);$('loginAllStatus').textContent=state.engine.login_all||'';$('loginAll').disabled=Boolean(state.engine.login_all_running);$('pause').disabled=!state.engine.enabled;$('run').disabled=state.engine.enabled;}
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
  $('topTotal').textContent=all;$('topFiles').textContent=totals.done;$('topRunning').textContent=state.accounts.reduce((n,account)=>n+Number(account.jobs_running||(account.state==='busy'?1:0)),0);$('headerRuns').textContent=$('topRunning').textContent;
  document.querySelector('.running-pill').title=state.engine?.keep_awake?'Đang tạo video: tool giữ máy không tự ngủ (màn hình vẫn tắt được) để Chrome không bị ngắt giữa chừng.':'';
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
    const progressLabel=job.status==='done'?'Hoàn thành':job.status==='error'?(job.error||'Dừng khi gặp lỗi'):job.phase;
    const account=esc(accounts.get(assigned)||(assigned&&job.status!=='waiting'?'TK đã xóa':(state.assign_hint?'⚠ Chưa có TK':'Chờ tài khoản')));
    const images=job.image_count?`<div class="jc-refs" title="${job.image_count} ảnh tham chiếu">${Array.from({length:Math.min(job.image_count,4)},(_,i)=>`<img src="/api/image/${job.id}/${i}" alt="" loading="lazy">`).join('')}${job.image_count>4?`<span>+${job.image_count-4}</span>`:''}</div>`:'';
    const spec=[job.aspect,job.duration?`${job.duration}s`:'',job.resolution].filter(Boolean).join(' · ');
    const post=job.status==='done'&&job.resolution?postLabel(job.post_status):'';
    const media=job.status==='done'&&job.output
      ?`<video data-thumb-job="${job.id}" data-video-src="/api/media/${job.id}" controls playsinline preload="none"></video>`
      :`<div class="jc-pending ${esc(job.status)}"><div class="jc-progress"><div class="job-progress ${esc(job.status)}" role="progressbar" aria-label="Tiến độ ${esc(job.name)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}"><span data-progress="${progress}"></span></div><div class="progress-caption"><b>${estimated?'~':''}${progress}%</b><span class="detail ${job.error?'error':''}" title="${esc(job.error||job.phase)}">${esc(progressLabel)}</span></div></div></div>`;
    return `<article class="job-card ${esc(job.status)}" data-job="${job.id}">
      <div class="jc-media">${media}
        <label class="jc-select"><input class="job-select" data-select-job="${job.id}" type="checkbox" aria-label="Chọn ${esc(job.name)}" ${selectedJobs.has(job.id)?'checked':''} ${job.status==='running'?'disabled':''}><span>#${esc(job.name.replace('Prompt ','')||'—')}</span></label>
        <span class="jc-status badge ${esc(job.status)}">${esc(statusNames[job.status])}</span>
        ${spec?`<span class="jc-spec">${esc(spec)}</span>`:''}
      </div>
      <div class="jc-body">
        <div class="jc-title prompt-cell" title="Mở chi tiết"><b>${esc(job.name)}</b>${job.status==='done'&&job.output?`<small>${esc(job.output.split(/[\\\\/]/).pop())}</small>`:''}</div>
        <div class="jc-meta"><span class="account-chip${!assigned&&job.status==='waiting'&&state.assign_hint?' warn':''}" title="${esc(!assigned&&job.status==='waiting'?(state.assign_hint||'Tool sẽ tự chia tài khoản'):'')}">👤 ${account}</span>${post?`<span class="jc-post${job.post_status.startsWith('error:')?' error':''}" title="${esc(job.post_status.startsWith('error:')?job.post_status.slice(6):'')}">${esc(post)}</span>`:''}</div>
        <p class="jc-prompt prompt-cell" title="${esc(job.prompt)}">${esc(job.prompt)}</p>
        <div class="jc-foot">${images}<div class="jc-actions row-actions"><button data-copy="${job.id}" title="Chép prompt" aria-label="Chép prompt">⧉</button>${job.status==='error'&&!job.submitted?`<button data-retry="${job.id}" title="Thử lại" aria-label="Thử lại">↻</button>`:''}${job.status==='done'?`<button data-play="${job.id}" title="Xem lớn" aria-label="Xem lớn">⛶</button><a href="/api/video/${job.id}" title="Tải video" aria-label="Tải video">⇩</a>`:''}${job.status==='error'&&job.submitted&&job.source_url?`<button data-fetch="${job.id}" title="Lấy video Muse đã làm xong (không gửi lại prompt)">⇩ Lấy video</button>`:''}${job.account_id&&job.status==='error'?`<button data-review="${job.account_id}" title="Mở phiên Muse" aria-label="Mở phiên Muse">↗</button>`:''}</div></div>
      </div>
    </article>`;
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
async function uploadImage(file){const data=await sendMedia(file);return {token:data.token,type:data.type,name:data.name};}
async function refreshLibrary(){const response=await fetch('/api/library');if(!response.ok)throw Error('Không tải được thư viện ảnh.');libraryImages=(await response.json()).images;return libraryImages;}
function renderLibrary(){renderLibraryIfOpen();}
function safeMuse(url){try{const u=new URL(url);return u.protocol==='https:'&&u.hostname==='muse.ai';}catch{return false;}}
function renderAccounts(){if(!state)return;renderNickOptions();renderStitchPanel();renderAccountStats();const active=document.activeElement;if(active&&$('accountList').contains(active))return;$('accountList').innerHTML=state.accounts.length?state.accounts.map(a=>`<div class="account-row" data-account="${a.id}"><div class="account-top"><strong>${esc(a.email)}</strong><span class="badge ${a.state==='busy'?'running':a.state==='ready'?'done':a.state==='attention'||a.state==='limited'?'error':''}">${esc(accountNames[a.state]||a.state)}${a.state==='limited'&&a.limited_until?` · đặt lại ${esc(new Date(a.limited_until*1000).toLocaleDateString('vi-VN',{day:'2-digit',month:'2-digit'}))}`:''}</span></div>${a.video_blocked?`<div class="video-blocked">⛔ Bị Muse chặn tạo video ở khu vực của tài khoản — tool không giao prompt cho tài khoản này. <button type="button" data-unblock="${a.id}">Bỏ chặn</button></div>`:''}<div class="detail">${esc(a.detail||'Phiên Chrome riêng cho tài khoản này')}</div><div class="account-health"><span class="badge ${healthClass(accountHealth(a))}">${esc(healthNames[accountHealth(a)]||accountHealth(a))}</span><span>Kiểm tra: ${esc(ago(a.last_checked))}</span><span>Chờ ${Number(a.jobs_assigned||0)} · Xong ${Number(a.jobs_completed||0)} · Lỗi ${Number(a.jobs_failed||0)}</span>${a.last_error&&a.last_error!==a.detail?`<span class="detail error">${esc(a.last_error)}</span>`:''}</div><div class="account-controls"><label><input data-enabled type="checkbox" ${a.enabled?'checked':''} ${['busy','authenticating','reviewing'].includes(a.state)?'disabled':''}> Sử dụng</label><label>Credit dự kiến <input data-budget type="number" min="0" step="0.01" value="${Number(a.budget).toFixed(2)}" ${['busy','authenticating','reviewing'].includes(a.state)?'disabled':''}></label><button data-save="${a.id}" ${['busy','authenticating','reviewing','checking'].includes(a.state)?'disabled':''}>Lưu</button><button data-check="${a.id}" ${['busy','authenticating','reviewing','checking'].includes(a.state)?'disabled':''} title="Mở muse.ai bằng hồ sơ của tài khoản để xem phiên còn đăng nhập không">↻ Kiểm tra</button><button data-login="${a.id}" ${['busy','authenticating','reviewing','checking'].includes(a.state)?'disabled':''}>Đăng nhập</button>${a.state==='reviewing'?`<button data-close-review="${a.id}">Đóng phiên</button>`:`<button data-review="${a.id}" ${['busy','authenticating','checking'].includes(a.state)?'disabled':''}>Mở phiên Muse</button>`}<button class="danger-outline" data-delete-account="${a.id}" data-email="${esc(a.email)}" ${['busy','authenticating','reviewing','checking'].includes(a.state)?'disabled':''} title="Xóa tài khoản khỏi tool">✕ Xóa</button></div></div>`).join(''):'<div class="notice">Thêm email đầu tiên để tạo phiên Chrome riêng.</div>';}
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
  if(generationMode==='image'&&!fixedCharacters.length&&imagesByPrompt.some(images=>!images.length))return toast('Chế độ Ảnh → video yêu cầu chọn ít nhất một ảnh cho từng prompt (hoặc thêm Ảnh nhân vật cố định).',true);
  const options=videoOptions();
  if(imagesByPrompt.some(images=>new Set([...options.characters,...images.map(i=>i.token)]).size>10))return toast('Ảnh nhân vật cố định cộng ảnh riêng của một prompt vượt quá 10 ảnh.',true);
  busy=true;$('create').disabled=$('add').disabled=true;
  try{
    const signature=JSON.stringify([prompts,cost,generationMode,imagesByPrompt.map(images=>images.map(image=>image.token)),options]);
    if(pendingBatch?.signature!==signature)pendingBatch={signature,id:crypto.randomUUID()};
    try{localStorage.setItem('huy-runner-pending',JSON.stringify(pendingBatch));}catch{}
    const images=imagesByPrompt.map(assets=>assets.map(asset=>asset.token));
    await api('/api/jobs',{prompts,cost,mode:generationMode,batch:pendingBatch.id,images,sources:prompts.map(prompt=>promptSources[prompt]||''),scene_ids:prompts.map(prompt=>promptScenes[prompt]||null),options});
    pendingBatch=null;
    try{localStorage.removeItem('huy-runner-pending');}catch{}
    $('prompts').value='';promptImages.clear();countPrompts();
    if(run)await api('/api/start',{parallel:Number($('parallel').value),headless:$('headless').checked,automatic:$('operation').value==='auto'});
    toast(`Đã thêm ${prompts.length} tác vụ${run?' và bật bộ chạy':''}.`);
  }catch(e){toast(e.message,true);}
  finally{busy=false;$('create').disabled=$('add').disabled=false;await refresh();}
}
$('create').onclick=()=>addJobs(true);$('add').onclick=()=>addJobs(false);
$('run').onclick=()=>action(async()=>{await api('/api/start',{parallel:Number($('parallel').value),headless:$('headless').checked,automatic:$('operation').value==='auto'});toast('Đã bật bộ chạy.');});
$('pause').onclick=()=>action(async()=>{await api('/api/pause',{});toast('Đã dừng nhận thêm. Tác vụ đang chạy vẫn tiếp tục tải video.');});
$('pickFolder').onclick=()=>action(async()=>{$('pickFolder').disabled=true;toast('Chọn thư mục trong cửa sổ Windows vừa mở.');try{await api('/api/folder',{});}finally{$('pickFolder').disabled=false;}});
$('openFolder').onclick=()=>action(()=>api('/api/open-folder',{}));
$('openFolderSecondary').onclick=()=>$('openFolder').click();
$('chooseImagesDrop').onclick=()=>$('characterImages').click();
$('accountsButton').onclick=()=>{$('accounts').showModal();renderAccounts();};$('closeAccounts').onclick=()=>$('accounts').close();
$('accountForm').onsubmit=e=>{e.preventDefault();action(async()=>{const emails=$('email').value.split(/[\s,;]+/).filter(Boolean),budget=Number($('budget').value);if(emails.length===1){await api('/api/accounts',{email:emails[0],mailbox:$('mailbox').value.trim(),budget});toast('Đã thêm tài khoản. Bấm Đăng nhập để kiểm tra phiên.');}else{const r=await api('/api/accounts',{emails:emails.join('\n'),budget});toast(`Đã thêm ${r.added} tài khoản`+(r.duplicate?` · bỏ qua ${r.duplicate} email trùng`:'')+(r.invalid_count?` · ${r.invalid_count} email sai: ${r.invalid.join(', ')}`:''));}$('email').value='';$('mailbox').value='';});};
$('loginAll').onclick=()=>action(async()=>{const r=await api('/api/accounts/login-all',{parallel:Number($('loginParallel').value)});$('loginAll').blur();toast(`Đang đăng nhập ${r.count} tài khoản. Tool tự lấy OTP từ hộp thư Tuan89.`);});
$('checkAll').onclick=()=>action(async()=>{const r=await api('/api/accounts/check-all',{parallel:Number($('checkParallel').value)});$('checkAll').blur();toast(`Đã xếp hàng kiểm tra ${r.queued} tài khoản${r.busy.length?`, bỏ qua ${r.busy.length} tài khoản đang chạy`:''}.`);});
$('accountList').addEventListener('click',e=>{const b=e.target.closest('[data-unblock]');if(!b)return;
  if(!confirm('Bỏ chặn tài khoản này? Chỉ bỏ chặn khi chắc chắn tài khoản đã tạo được video trên Muse, nếu không prompt gửi tới sẽ lại bị từ chối.'))return;
  action(async()=>{await api('/api/accounts/unblock',{id:b.dataset.unblock});toast('Đã bỏ chặn tài khoản.');});});
$('accountList').onclick=e=>{const save=e.target.closest('[data-save]'),login=e.target.closest('[data-login]'),check=e.target.closest('[data-check]');if(check)action(async()=>{const r=await api('/api/accounts/check',{id:check.dataset.check});check.blur();toast(r.queued?'Đang kiểm tra phiên Muse của tài khoản này.':r.busy.length?'Tài khoản đang chạy: không kiểm tra để khỏi chen vào tác vụ.':'Tài khoản đã có trong hàng kiểm tra.');});if(save){const row=save.closest('[data-account]');action(async()=>{await api('/api/accounts/update',{id:save.dataset.save,enabled:row.querySelector('[data-enabled]').checked,budget:Number(row.querySelector('[data-budget]').value)});save.blur();toast('Đã lưu credit dự kiến.');});}if(login)action(async()=>{await api('/api/login',{id:login.dataset.login});login.blur();toast('Đang mở phiên Chrome. Nếu hộp thư lỗi, nhập OTP trực tiếp trên Muse.');});const remove=e.target.closest('[data-delete-account]');if(remove){if(!confirm(`Xóa tài khoản ${remove.dataset.email}?\n\nTool đóng phiên Chrome và xóa phiên đăng nhập của tài khoản này trên máy. Prompt đang chờ sẽ được chia cho tài khoản khác. Video đã tải vẫn giữ nguyên.`))return;remove.blur();action(async()=>{const {email}=await api('/api/accounts/delete',{id:remove.dataset.deleteAccount});toast(`Đã xóa tài khoản ${email}.`);});}};
$('rows').onclick=e=>{const button=e.target.closest('[data-retry]');if(button)action(()=>api('/api/retry',{id:button.dataset.retry}));
  const fetchButton=e.target.closest('[data-fetch]');if(fetchButton)action(async()=>{await api('/api/jobs/fetch',{id:fetchButton.dataset.fetch});toast('Đang mở lại cuộc trò chuyện Muse để lấy video…');});};
document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(t=>t.classList.toggle('active',t===b));renderJobs();});
$('search').oninput=renderJobs;$('prompts').oninput=countPrompts;$('separator').onchange=countPrompts;
document.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>{mode=button.dataset.mode;renderMode();});
document.querySelectorAll('[data-coming]').forEach(button=>button.onclick=()=>toast('Chức năng này sẽ được phát triển ở bước tiếp theo.'));
$('imagePromptIndex').onchange=renderImageAssignments;
$('chooseImages').onclick=()=>$('characterImages').click();
$('chooseLibrary').onclick=()=>openPicker('composer');
$('closeImageLibrary').onclick=()=>$('imageLibrary').close();
$('characterImages').onchange=async e=>{
  const index=Number($('imagePromptIndex').value),prompts=splitPrompts(),files=[...e.target.files];
  e.target.value='';
  if(!prompts.length)return toast('Nhập prompt trước khi chọn ảnh.',true);
  if(files.some(file=>!['image/png','image/jpeg','image/webp'].includes(file.type)))return toast('Chỉ chọn ảnh JPG, PNG hoặc WEBP.',true);
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
const defaultBrief='Phân tích video gốc và chia thành từng cảnh 10 giây theo đúng thứ tự thời gian. Mỗi cảnh liệt kê từng góc máy theo mốc giây (cỡ cảnh, góc máy, chuyển động máy), mô tả bối cảnh, hành vi và hành động nhân vật đủ chi tiết để tạo lại bằng AI video. Giữ nhân vật, trang phục và bối cảnh nhất quán giữa các cảnh.';
const oldBrief='Phân tích video gốc và chia thành từng cảnh theo đúng thứ tự thời gian. Mỗi cảnh khoảng 15 giây, mô tả đủ chi tiết để tạo lại bằng AI video. Giữ nhân vật, trang phục và bối cảnh nhất quán giữa các cảnh.';
let content={brief:defaultBrief,video:'',videoFile:'',language:'vi',script:'',scenes:[],issues:[],englishInput:'',englishStyle:'',englishAspect:'16:9',englishStructured:true,englishSeconds:10,windows:[],englishAngles:0,englishIssues:[],english:'',englishMissing:[],englishEngine:'chatgpt',englishChatgpt:'',englishChatgptMissing:[]};
try{const saved=JSON.parse(localStorage.getItem('hana-content')||'{}');content={...content,reviewed:saved.reviewed===true,reviewViolations:saved.reviewViolations&&typeof saved.reviewViolations==='object'?saved.reviewViolations:{},reviewIssues:Array.isArray(saved.reviewIssues)?saved.reviewIssues:[],brief:saved.brief&&saved.brief!==oldBrief?saved.brief:content.brief,windows:Array.isArray(saved.windows)?saved.windows:[],video:saved.video||'',videoFile:typeof saved.videoFile==='string'?saved.videoFile:'',videoFrom:typeof saved.videoFrom==='string'?saved.videoFrom:'',language:saved.language||'vi',script:saved.script||'',scenes:Array.isArray(saved.scenes)?saved.scenes:[],issues:Array.isArray(saved.issues)?saved.issues:[],englishInput:saved.englishInput||'',englishStyle:saved.englishStyle||'',englishAspect:['16:9','9:16','1:1'].includes(saved.englishAspect)?saved.englishAspect:'16:9',englishStructured:saved.englishStructured!==false,englishFormat:saved.englishFormat==='labels'?'labels':'detailed',englishSeconds:[5,8,10].includes(Number(saved.englishSeconds))?Number(saved.englishSeconds):10,english:saved.english||'',englishMissing:Array.isArray(saved.englishMissing)?saved.englishMissing:[],englishEngine:'chatgpt',englishChatgpt:saved.englishChatgpt||'',englishChatgptMissing:Array.isArray(saved.englishChatgptMissing)?saved.englishChatgptMissing:[],englishSources:Array.isArray(saved.englishSources)?saved.englishSources:null,englishChatgptSources:Array.isArray(saved.englishChatgptSources)?saved.englishChatgptSources:null};}catch{}
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
  $('sceneList').innerHTML=content.scenes.map((scene,index)=>`<article class="scene-card"><div class="scene-top"><b>Cảnh ${index+1}</b>${sceneTime(index)}<span class="bulk-spacer"></span><button type="button" data-scene-move="${index}" data-dir="-1" ${index?'':'disabled'} aria-label="Đưa cảnh ${index+1} lên">↑</button><button type="button" data-scene-move="${index}" data-dir="1" ${index<content.scenes.length-1?'':'disabled'} aria-label="Đưa cảnh ${index+1} xuống">↓</button><button type="button" class="danger-outline" data-scene-delete="${index}" aria-label="Xóa cảnh ${index+1}">✕</button></div><div class="scene-fields">${sceneFields.map(([field,name])=>`<label>${name}<textarea rows="2" data-scene="${index}" data-field="${field}">${esc(scene[field])}</textarea></label>`).join('')}${scene.subjects!==undefined?`<label class="scene-subjects">Nhân vật · số lượng · vị trí<textarea rows="2" data-scene="${index}" data-field="subjects">${esc(scene.subjects)}</textarea></label>`:''}</div></article>`).join('');
  renderContentOutput();
}
const CONTENT_VERSION='content-22';
const fmtClock=s=>{s=Math.max(0,Number(s)||0);return `${String(Math.floor(s/60)).padStart(2,'0')}:${String(Math.floor(s%60)).padStart(2,'0')}`;};
function sceneTime(index){const w=content.windows?.[index];if(!w)return '';const angles=(w.angles||[]).map(([a,b])=>`${a}–${b}s`).join(' · ');return `<span class="scene-time" title="Góc máy FFmpeg đo được: ${esc(angles)}">${fmtClock(w.start)}–${fmtClock(w.end)} · ${w.shot?`${(w.end-w.start).toFixed(1)} giây · 1 góc máy`:`${(w.angles||[]).length} góc máy`}</span>`;}
function saveBlob(blob,name){const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);}
$('downloadContentJson').onclick=()=>saveBlob(new Blob([JSON.stringify({original_script:content.script,scenes:content.scenes.map((s,i)=>({scene:i+1,start:content.windows?.[i]?.start??null,end:content.windows?.[i]?.end??null,measured_angles:content.windows?.[i]?.angles??[],...s})),issues:content.issues},null,2)],{type:'application/json'}),'kich-ban.json');
$('downloadContentXlsx').onclick=async()=>{try{const r=await fetch('/api/content/export-xlsx',{method:'POST',headers:{'Content-Type':'application/json','X-Runner-Key':key},body:JSON.stringify({scenes:content.scenes,windows:content.windows,script:content.script,subjects:content.scenes.map(s=>s.subjects||'')})});if(!r.ok)throw Error((await r.json()).error||'Không xuất được Excel.');saveBlob(await r.blob(),'kich-ban.xlsx');}catch(error){toast(error.message,true);}};
function renderRunnerVersion(){if(state)$('runnerOutdated').hidden=state.content?.version===CONTENT_VERSION;}
function showView(view){
  if(view==='community')view='studio';  // mục Cộng đồng đã bỏ
  const isProduction=view==='production',isContent=view==='content',isSettings=view==='settings',isMedia=view==='media',isCommunity=view==='community',isLicense=view==='license';
  $('productionView').hidden=!isProduction;if(isProduction)loadProduction();
  $('jobView').hidden=true;$('studioView').hidden=isProduction||isContent||isSettings||isMedia||isCommunity||isLicense;$('communityView').hidden=!isCommunity;$('licenseView').hidden=!isLicense;$('contentView').hidden=!isContent;$('settingsView').hidden=!isSettings;$('mediaView').hidden=!isMedia;
  if(isMedia)loadMedia();
  if(isCommunity)renderCommunity();
  if(isLicense)loadLicense();
  if(isSettings){applyAutoSettings(true);renderSettings(true);}
  document.querySelectorAll('[data-nav]').forEach(b=>b.classList.toggle('active',b.dataset.nav===view));
  if(isContent){$('contentBrief').value=content.brief;$('contentVideo').value=content.video;renderVideoFile();$('contentLanguage').value=content.language;renderRunnerVersion();applyAutoSettings(true);renderAuto();renderScenes();$('englishInput').value=content.englishInput;$('englishStyle').value=content.englishStyle;$('englishAspect').value=content.englishAspect;$('englishStructured').checked=content.englishStructured;$('englishSeconds').value=String(content.englishSeconds);$('englishAngles').value=String(content.englishAngles??'lib');renderEnglishEngine();openCharacters();}
  const hash=isProduction?'#production':isContent?'#content':isSettings?'#settings':isMedia?'#media':isCommunity?'#community':isLicense?'#license':'';if(location.hash!==hash)history.replaceState(null,'',location.pathname+hash);
  window.scrollTo(0,0);markSidebar();
}
document.querySelectorAll('[data-nav]').forEach(b=>b.onclick=()=>showView(b.dataset.nav));
$('closeContent').onclick=()=>showView('studio');
window.addEventListener('hashchange',()=>{if(/^#job=/.test(location.hash))return;showView(location.hash==='#production'?'production':location.hash==='#content'?'content':location.hash==='#settings'?'settings':location.hash==='#media'?'media':location.hash==='#community'?'community':location.hash==='#license'?'license':'studio');});
$('contentBrief').oninput=()=>{content.brief=$('contentBrief').value;saveContent();};
$('contentVideo').oninput=()=>{content.video=$('contentVideo').value.trim();
  // File tải từ link cũ: đổi link thì bỏ file để không gửi nhầm video của lần trước cho Gemini.
  if(content.videoFile&&content.videoFrom&&content.videoFrom!==content.video){content.videoFile='';content.videoFrom='';renderVideoFile();}
  saveContent();};
// Đính kèm FILE video gốc cho Gemini (xem đủ từng khung hình thay vì đọc link); tool còn đo điểm cắt để Gemini viết đủ cảnh.
function renderVideoFile(){
  const file=content.videoFile;$('contentClearVideo').hidden=!file;
  $('contentVideoFile').textContent=file?`📎 Gemini sẽ xem file: ${file.split(/[\\/]/).pop()} · tool đo điểm cắt để viết đủ cảnh`:'Chưa đính kèm file. Tải video từ link hoặc chọn file để Gemini xem đủ từng cảnh (chính xác hơn đọc link).';
}
let contentDownloadTimer=null;
async function pollContentDownload(){
  try{
    const st=await (await fetch('/api/production/download')).json(),bar=$('contentDownloadProgress');
    bar.hidden=!st.busy;bar.value=st.progress||0;
    if(st.busy){$('contentVideoFile').textContent=st.status||'Đang tải…';contentDownloadTimer=setTimeout(pollContentDownload,1000);return;}
    $('contentDownload').disabled=false;contentDownloadTimer=null;
    if(st.error){toast(st.error,true);renderVideoFile();return;}
    if(st.path){content.videoFile=st.path;content.videoFrom=st.url||content.video;saveContent();renderVideoFile();toast('Đã tải video gốc. Bấm Chạy tự động trên Gemini: tool sẽ đính kèm file này.');}
  }catch(error){$('contentDownload').disabled=false;toast(error.message,true);}
}
$('contentDownload').onclick=async()=>{try{const url=$('contentVideo').value.trim();if(!url)throw Error('Dán link video gốc trước.');await api('/api/production/download',{url});$('contentDownload').disabled=true;if(contentDownloadTimer)clearTimeout(contentDownloadTimer);pollContentDownload();}catch(error){toast(error.message,true);}};
$('contentPickVideo').onclick=async()=>{try{const r=await api('/api/production/pick-video',{});if(r.path){content.videoFile=r.path;content.videoFrom='';saveContent();renderVideoFile();}}catch(error){toast(error.message,true);}};
$('contentClearVideo').onclick=()=>{content.videoFile='';content.videoFrom='';saveContent();renderVideoFile();};
$('contentLanguage').onchange=()=>{content.language=$('contentLanguage').value;saveContent();};
$('contentScript').oninput=()=>{content.script=$('contentScript').value;saveContent();};
// Gemini Pro tự động: tool điều khiển Chrome đã đăng nhập tài khoản Google.
let autoJob=null,pipeline=null;
function applyAutoSettings(force){const web=state?.content?.web;if(!web)return;if(force||document.activeElement!==$('autoEmail'))$('autoEmail').value=web.email||'';if(force||document.activeElement!==$('autoModel'))$('autoModel').value=web.model||'';}
async function saveAutoSettings(){await api('/api/content/web-settings',{email:$('autoEmail').value.trim(),model:$('autoModel').value.trim()});}
$('autoEmail').onchange=()=>saveAutoSettings().then(refresh).then(()=>renderSettings(false)).catch(error=>toast(error.message,true));
$('autoModel').onchange=()=>saveAutoSettings().then(refresh).then(()=>renderSettings(false)).catch(error=>toast(error.message,true));
let lastJobError='';
async function runAuto(kind,silent=false){
  if(autoJob)return false;
  if(kind==='review'&&!content.scenes.length)return false;
  if(kind!=='review'&&!content.brief.trim()){toast('Nhập yêu cầu (prompt) cho Gemini.',true);return false;}
  if(!silent&&kind!=='review'&&content.scenes.length&&!confirm('Chạy lại sẽ thay thế danh sách cảnh hiện tại. Tiếp tục?'))return false;
  try{
    await saveAutoSettings();
    const {id}=await api('/api/content/web-run',{kind,brief:content.brief,video_url:content.video,video_file:content.videoFile||undefined,language:content.language,split:$('sceneSplit').value,scenes:kind==='review'?content.scenes:undefined});
    autoJob={id,kind};lastJobError='';renderAuto();refresh();return true;
  }catch(error){toast(error.message,true);return false;}
}
function renderAuto(){
  const web=state?.content?.web,running=Boolean(autoJob),english=running&&!['generate','review'].includes(autoJob.kind);
  const text=running?(web&&web.id===autoJob.id?(web.status||''):'Đang mở Gemini…'):'';
  $('autoProgress').hidden=!running||english;
  for(const id of ['runAuto','loginAuto','reviewContent'])$(id).disabled=running;
  $('runAuto').textContent=running&&autoJob.kind==='generate'?'Gemini đang chạy…':'✦ Chỉ chạy bước phân tích trên Gemini';
  $('runPipeline').disabled=Boolean(pipeline)||running||Boolean(chatJob);$('runReview').disabled=running||Boolean(chatJob);
  $('reviewContent').textContent=running&&autoJob.kind==='review'?'Gemini đang kiểm tra…':'↻ Kiểm tra thiếu cảnh / lỗi';
  if(running&&!english)$('autoStatus').textContent=text;
  renderEnglishRun();
}
function pollAuto(){
  const web=state?.content?.web;applyAutoSettings();renderRunnerVersion();loadReviewRules();
  if(!autoJob||!web){renderAuto();return;}
  if(web.id===autoJob.id&&web.busy)applyPartial('gemini',autoJob,web);
  if(web.id!==autoJob.id||web.busy){renderAuto();return;}
  const job=autoJob;autoJob=null;renderAuto();
  if(job.kind==='characters')return finishCharacters('gemini',job.id,web);
  if(web.error){lastJobError=web.error;if(typeof reviewOne!=='undefined')reviewOne=null;return toast(web.error,true);}
  const result=web.result;if(!result){lastJobError='Không có kết quả.';return;}
  if(job.kind==='english')return finishEnglish('gemini',result);
  if(job.kind==='promptreview')return finishReview('gemini',result);
  if(job.kind==='review'){content.scenes=result.scenes;content.issues=result.issues;}
  else{content.scenes=result.scenes;content.script=result.original_script;content.issues=result.issues||[];content.windows=Array.isArray(result.windows)?result.windows:[];
    if(Array.isArray(result.subjects))content.scenes.forEach((scene,i)=>{scene.subjects=(result.subjects[i]||[]).map(s=>s.name+(s.count?` ×${s.count}`:'')+(s.detail?` (${s.detail})`:'')).join('; ');});}
  if(result.shots)toast(result.windows?`Video gốc: ${result.shots} cảnh 10 giây · Gemini viết ${result.scenes.length} cảnh · ${result.verified??0} cảnh khớp điểm cắt FFmpeg.`:`Video gốc có ${result.shots} shot · Gemini viết ${result.scenes.length} cảnh.`);
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
// Kịch bản → đầu vào bước Tạo prompt (prompt tiếng Anh cho Muse), không đưa thẳng vào Studio nữa.
$('useContent').onclick=()=>{
  const prompts=contentPrompts();if(!prompts.length)return toast('Chưa có cảnh nào để đưa sang.',true);
  if($('englishInput').value.trim()&&$('englishInput').value.trim()!==prompts.join('\n')&&!confirm('Thay nội dung đang có ở đầu vào phần Tạo prompt bằng kịch bản này?'))return;
  $('englishInput').value=prompts.join('\n');content.englishInput=$('englishInput').value;saveContent();
  $('englishInput').dispatchEvent(new Event('input',{bubbles:true}));
  setStep(4);$('englishInput').focus();
  toast(`Đã đưa ${prompts.length} cảnh sang phần Tạo prompt.`);
};
$('copyContent').onclick=()=>navigator.clipboard.writeText($('contentPrompts').value).then(()=>toast('Đã chép hàng loạt prompt.')).catch(()=>toast('Không chép được. Chọn và sao chép trong ô bên dưới.',true));
$('downloadContent').onclick=()=>{const blob=new Blob([[content.script?'KỊCH BẢN VIDEO GỐC\n'+content.script+'\n\nPROMPT\n':'',$('contentPrompts').value].join('')],{type:'text/plain;charset=utf-8'});const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download='content-prompt.txt';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);};



// ---- Prompt tiếng Anh (Gemini Pro hoặc ChatGPT; kết quả mỗi bên lưu riêng để so sánh) ----
const engineNames={gemini:'Gemini',chatgpt:'ChatGPT'};
let chatJob=null;
const englishKey=engine=>engine==='chatgpt'?['englishChatgpt','englishChatgptMissing']:['english','englishMissing'];
const englishLines=()=>$('englishOutput').value.split('\n');
function englishJob(engine){return engine==='chatgpt'?(chatJob&&chatJob.kind!=='characters'?chatJob:null):(autoJob&&['english','promptreview'].includes(autoJob.kind)?autoJob:null);}
function renderEnglish(){
  const [textKey,missingKey]=englishKey(content.englishEngine);
  const lines=englishLines(),filled=lines.filter(line=>line.trim()).length,missing=content[missingKey]||[];
  $('englishCount').textContent=content[textKey]?`${filled} prompt · ${engineNames[content.englishEngine]}`:'';
  const issues=content[textKey+'Issues']||[];
  $('englishMissing').hidden=!missing.length&&!issues.length;
  $('englishMissing').innerHTML=(missing.length?`<b>Còn thiếu hoặc chưa đủ trường</b><ul><li>Cảnh ${missing.join(', ')} — dòng tương ứng đang để trống, còn tiếng Việt hoặc thiếu trường của Master Prompt 08. Bấm viết lại hoặc tự sửa.</li></ul>`:'')+
    (issues.length?`<b>AI báo mâu thuẫn trong kịch bản (Master Prompt 08 · mục 9)</b><ul>${issues.map(i=>`<li>${esc(i)}</li>`).join('')}</ul>`:'');
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
// Viết / kiểm duyệt lần lượt từng nhóm 10: nhóm nào xong thì hiện ngay vào ô kết quả (dừng giữa chừng vẫn giữ phần đã có).
function applyPartial(engine,job,web){
  const p=web.partial;if(!p||!Array.isArray(p.english)||p.done===job.partialDone)return;
  job.partialDone=p.done;
  const [textKey]=englishKey(engine);
  if(job.kind==='english'){
    content[textKey]=p.english.join('\n');saveContent();
    if(content.englishEngine===engine)$('englishOutput').value=content[textKey];
    renderEnglish();
  }else if(job.kind==='promptreview'&&reviewOne===null){
    const lines=(content[textKey]||'').split('\n');p.english.forEach((line,i)=>{if(line)lines[i]=line;});
    content[textKey]=lines.join('\n');saveContent();
    if(content.englishEngine===engine)$('englishOutput').value=content[textKey];
  }else return;
  $(job.kind==='english'?'englishStatus':'reviewStatus')&&($(job.kind==='english'?'englishStatus':'reviewStatus').textContent=`Đã có ${p.done}/${p.total} prompt · đang làm nhóm tiếp theo…`);
}
function finishEnglish(engine,result){
  const [textKey,missingKey]=englishKey(engine);
  content[textKey]=result.english.join('\n');content[missingKey]=result.missing;content[textKey+'Issues']=result.issues||[];content[textKey+'Sources']=result.lines||splitSceneLines(content.englishInput);saveContent();
  if(content.englishEngine===engine)renderEnglishEngine();
  const name=engineNames[engine];
  if(!pipeline&&result.english.some(line=>String(line||'').trim()))setTimeout(()=>sendEnglishToStudio(true),1500);
  toast(result.missing.length?`${name} viết ${result.total-result.missing.length}/${result.total} prompt; còn thiếu cảnh ${result.missing.join(', ')}. Bấm chạy lại hoặc tự sửa.`:`${name}: đủ ${result.total} prompt tiếng Anh (${result.rounds} lượt trả lời).`,Boolean(result.missing.length));
}
function pollChat(){
  const web=state?.content?.chatgpt;
  if(chatJob&&web&&web.id===chatJob.id&&web.busy)applyPartial('chatgpt',chatJob,web);
  if(!chatJob||!web||web.id!==chatJob.id||web.busy){renderEnglishRun();return;}
  const job=chatJob;chatJob=null;renderEnglishRun();
  if(job.kind==='characters')return finishCharacters('chatgpt',job.id,web);
  if(web.error){lastJobError=web.error;if(typeof reviewOne!=='undefined')reviewOne=null;return toast(web.error,true);}
  if(!web.result){lastJobError='Không có kết quả.';return;}
  if(job.kind==='promptreview')return finishReview('chatgpt',web.result);
  finishEnglish('chatgpt',web.result);
}
setInterval(pollChat,1000);
document.querySelectorAll('[data-english-engine]').forEach(b=>b.onclick=()=>{content.englishEngine=b.dataset.englishEngine;saveContent();renderEnglishEngine();});
$('englishFromScenes').onclick=()=>{const prompts=contentPrompts();if(!prompts.length)return toast('Chưa có danh sách cảnh ở phía trên.',true);$('englishInput').value=prompts.join('\n');content.englishInput=$('englishInput').value;saveContent();};
$('englishInput').oninput=()=>{content.englishInput=$('englishInput').value;saveContent();};
$('englishStyle').oninput=()=>{content.englishStyle=$('englishStyle').value;saveContent();};
$('englishAspect').onchange=()=>{content.englishAspect=$('englishAspect').value;saveContent();};
$('englishSeconds').onchange=()=>{content.englishSeconds=Number($('englishSeconds').value);saveContent();};
$('englishAngles').onchange=()=>{content.englishAngles=$('englishAngles').value;try{localStorage.setItem('hana-english-angles',String(content.englishAngles));}catch{}};
try{const saved=localStorage.getItem('hana-english-angles');content.englishAngles=['lib','0','3'].includes(saved)?saved:'lib';}catch{content.englishAngles='lib';}
$('englishStructured').onchange=()=>{content.englishStructured=$('englishStructured').checked;saveContent();};
$('englishFormat').value=content.englishFormat||'detailed';
$('englishFormat').onchange=()=>{content.englishFormat=$('englishFormat').value;saveContent();};
$('englishOutput').oninput=()=>{content[englishKey(content.englishEngine)[0]]=$('englishOutput').value;saveContent();renderEnglish();renderReviewList();};
$('chatgptModel').onchange=()=>api('/api/content/chatgpt-settings',{model:$('chatgptModel').value.trim()}).then(refresh).catch(error=>toast(error.message,true));
$('loginChatgpt').onclick=async()=>{try{await api('/api/content/web-login',{engine:'chatgpt'});await refresh();toast('Đã mở Chrome. Đăng nhập ChatGPT, thấy ô nhắn tin thì đóng hẳn cửa sổ đó rồi bấm Viết prompt.');}catch(error){toast(error.message,true);}};
$('runEnglish').onclick=()=>startEnglish(false);
async function startEnglish(silent){
  const engine=content.englishEngine,[textKey]=englishKey(engine);
  if(engine==='gemini'?autoJob:chatJob)return false;
  if(!$('englishInput').value.trim()&&contentPrompts().length)$('englishFromScenes').click();
  if(!$('englishInput').value.trim()){toast('Dán danh sách “Bối cảnh 1: …” hoặc bấm Lấy từ danh sách cảnh.',true);return false;}
  if(!silent&&(content[textKey]||'').trim()&&!confirm(`Viết lại sẽ thay kết quả ${engineNames[engine]} hiện tại. Tiếp tục?`))return false;
  try{
    if(engine==='gemini')await saveAutoSettings();
    else await api('/api/content/chatgpt-settings',{model:$('chatgptModel').value.trim()});
    const {id,total}=await api('/api/content/english-run',{engine,text:$('englishInput').value,style:$('englishStyle').value,aspect:$('englishAspect').value,structured:$('englishStructured').checked,format:$('englishFormat').value,angles:$('englishAngles').value==='lib'?0:Number($('englishAngles').value),library:$('englishAngles').value==='lib',seconds:Number($('englishSeconds').value),windows:(content.windows||[]).length?content.windows:undefined});
    lastJobError='';
    if(engine==='gemini'){autoJob={id,kind:'english'};renderAuto();}else{chatJob={id};renderEnglishRun();}
    refresh();toast(`Đã gửi ${total} cảnh cho ${engineNames[engine]}.`);return true;
  }catch(error){toast(error.message,true);return false;}
}
// ---- Kiểm duyệt prompt: quy tắc + khóa (lưu trên máy, người dùng cập nhật dần) ----
let reviewLoaded=false,reviewTimer=null;
function loadReviewRules(){const r=state?.content?.review;if(!r)return;$('cameraCount').textContent=Number(r.camera_count||0).toLocaleString('vi-VN');if(reviewLoaded)return;reviewLoaded=true;$('reviewRules').value=r.rules||'';$('reviewLock').value=r.lock||'';$('cameraCustom').value=r.camera_custom||'';renderReviewIssues();}
let cameraTimer=null;$('cameraCustom').oninput=()=>{clearTimeout(cameraTimer);cameraTimer=setTimeout(()=>api('/api/content/camera-custom',{text:$('cameraCustom').value}).then(r=>{$('cameraCount').textContent=Number(r.count).toLocaleString('vi-VN');}).catch(error=>toast(error.message,true)),700);};
function saveReviewRules(){clearTimeout(reviewTimer);reviewTimer=setTimeout(()=>api('/api/content/review-rules',{rules:$('reviewRules').value,lock:$('reviewLock').value}).catch(error=>toast(error.message,true)),600);}
$('reviewRules').oninput=saveReviewRules;$('reviewLock').oninput=saveReviewRules;
$('resetReview').onclick=()=>{if(!confirm('Khôi phục bộ quy tắc và khóa mặc định?'))return;$('reviewRules').value='';$('reviewLock').value='';api('/api/content/review-rules',{rules:'',lock:''}).then(async()=>{reviewLoaded=false;await refresh();loadReviewRules();toast('Đã khôi phục mặc định.');}).catch(error=>toast(error.message,true));};
let reviewOne=null;  // số dòng đang tạo lại riêng (null = kiểm duyệt cả danh sách)
// Chia cảnh theo góc quay: mỗi prompt có thời lượng riêng (đúng số giây của góc máy) để kiểm duyệt không sửa sai DURATION.
function reviewDurations(line){
  const w=content.windows||[],count=$('englishOutput').value.split('\n').filter(l=>l.trim()).length;
  if(!w.length||!w.every(x=>x.shot)||w.length!==count)return undefined;
  const all=w.map(x=>Math.max(1,Math.round(x.end-x.start)));return line===null?all:[all[line-1]];
}
async function startReview(line=null){
  const engine=content.englishEngine;
  if(engine==='gemini'?autoJob:chatJob)return false;
  const lines=$('englishOutput').value.split('\n'),text=line===null?$('englishOutput').value.trim():(lines[line-1]||'').trim();
  if(!text){toast('Chưa có prompt tiếng Anh để kiểm duyệt.',true);return false;}
  reviewOne=line;
  try{
    const {id,total}=await api('/api/content/review-prompts',{engine,text,rules:$('reviewRules').value,lock:$('reviewLock').value,seconds:content.englishSeconds,durations:reviewDurations(line)});
    lastJobError='';
    if(engine==='gemini'){autoJob={id,kind:'promptreview'};renderAuto();}else{chatJob={id,kind:'promptreview'};renderEnglishRun();}
    $('reviewStatus').textContent=`${engineNames[engine]} đang kiểm duyệt ${total} prompt…`;renderReviewList();refresh();return true;
  }catch(error){toast(error.message,true);return false;}
}
$('runReview').onclick=()=>startReview();
function finishReview(engine,result){
  const [textKey]=englishKey(engine);
  if(reviewOne!==null){  // tạo lại riêng một prompt: chỉ thay đúng dòng đó
    const line=reviewOne,lines=(content[textKey]||'').split('\n');reviewOne=null;
    lines[line-1]=result.english[0]||lines[line-1];content[textKey]=lines.join('\n');
    content.reviewViolations={...(content.reviewViolations||{})};
    if(result.violations&&result.violations[1])content.reviewViolations[line]=result.violations[1];else delete content.reviewViolations[line];
    saveContent();if(content.englishEngine===engine)renderEnglishEngine();renderReviewIssues();
    const left=content.reviewViolations[line];
    $('reviewStatus').textContent=left?`Prompt ${line} vẫn còn lỗi: ${left.join(', ')}`:`Đã tạo lại prompt ${line} · đạt quy tắc`;
    return toast(left?`Prompt ${line} vẫn còn lỗi, thử tạo lại lần nữa hoặc sửa tay.`:`Đã tạo lại prompt ${line}.`,Boolean(left));
  }
  content[textKey]=result.english.join('\n');content.reviewIssues=result.issues||[];content.reviewViolations=result.violations||{};content.reviewed=true;saveContent();
  if(content.englishEngine===engine)renderEnglishEngine();
  renderReviewIssues();
  const left=Object.keys(result.violations||{}).length;
  $('reviewStatus').textContent=`Đã kiểm duyệt ${result.total} prompt${left?` · ${left} prompt cần xem lại`:' · đạt hết quy tắc'} · đã gắn khóa`;
  toast(left?`Kiểm duyệt xong: ${left} prompt còn vi phạm, xem danh sách bên dưới.`:`Kiểm duyệt xong ${result.total} prompt, đã gắn khóa.`,Boolean(left));
}
function renderReviewList(){
  const lines=$('englishOutput').value.split('\n').filter(l=>l.trim()),violations=content.reviewViolations||{},busy=Boolean(autoJob||chatJob);
  $('reviewList').hidden=!content.reviewed||!lines.length;
  if($('reviewList').hidden)return;
  $('reviewList').innerHTML=lines.map((line,i)=>{const n=i+1,bad=violations[n];return `<div class="review-item ${bad?'bad':'ok'}"><div class="review-item-head"><b>Prompt ${n}</b><span>${bad?`⚠ ${esc(bad.join(', '))}`:'✓ Đạt quy tắc'}</span><button type="button" data-regen="${n}" ${busy?'disabled':''} title="Tạo lại riêng prompt này theo quy tắc kiểm duyệt">↻ Tạo lại</button></div><p>${esc(line.length>260?line.slice(0,260)+'…':line)}</p></div>`;}).join('');
}
$('reviewList').addEventListener('click',e=>{const b=e.target.closest('[data-regen]');if(!b)return;const n=Number(b.dataset.regen);startReview(n).then(ok=>{if(ok)$('reviewStatus').textContent=`Đang tạo lại prompt ${n}…`;});});
function renderReviewIssues(){
  renderReviewList();
  const issues=content.reviewIssues||[],violations=Object.entries(content.reviewViolations||{});
  $('reviewIssues').hidden=!issues.length&&!violations.length;
  $('reviewIssues').innerHTML=(violations.length?`<b>⚠ Cần kiểm tra — AI đã tự sửa tối đa 3 lần vẫn còn lỗi (bấm ↻ Tạo lại hoặc sửa tay)</b><ul>${violations.map(([n,v])=>`<li>Prompt ${esc(n)}: ${esc(v.join(', '))}</li>`).join('')}</ul>`:'')+
    (issues.length?`<b>AI đã sửa</b><ul>${issues.slice(0,40).map(i=>`<li>${esc(i)}</li>`).join('')}</ul>`:'');
}
// ---- Tự động: link → tải video → Gemini phân tích → prompt tiếng Anh → kiểm duyệt ----
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function pipeStep(name,stateName){const li=document.querySelector(`#pipelineSteps [data-pipe="${name}"]`);if(li){li.classList.remove('active','done','failed');if(stateName)li.classList.add(stateName);}}
async function waitJobs(){while(autoJob||chatJob){await sleep(1000);}if(lastJobError)throw Error(lastJobError);}
async function runPipelineFlow(){
  if(pipeline)return;
  const link=$('contentVideo').value.trim();
  if(!link&&!content.videoFile)return toast('Dán link video YouTube (hoặc chọn file video) trước.',true);
  if(!content.brief.trim()){content.brief=defaultBrief;$('contentBrief').value=defaultBrief;saveContent();}
  pipeline={step:''};$('pipelineSteps').hidden=false;['download','cut','script','prompt','review'].forEach(s=>pipeStep(s));renderAuto();
  let current='download';
  try{
    pipeStep('download','active');
    if(link&&(!content.videoFile||content.videoFrom!==link)){
      content.video=link;saveContent();
      try{
        await api('/api/production/download',{url:link});
        let st;do{await sleep(1500);st=await (await fetch('/api/production/download')).json();$('contentVideoFile').textContent=st.status||'Đang tải…';}while(st.busy);
        if(st.error||!st.path)throw Error(st.error||'Không tải được video.');
        content.videoFile=st.path;content.videoFrom=link;saveContent();renderVideoFile();
      }catch(error){
        // Không tải được (YouTube chặn, video riêng tư…): link YouTube thì Gemini vẫn xem qua link, chỉ thiếu phần FFmpeg.
        if(!/youtu\.?be/i.test(link))throw error;
        content.videoFile='';content.videoFrom='';saveContent();renderVideoFile();
        toast(`Không tải được video (${error.message}). Gemini sẽ xem qua link YouTube; bước FFmpeg (điểm cắt, khung hình) bị bỏ qua.`,true);
      }
    }
    pipeStep('download','done');current='cut';pipeStep('cut','active');
    // Bước FFmpeg chạy trước khi mở Gemini (trong cùng tác vụ): xong khi tác vụ chuyển sang giai đoạn "gemini".
    if(!await runAuto('generate',true))throw Error('Không chạy được Gemini.');
    while(autoJob&&current==='cut'){await sleep(700);if([state?.content?.web,state?.content?.chatgpt].some(w=>w?.busy&&w.phase==='gemini')){pipeStep('cut','done');current='script';pipeStep('script','active');}}
    await waitJobs();
    if(current==='cut'){pipeStep('cut','done');current='script';pipeStep('script','active');}
    if(!content.scenes.length)throw Error('Gemini không trả về cảnh nào.');
    pipeStep('script','done');current='prompt';pipeStep('prompt','active');
    $('englishInput').value=contentPrompts().join('\n');content.englishInput=$('englishInput').value;saveContent();
    if(!await startEnglish(true))throw Error('Không chạy được bước viết prompt.');
    await waitJobs();
    pipeStep('prompt','done');current='review';pipeStep('review','active');
    if(!await startReview())throw Error('Không chạy được bước kiểm duyệt.');
    await waitJobs();
    pipeStep('review','done');setStep(4);
    await sendEnglishToStudio(true);
  }catch(error){pipeStep(current,'failed');toast(`Dừng ở bước “${document.querySelector(`#pipelineSteps [data-pipe="${current}"]`).textContent}”: ${error.message}`,true);}
  finally{pipeline=null;renderAuto();}
}
$('runPipeline').onclick=runPipelineFlow;
// Dán link là tự chạy (bật/tắt bằng ô "Tự chạy ngay khi dán link", nhớ trên máy).
try{$('autoStart').checked=localStorage.getItem('hana-auto-start')!=='0';}catch{}
$('autoStart').onchange=()=>{try{localStorage.setItem('hana-auto-start',$('autoStart').checked?'1':'0');}catch{}};
function autoStartFromLink(){
  const link=$('contentVideo').value.trim();
  if(!$('autoStart').checked||!/^https?:\/\/\S+$/i.test(link)||pipeline||autoJob||chatJob)return;
  content.video=link;saveContent();toast('Đã nhận link · tool tự chạy: tải video → phân tích → viết prompt → kiểm duyệt.');runPipelineFlow();
}
$('contentVideo').addEventListener('paste',()=>setTimeout(autoStartFromLink,300));
$('contentVideo').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();autoStartFromLink();}});
$('takeEnglish').onclick=()=>api('/api/content/web-take',{engine:content.englishEngine}).then(()=>$('englishStatus').textContent='Đang đọc câu trả lời trên trang…').catch(error=>toast(error.message,true));
$('stopEnglish').onclick=()=>api('/api/content/web-stop',{engine:content.englishEngine}).then(()=>$('englishStatus').textContent='Đang dừng…').catch(error=>toast(error.message,true));
// Tách đầu vào giống máy chủ (content.split_scene_lines): mỗi dòng một cảnh, hoặc "[...; Bối cảnh 2: ...]".
function splitSceneLines(text){
  const raw=String(text||'').replace(/\r/g,'').trim().replace(/^\s*(input\s*:)?\s*\[/i,'').replace(/\]\s*$/,'')
    .replace(/;\s*(?=b[ốo]i\s*c[ảa]nh\s*\d)/gi,';\n').split('\n').map(line=>line.trim());
  const header=/^\W*(?:shot|c[ảa]nh|scene)\s*\d{1,3}\s*[—–:\-.]/i;
  if(raw.some(line=>header.test(line))){ // khối "SHOT N — …" nhiều dòng (phân tích theo shot) = một cảnh
    const blocks=[];for(const line of raw){if(header.test(line)||!blocks.length)blocks.push([]);if(line)blocks[blocks.length-1].push(line.replace(/^[*•\-–]\s*/,''));}
    return blocks.filter(b=>b.length).map(b=>b.join(' | ').replace(/\s+/g,' ').trim());
  }
  return raw.map(line=>line.replace(/\s+/g,' ').trim()).filter(Boolean);
}
// Prompt tiếng Anh số N đi kèm dòng cảnh tiếng Việt số N, để Prompt Studio có đủ Bối cảnh / Góc quay / Hành vi / Hành động.
let promptSources={},promptScenes={};
try{promptSources=JSON.parse(localStorage.getItem('hana-prompt-sources')||'{}')||{};promptScenes=JSON.parse(localStorage.getItem('hana-prompt-scenes')||'{}')||{};}catch{}
function rememberSources(pairs){promptSources={};promptScenes={};for(const [prompt,source,scene] of pairs){if(source)promptSources[prompt]=source;if(scene)promptScenes[prompt]=scene;}try{localStorage.setItem('hana-prompt-sources',JSON.stringify(promptSources));localStorage.setItem('hana-prompt-scenes',JSON.stringify(promptScenes));}catch{}}
$('useEnglish').onclick=()=>sendEnglishToStudio(false);
// Viết prompt xong là tự đưa vào hàng đợi Studio (auto); bấm nút thì đưa lại theo ý người dùng.
// Không đưa trùng: bộ prompt đã đưa tự động rồi thì lần tự động sau bỏ qua.
async function sendEnglishToStudio(auto){
  const [textKey]=englishKey(content.englishEngine);
  const sources=content[textKey+'Sources']||splitSceneLines(content.englishInput);
  const lines=englishLines(),sceneIds=project&&project.scenes.length===lines.filter(l=>l.trim()).length?project.scenes.map(x=>x.id):[];
  let n=0;const pairs=lines.map((line,index)=>[line.trim(),sources[index]||'',line.trim()?sceneIds[n++]||'':'']).filter(([line])=>line);
  const prompts=pairs.map(([line])=>line);if(!prompts.length)return toast('Chưa có prompt tiếng Anh.',true);
  if(prompts.length>200)return toast('Mỗi lần đưa tối đa 200 prompt; chia làm nhiều lần.',true);
  const tooLong=prompts.findIndex(p=>p.length>20000);if(tooLong>=0)return toast(`Prompt số ${tooLong+1} dài quá 20.000 ký tự.`,true);
  const signature=prompts.join('\n');
  if(auto&&content.sentToStudio===signature)return;
  rememberSources(pairs);
  // Đưa thẳng từng prompt vào hàng đợi Studio (không qua khung nhập), kèm ảnh nhân vật đã chọn và độ phân giải.
  // Thời lượng để trống: mỗi prompt đã ghi đúng số giây của cảnh (Generate a N-second…), không ép một số chung.
  const options={...videoOptions(),duration:0,resolution:$('contentResolution').value,aspect:content.englishAspect||$('vAspect').value};
  const cost=Number($('cost').value)||1,button=$('useEnglish');button.disabled=true;
  try{
    await api('/api/jobs',{prompts,cost,mode:'text',batch:crypto.randomUUID(),images:prompts.map(()=>[]),
      sources:pairs.map(([,source])=>source),scene_ids:pairs.map(([,,scene])=>scene||null),options});
    content.sentToStudio=signature;saveContent();
    await refresh();showView('studio');
    toast(`${auto?'Viết prompt xong · đã tự ':'Đã '}thêm ${prompts.length} prompt vào hàng đợi Studio${fixedCharacters.length?` · kèm ${fixedCharacters.length} ảnh nhân vật`:''} · ${options.resolution||'độ phân giải của Muse'}. Bấm ▶ Chạy để tạo video.`);
  }catch(error){toast((auto?'Chưa tự đưa được vào Studio: ':'')+error.message,true);}
  finally{button.disabled=false;}
}
$('copyEnglish').onclick=()=>navigator.clipboard.writeText($('englishOutput').value).then(()=>toast('Đã chép prompt tiếng Anh.')).catch(()=>toast('Không chép được. Chọn và sao chép trong ô kết quả.',true));
$('downloadEnglish').onclick=()=>{const blob=new Blob([$('englishOutput').value],{type:'text/plain;charset=utf-8'});const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download=`prompt-tieng-anh-${content.englishEngine}.txt`;link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);};

// ---- Prompt Studio: bấm vào một prompt trong hàng đợi để xem và sửa chi tiết ----
let studioJob=null,studioEditing=false,studioImages=[],studioMode='text';
const statusLabel={waiting:'Chờ',running:'Đang chạy',done:'Xong',error:'Lỗi'};
function jobViewShown(){return !$('jobView').hidden;}
function setJobView(show){$('jobView').hidden=!show;if(show){$('productionView').hidden=true;$('studioView').hidden=true;$('communityView').hidden=true;$('licenseView').hidden=true;$('contentView').hidden=true;$('mediaView').hidden=true;document.querySelectorAll('[data-nav]').forEach(b=>b.classList.toggle('active',b.dataset.nav==='studio'));window.scrollTo(0,0);}}
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
  $('jobAspect').value=job.aspect||'';$('jobDuration').value=job.duration?String(job.duration):'';$('jobResolution').value=job.resolution||'';
  const canRerun=job.status==='done'||job.status==='error';
  $('jobStatus').innerHTML=`<option value="${job.status}">${statusLabel[job.status]}${job.status==='error'&&job.error?' – '+esc(job.error.slice(0,80)):''}</option>`+(canRerun?'<option value="rerun">Chờ (chạy lại prompt này)</option>':'');
  $('jobStatus').disabled=!canRerun;
  $('jobInfo').textContent=job.status==='running'?'Prompt đang chạy: chỉ xem, chưa sửa được.':job.status==='done'?'Đã có video. Sửa rồi bấm Chạy lại để tạo video mới; video cũ vẫn giữ trong thư mục lưu.':job.status==='error'?(job.submitted?'Lỗi sau khi đã gửi lên Muse: kiểm tra Muse trước khi chạy lại.':'Lỗi trước khi gửi: sửa rồi bấm Chạy lại.'):'Đang chờ chạy: sửa xong bấm Lưu.';
  $('jobNotice').hidden=true;renderJobImages();renderJobEditing();
}
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
function jobPayload(){return {id:studioJob.id,prompt:$('jobPrompt').value,details:{setting:$('jobSetting').value,behavior:$('jobBehavior').value,action:$('jobAction').value,camera:$('jobCamera').value},images:studioImages,mode:studioMode,account_id:$('jobAccount').value||null,video:{aspect:$('jobAspect').value,duration:Number($('jobDuration').value)||0,resolution:$('jobResolution').value}};}
function jobChanged(){if(!studioJob||!studioEditing)return false;const p=jobPayload(),j=studioJob;return p.prompt.trim()!==j.prompt||JSON.stringify(Object.values(p.details).map(v=>v.trim()))!==JSON.stringify([j.details.setting,j.details.behavior,j.details.action,j.details.camera])||JSON.stringify(p.images)!==JSON.stringify(j.images)||p.mode!==(j.generation_mode==='image'?'image':'text')||(p.account_id||'')!==(j.assigned_account_id&&state?.accounts?.some(a=>a.id===j.assigned_account_id)?j.assigned_account_id:'');}
async function saveJob(){
  if(!$('jobPrompt').value.trim()){$('jobNotice').innerHTML='<b>Prompt không được để trống.</b>';$('jobNotice').hidden=false;$('jobPrompt').focus();return false;}
  try{const {job}=await api('/api/jobs/update',jobPayload());studioJob=job;await refresh();return true;}
  catch(error){$('jobNotice').innerHTML=`<b>${esc(error.message)}</b>`;$('jobNotice').hidden=false;return false;}
}
$('rows').addEventListener('click',e=>{if(e.target.closest('button,a,input,video,.video-thumb'))return;const cell=e.target.closest('.prompt-cell');if(!cell)return;const card=cell.closest('[data-job]');if(card)openJob(card.dataset.job);});
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
  box.innerHTML=`<div class="picker-head"><b>Chọn ảnh Master</b><span class="bulk-spacer"></span><button type="button" data-picker-flow>⇩ Lấy ảnh mới tải từ Flow</button><label class="picker-upload">⇪ Tải ảnh lên<input type="file" accept="image/png,image/jpeg,image/webp" data-picker-upload hidden></label><button type="button" data-picker-close>✕</button></div>
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
if(currentStep===3)currentStep=4; // bước Nhân vật đã bỏ khỏi Content Studio
function stepDone(n){return n===1?Boolean(content.brief.trim()):n===2?content.scenes.length>0:n===3?Boolean(project?.characters.length):n===4?Boolean((content.english||content.englishChatgpt||'').trim()):false;}
function setStep(n,scroll=true){
  if(n===3)n=4;  // bước Nhân vật đã bỏ: chuyển sang bước Prompt
  currentStep=n;try{localStorage.setItem('hana-step',String(n));}catch{}
  document.querySelectorAll('[data-step-panel]').forEach(el=>el.hidden=Number(el.dataset.stepPanel)!==n);
  document.querySelectorAll('.stepper [data-step]').forEach(b=>{const k=Number(b.dataset.step);b.classList.toggle('active',k===n);b.classList.toggle('done',k!==n&&stepDone(k));});
  if(n===5)renderOverview();
  markSidebar();if(scroll)window.scrollTo({top:0,behavior:'smooth'});
}
function renderOverview(){
  const english=(content.english||'').split('\n').filter(x=>x.trim()).length,chat=(content.englishChatgpt||'').split('\n').filter(x=>x.trim()).length;
  const tiles=[['Cảnh',content.scenes.length,'bước 2'],['Prompt tiếng Anh · ChatGPT',chat,'bước 3']];
  $('overviewStats').innerHTML=tiles.map(([label,value,note])=>`<div class="overview-tile"><small>${esc(label)}</small><b>${esc(String(value))}</b><span>${esc(note)}</span></div>`).join('');
}
function markSidebar(){
  const production=!$('productionView').hidden,content=!$('contentView').hidden,settings=!$('settingsView').hidden,media=!$('mediaView').hidden,community=!$('communityView').hidden,licensePage=!$('licenseView').hidden;
  $('navProduction').classList.toggle('active',production);$('navStudio').classList.toggle('active',!production&&!content&&!settings&&!media&&!community&&!licensePage);$('navCommunity').classList.toggle('active',community);$('navLicense').classList.toggle('active',licensePage);$('openLibrary').classList.toggle('active',media);$('navSettings').classList.toggle('active',settings);$('openContent').classList.toggle('active',content);
}
document.querySelectorAll('.stepper [data-step]').forEach(b=>b.onclick=()=>setStep(Number(b.dataset.step)));
document.addEventListener('click',e=>{const go=e.target.closest('[data-goto-step]');if(go)setStep(Number(go.dataset.gotoStep));});
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
  renderApiCards(force);
  const shared=state.shared_account||{};
  if(force||document.activeElement!==$('sharedAccount'))$('sharedAccount').checked=Boolean(shared.enabled);
  $('sharedBadge').textContent=shared.enabled?(shared.login_open?'Cửa sổ đăng nhập đang mở':shared.profile_used?'✓ Đang bật':'Đang bật · chưa đăng nhập'):'Đang tắt';
  $('sharedBadge').className='status-chip '+(shared.enabled?(shared.profile_used?'approved':'review'):'');
  $('sharedLogin').hidden=$('sharedHint').hidden=!shared.enabled;$('sharedLogin').disabled=Boolean(shared.login_open);
  for(const id of ['loginAuto','loginChatgpt']){$(id).textContent=shared.enabled?'Đăng nhập bằng tài khoản chung (ở trên)':{loginAuto:'Đăng nhập Google · Gemini (mở Chrome thường)',loginChatgpt:'Đăng nhập ChatGPT (mở Chrome thường)'}[id];}
  const seen=web.account?.logged_in?(web.account.email||'đã đăng nhập'):'';
  $('geminiAccountLine').innerHTML=`Tài khoản Gemini: <b>${esc(email||seen||'chưa đăng nhập')}</b>${model?` · model ${esc(model)}`:''} · <button type="button" class="text-button" data-open-settings>⚙ Đổi trong Cài đặt</button>`;
  if(force||document.activeElement!==$('chatgptModel'))$('chatgptModel').value=chat.model||'';
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
setInterval(()=>{renderSettings(false);},1500);
$('sharedAccount').onchange=()=>{const enabled=$('sharedAccount').checked;
  if(enabled&&!confirm('Dùng một tài khoản Google cho Gemini, ChatGPT và Google Flow?\n\nTool sẽ đóng các cửa sổ Chrome đang rảnh và dùng một hồ sơ chung. Lần đầu cần đăng nhập lại một lần.')){$('sharedAccount').checked=false;return;}
  action(async()=>{try{await api('/api/settings/shared-account',{enabled});toast(enabled?'Đã bật tài khoản chung. Bấm “Đăng nhập tài khoản chung”.':'Đã tắt tài khoản chung: mỗi phần dùng lại hồ sơ riêng.');}catch(error){$('sharedAccount').checked=!enabled;throw error;}renderSettings(true);});};
$('sharedLogin').onclick=()=>action(async()=>{await api('/api/shared-login',{});$('sharedLogin').blur();toast('Đã mở Chrome thường với 3 tab: Google, ChatGPT, Flow. Đăng nhập xong thì đóng hẳn cửa sổ đó.');});
initProduction({api,toast,showView,openJob,key:()=>key});
renderMode();await refresh();if(state?.content?.web?.busy&&state.content.web.kind!=='production')autoJob={id:state.content.web.id,kind:state.content.web.kind||'generate'};if(state?.content?.chatgpt?.busy&&state.content.chatgpt.kind!=='production')chatJob={id:state.content.chatgpt.id,kind:state.content.chatgpt.kind};for(const [engine,web] of [['gemini',state?.content?.web],['chatgpt',state?.content?.chatgpt]])if(web?.busy&&web.kind==='characters')charJob={id:web.id,engine};if(location.hash==='#production')showView('production');else if(location.hash==='#content')showView('content');else if(location.hash==='#media')showView('media');else if(location.hash==='#community')showView('community');else if(location.hash==='#license')showView('license');else if(location.hash==='#settings')showView('settings');{const m=location.hash.match(/^#job=([a-f0-9]{32})$/);if(m)openJob(m[1]);}setInterval(refresh,2000);

document.addEventListener('click',e=>{const b=e.target.closest('[data-review],[data-close-review]');if(!b)return;action(async()=>{await api(b.dataset.review?'/api/review':'/api/review-close',{id:b.dataset.review||b.dataset.closeReview});b.blur();toast(b.dataset.review?'Đang mở đúng phiên tài khoản. Đóng phiên trong mục Tài khoản khi kiểm tra xong.':'Đang đóng phiên kiểm tra.');});});

$('operation').onchange=()=>{const manual=$('operation').value==='assisted';$('headless').disabled=manual;if(manual)$('headless').checked=false;};
document.addEventListener('click',e=>{const b=e.target.closest('[data-copy]');if(!b)return;const job=state.jobs.find(j=>j.id===b.dataset.copy);if(!job)return;const request='Tạo video theo yêu cầu sau và trả về file video để tải xuống:\n\n'+job.prompt+'\n\nMã tác vụ: '+job.id;navigator.clipboard.writeText(request).then(()=>toast('Đã chép prompt. Dán vào đúng cửa sổ tác vụ đang chạy.')).catch(()=>toast('Không chép được. Chọn và sao chép prompt trong bảng.',true));});

document.addEventListener('click',e=>{const b=e.target.closest('[data-play]');if(!b)return;const job=state.jobs.find(j=>j.id===b.dataset.play);if(!job)return;$('videoPlayer').src='/api/media/'+job.id;$('playerPrompt').textContent=job.prompt;$('playerTitle').textContent='Video cảnh '+job.id.slice(0,12);$('player').showModal();});$('closePlayer').onclick=()=>$('player').close();$('player').onclose=()=>{$('videoPlayer').pause();$('videoPlayer').removeAttribute('src');$('videoPlayer').load();};

// ===== Thư viện Video & Ảnh (Media Library) =====
async function mediaRequest(method,path,body){
  const response=await fetch(path,{method,headers:{'Content-Type':'application/json','X-Runner-Key':key},body:JSON.stringify(body||{})});
  const data=await response.json().catch(()=>({}));
  if(!response.ok){const error=Error(data.error||'Yêu cầu thất bại.');error.data=data;error.status=response.status;throw error;}
  return data;
}
function sendMedia(file,onProgress,category){
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();xhr.open('POST','/api/media/upload');
    xhr.setRequestHeader('X-Runner-Key',key);xhr.setRequestHeader('X-Image-Name',encodeURIComponent(file.name));
    if(category)xhr.setRequestHeader('X-Media-Category',category);
    xhr.setRequestHeader('Content-Type',file.type||'application/octet-stream');
    xhr.upload.onprogress=e=>{if(e.lengthComputable&&onProgress)onProgress(e.loaded/e.total);};
    xhr.onload=()=>{let data={};try{data=JSON.parse(xhr.responseText);}catch{}if(xhr.status<300)resolve(data);else reject(Error(data.error||`Tải lên thất bại (mã ${xhr.status}).`));};
    xhr.onerror=()=>reject(Error('Mất kết nối bộ chạy khi tải lên.'));
    xhr.send(file);
  });
}
function mediaFileProblem(file){
  const ext=(file.name.split('.').pop()||'').toLowerCase();
  if(!['jpg','jpeg','png','webp'].includes(ext))return 'Chỉ nhận JPG, JPEG, PNG, WEBP.';
  if(!file.size)return 'File trống.';
  if(file.size>mediaLimits.max_file_mb*1048576)return `Vượt quá ${mediaLimits.max_file_mb} MB.`;
  return '';
}
async function uploadMediaFiles(list,box){
  let files=[...list];if(!files.length)return [];
  if(files.length>mediaLimits.max_batch){toast(`Mỗi lần tải tối đa ${mediaLimits.max_batch} ảnh; đã lấy ${mediaLimits.max_batch} ảnh đầu.`,true);files=files.slice(0,mediaLimits.max_batch);}
  box.hidden=false;
  box.innerHTML=`<div class="uploads-head"><b>Tải lên ${files.length} ảnh</b><button type="button" data-uploads-close aria-label="Ẩn danh sách tải lên">✕</button></div>`+files.map((file,i)=>`<div class="upload-row" data-upload="${i}"><span class="upload-name" title="${esc(file.name)}">${esc(file.name)}</span><progress max="1" value="0"></progress><span class="upload-state">Đang chờ</span></div>`).join('');
  const results=[];let next=0,ok=0,dup=0,failed=0;
  const mark=(i,cls,text,value)=>{const row=box.querySelector(`[data-upload="${i}"]`);if(!row)return;row.className=`upload-row ${cls}`;row.querySelector('.upload-state').textContent=text;if(value!==undefined)row.querySelector('progress').value=value;};
  async function worker(){
    while(next<files.length){
      const i=next++,file=files[i],problem=mediaFileProblem(file);
      if(problem){failed++;mark(i,'fail',problem,0);continue;}
      mark(i,'busy','Đang tải…');
      try{
        const data=await sendMedia(file,v=>{const row=box.querySelector(`[data-upload="${i}"] progress`);if(row)row.value=v;});
        results.push(data.item);data.duplicate?dup++:ok++;
        mark(i,data.duplicate?'dup':'ok',data.duplicate?'Đã có trong thư viện (trùng nội dung)':'Đã tải lên',1);
      }catch(error){failed++;mark(i,'fail',error.message,0);}
    }
  }
  await Promise.all(Array.from({length:Math.min(3,files.length)},worker));
  toast(`Tải lên: ${ok} ảnh mới${dup?`, ${dup} ảnh trùng (dùng ảnh đã có)`:''}${failed?`, ${failed} ảnh lỗi`:''}.`,failed>0&&!ok&&!dup);
  return results;
}
document.addEventListener('click',e=>{const b=e.target.closest('[data-uploads-close]');if(b){const box=b.closest('.media-uploads');box.hidden=true;box.innerHTML='';}});
async function fetchMedia(params){
  const response=await fetch('/api/media?'+new URLSearchParams(params));
  const data=await response.json().catch(()=>({}));
  if(!response.ok)throw Error(data.error||'Không tải được thư viện.');
  mediaLimits=data.limits||mediaLimits;
  return data;
}
async function loadMedia(){
  try{
    const params={};if(mediaType)params.type=mediaType;if(mediaCategory)params.category=mediaCategory;
    const q=$('mediaSearch').value.trim();if(q)params.q=q;
    const data=await fetchMedia(params);
    mediaItems=data.items;$('mediaTotal').textContent=data.totals.all;
    $('mediaLimits').textContent=`tối đa ${mediaLimits.max_file_mb} MB mỗi ảnh · ${mediaLimits.max_batch} ảnh mỗi lần`;
    for(const id of [...mediaSelected])if(!mediaItems.some(item=>item.id===id))mediaSelected.delete(id);
    renderMedia();
  }catch(error){toast(error.message,true);}
}
function renderMedia(){
  const grid=$('mediaGrid');
  grid.innerHTML=mediaItems.length?mediaItems.map((item,index)=>{
    const image=item.media_type==='image',used=item.used_by_jobs+item.used_by_characters;
    return `<article class="media-card${mediaSelected.has(item.id)?' selected':''}" data-media="${item.id}">
<label class="media-check" title="Chọn"><input type="checkbox" data-media-select="${item.id}" ${mediaSelected.has(item.id)?'checked':''} aria-label="Chọn ${esc(item.display_name)}"></label>
<button type="button" class="media-thumb" data-media-view="${index}" aria-label="Xem ${esc(item.display_name)}">${!item.available?'<span class="media-missing">File không còn trên máy</span>':image?`<img loading="lazy" src="/api/media/item/${item.id}/thumbnail" alt="">`:`<video muted preload="none" data-video-src="/api/media/item/${item.id}/file#t=0.5"></video><span class="media-play">▶</span>`}</button>
<div class="media-meta"><strong title="${esc(item.display_name)}">${esc(item.display_name)}</strong><span>${esc(mediaSize(item))} · ${formatBytes(item.file_size)}</span><span>${formatDate(item.created_at)}</span>
<span class="media-tags"><span class="badge">${image?'Ảnh':'Video'}</span><span class="badge">${esc(categoryNames[item.category]||item.category)}</span><span class="badge">${esc(sourceNames[item.source_type]||'')}</span>${item.running?'<span class="badge running">Đang chạy</span>':used?`<span class="badge done" title="Đang được prompt/nhân vật dùng">Đang dùng · ${used}</span>`:''}</span></div>
<div class="media-actions"><button type="button" data-media-view="${index}">Xem</button><button type="button" data-media-rename="${item.id}">Đổi tên</button><a class="button-link" href="/api/media/item/${item.id}/download" download>Tải</a><button type="button" class="danger-outline" data-media-delete="${item.id}">Xóa</button></div></article>`;
  }).join(''):`<div class="empty media-empty"><span>▧</span><h3>${$('mediaSearch').value||mediaType||mediaCategory?'Không có media khớp bộ lọc':'Thư viện còn trống'}</h3><p>Bấm “+ Tải ảnh lên” hoặc kéo thả ảnh vào khung phía trên. Video Muse đã tải sẽ tự hiện ở đây.</p></div>`;
  grid.querySelectorAll('video[data-video-src]').forEach(video=>thumbnailObserver?thumbnailObserver.observe(video):(video.src=video.dataset.videoSrc));
  $('mediaSelection').hidden=!mediaSelected.size;$('mediaSelectedCount').textContent=mediaSelected.size;
}
async function deleteMedia(ids){
  if(!ids.length)return false;
  if(!confirm(ids.length>1?`Xóa ${ids.length} media khỏi thư viện?`:'Xóa media này khỏi thư viện?'))return false;
  let result;
  try{result=await mediaRequest('POST','/api/media/delete-batch',{ids});}
  catch(error){
    if(!error.data?.needs_confirm)throw error;
    const deps=Object.values(error.data.dependencies||{});
    const jobs=new Set(deps.flatMap(d=>d.jobs.map(j=>j.name))),chars=new Set(deps.flatMap(d=>d.characters.map(c=>c.name)));
    const detail=[jobs.size?`Prompt: ${[...jobs].slice(0,8).join(', ')}${jobs.size>8?'…':''}`:'',chars.size?`Nhân vật: ${[...chars].slice(0,8).join(', ')}`:''].filter(Boolean).join('\n');
    if(!confirm(`${error.message}\n\n${detail}\n\nVẫn bỏ khỏi thư viện?`))return false;
    result=await mediaRequest('POST','/api/media/delete-batch',{ids,confirmed:true});
  }
  ids.forEach(id=>mediaSelected.delete(id));
  toast(`Đã xóa ${result.deleted} media${result.hidden?`; ${result.hidden} media chỉ được ẩn (file được giữ cho prompt/nhân vật hoặc là video kết quả)`:''}.`);
  return true;
}
async function renameMedia(id){
  const item=mediaItems.find(i=>i.id===id);if(!item)return;
  const name=prompt('Tên hiển thị mới:',item.display_name);
  if(name===null||!name.trim()||name.trim()===item.display_name)return;
  try{await mediaRequest('PATCH',`/api/media/item/${id}`,{display_name:name.trim()});toast('Đã đổi tên.');await loadMedia();}catch(error){toast(error.message,true);}
}
$('mediaGrid').onclick=e=>{
  const view=e.target.closest('[data-media-view]'),rename=e.target.closest('[data-media-rename]'),remove=e.target.closest('[data-media-delete]');
  if(view)openPreview(Number(view.dataset.mediaView));
  if(rename)renameMedia(rename.dataset.mediaRename);
  if(remove)deleteMedia([remove.dataset.mediaDelete]).then(done=>done&&loadMedia()).catch(error=>toast(error.message,true));
};
$('mediaGrid').onchange=e=>{const box=e.target.closest('[data-media-select]');if(!box)return;box.checked?mediaSelected.add(box.dataset.mediaSelect):mediaSelected.delete(box.dataset.mediaSelect);box.closest('.media-card').classList.toggle('selected',box.checked);$('mediaSelection').hidden=!mediaSelected.size;$('mediaSelectedCount').textContent=mediaSelected.size;};
$('mediaSelectAll').onclick=()=>{mediaItems.forEach(i=>mediaSelected.add(i.id));renderMedia();};
$('mediaClearSelection').onclick=()=>{mediaSelected.clear();renderMedia();};
$('mediaDeleteSelected').onclick=()=>deleteMedia([...mediaSelected]).then(done=>done&&loadMedia()).catch(error=>toast(error.message,true));
document.querySelectorAll('[data-media-type]').forEach(b=>b.onclick=()=>{mediaType=b.dataset.mediaType;document.querySelectorAll('[data-media-type]').forEach(t=>t.classList.toggle('active',t===b));loadMedia();});
document.querySelectorAll('[data-media-category]').forEach(b=>b.onclick=()=>{mediaCategory=b.dataset.mediaCategory;document.querySelectorAll('[data-media-category]').forEach(t=>t.classList.toggle('active',t===b));loadMedia();});
$('mediaSearch').oninput=()=>{clearTimeout(mediaSearchTimer);mediaSearchTimer=setTimeout(loadMedia,250);};
$('mediaRefresh').onclick=()=>loadMedia().then(()=>toast('Đã làm mới thư viện.'));
$('mediaOpenFolder').onclick=async()=>{try{await mediaRequest('POST','/api/media/open-folder',{});}catch(error){toast(error.message,true);}};
$('mediaUpload').onclick=()=>$('mediaFiles').click();
$('mediaFiles').onchange=async()=>{const files=[...$('mediaFiles').files];$('mediaFiles').value='';await uploadMediaFiles(files,$('mediaUploads'));await loadMedia();};
$('mediaDrop').onclick=()=>$('mediaFiles').click();
$('mediaDrop').onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('mediaFiles').click();}};
for(const zone of [$('mediaDrop'),$('imageLibrary')]){
  zone.addEventListener('dragover',e=>{if(!e.dataTransfer.types.includes('Files'))return;e.preventDefault();zone.classList.add('over');});
  zone.addEventListener('dragleave',()=>zone.classList.remove('over'));
  zone.addEventListener('drop',async e=>{if(!e.dataTransfer.files.length)return;e.preventDefault();zone.classList.remove('over');
    if(zone.id==='mediaDrop'){await uploadMediaFiles(e.dataTransfer.files,$('mediaUploads'));await loadMedia();}
    else pickerUpload(e.dataTransfer.files);});
}
// ---- Xem trước ----
async function openPreview(index){
  if(index<0||index>=mediaItems.length)return;
  previewIndex=index;const item=mediaItems[index],image=item.media_type==='image';
  $('previewKind').textContent=`${image?'ẢNH':'VIDEO'} · ${index+1}/${mediaItems.length}`;
  $('previewName').textContent=item.display_name;
  $('previewMedia').innerHTML=!item.available?'<p class="notice">File không còn trên máy.</p>':image?`<img src="/api/media/item/${item.id}/file" alt="${esc(item.display_name)}">`:`<video controls preload="metadata" src="/api/media/item/${item.id}/file"></video>`;
  $('previewRename').value=item.display_name;$('previewCategory').value=item.category;
  $('previewDownload').href=`/api/media/item/${item.id}/download`;
  const rows=[['Loại',image?'Ảnh':'Video'],['Nguồn',sourceNames[item.source_type]],['Kích thước',mediaSize(item)],['Dung lượng',formatBytes(item.file_size)],['Ngày thêm',formatDate(item.created_at)],['File gốc',item.original_filename||'—'],['Định dạng',item.mime_type]];
  if(item.sha256)rows.push(['SHA-256',item.sha256.slice(0,16)+'…']);
  $('previewMeta').innerHTML=rows.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
  $('previewUsage').textContent='';$('previewPrev').disabled=index===0;$('previewNext').disabled=index===mediaItems.length-1;
  if(!$('mediaPreview').open)$('mediaPreview').showModal();
  try{const detail=await (await fetch(`/api/media/item/${item.id}`)).json();if(previewIndex!==index)return;
    const parts=[];if(detail.jobs?.length)parts.push(`Đang gắn vào: ${detail.jobs.map(j=>`${j.name}${j.status==='running'?' (đang chạy)':''}`).slice(0,10).join(', ')}`);
    if(detail.characters?.length)parts.push(`Ảnh nhân vật: ${detail.characters.map(c=>c.name).join(', ')}`);
    if(item.linked_job_id)parts.push(`Tạo từ tác vụ ${item.linked_job_id.slice(0,12)}${item.linked_scene_id?` · cảnh ${item.linked_scene_id.slice(0,8)}`:''}`);
    $('previewUsage').textContent=parts.join(' · ')||'Chưa được prompt hay nhân vật nào dùng.';
  }catch{}
}
$('previewPrev').onclick=()=>openPreview(previewIndex-1);$('previewNext').onclick=()=>openPreview(previewIndex+1);
$('closePreview').onclick=()=>$('mediaPreview').close();
$('mediaPreview').onclose=()=>{const video=$('previewMedia').querySelector('video');if(video)video.pause();$('previewMedia').innerHTML='';};
$('mediaPreview').addEventListener('keydown',e=>{if(e.target.matches('input,select'))return;if(e.key==='ArrowLeft')openPreview(previewIndex-1);if(e.key==='ArrowRight')openPreview(previewIndex+1);});
$('previewSave').onclick=async()=>{const item=mediaItems[previewIndex];if(!item)return;
  try{const {item:saved}=await mediaRequest('PATCH',`/api/media/item/${item.id}`,{display_name:$('previewRename').value,category:$('previewCategory').value});
    mediaItems[previewIndex]=saved;toast('Đã lưu tên và phân loại.');renderMedia();openPreview(previewIndex);}catch(error){toast(error.message,true);}};
$('previewDelete').onclick=async()=>{const item=mediaItems[previewIndex];if(!item)return;
  try{if(await deleteMedia([item.id])){$('mediaPreview').close();await loadMedia();}}catch(error){toast(error.message,true);}};
// ---- Chọn ảnh từ thư viện cho prompt (Video Studio / Prompt Studio) ----
function pickerCurrent(){
  if(pickerTarget==='studio')return studioImages;
  if(pickerTarget==='characters')return fixedCharacters.map(c=>c.token);
  const prompts=splitPrompts(),index=Number($('imagePromptIndex').value),entry=promptImages.get(index);
  return entry&&entry.prompt===prompts[index]?entry.assets.map(a=>a.token):[];
}
async function openPicker(target){
  pickerTarget=target;pickerSelected.clear();
  if(target==='characters'){
    $('pickerTarget').textContent='Chọn ảnh nhân vật cố định: tool gắn các ảnh này vào MỌI cảnh của lượt chạy (tối đa 10 ảnh mỗi cảnh).';
  }else if(target==='composer'){
    const prompts=splitPrompts(),index=Number($('imagePromptIndex').value);
    if(!prompts[index])return toast('Nhập prompt trước khi chọn ảnh.',true);
    $('pickerTarget').textContent=`Gắn ảnh cho Prompt ${index+1}: ${prompts[index].slice(0,90)}${prompts[index].length>90?'…':''}`;
  }else{
    if(!studioJob||!studioEditing)return toast('Prompt này đang chạy hoặc đã xong nên không đổi ảnh được.',true);
    $('pickerTarget').textContent=`Gắn ảnh cho ${studioJob.name}${studioJob.scene_id?' · cảnh của dự án':''}. Ảnh được lưu khi bấm Lưu thay đổi.`;
  }
  $('pickerUploads').hidden=true;$('pickerUploads').innerHTML='';
  await loadPicker();$('imageLibrary').showModal();
}
async function loadPicker(){
  try{const params={type:'image'};const q=$('pickerSearch').value.trim();if(q)params.q=q;if($('pickerCategory').value)params.category=$('pickerCategory').value;
    pickerItems=(await fetchMedia(params)).items.filter(item=>item.available);renderPicker();}
  catch(error){toast(error.message,true);}
}
function renderPicker(){
  const attached=new Set(pickerCurrent());
  $('imageLibraryGrid').innerHTML=pickerItems.length?pickerItems.map(item=>`<button type="button" class="pick-card${pickerSelected.has(item.id)?' selected':''}${attached.has(item.id)?' attached':''}" data-pick="${item.id}" aria-pressed="${pickerSelected.has(item.id)}" title="${esc(item.display_name)}"><img loading="lazy" src="/api/media/item/${item.id}/thumbnail" alt=""><span class="pick-name">${esc(item.display_name)}</span><span class="pick-mark">${attached.has(item.id)?'Đã gắn':pickerSelected.has(item.id)?'✓':''}</span></button>`).join(''):'<div class="notice">Chưa có ảnh nào. Bấm “+ Tải ảnh lên” hoặc kéo thả ảnh vào đây.</div>';
  const n=pickerSelected.size;$('pickerCount').textContent=n?`Đã chọn ${n} ảnh · tối đa 10 ảnh mỗi prompt`:'Chưa chọn ảnh';
  $('pickerAttach').disabled=!n;$('pickerAttach').textContent=n?`Gắn ${n} ảnh vào Prompt`:'Gắn vào Prompt';
}
async function pickerUpload(files){
  const added=await uploadMediaFiles(files,$('pickerUploads'));
  added.forEach(item=>item&&pickerSelected.add(item.id));await loadPicker();
}
$('imageLibraryGrid').onclick=e=>{const card=e.target.closest('[data-pick]');if(!card)return;const id=card.dataset.pick;
  if(pickerCurrent().includes(id))return toast('Ảnh này đã được gắn cho prompt.');
  pickerSelected.has(id)?pickerSelected.delete(id):pickerSelected.add(id);renderPicker();};
$('pickerSearch').oninput=()=>{clearTimeout(mediaSearchTimer);mediaSearchTimer=setTimeout(loadPicker,250);};
$('pickerCategory').onchange=loadPicker;
$('pickerUpload').onclick=()=>$('pickerFiles').click();
$('pickerFiles').onchange=()=>{const files=[...$('pickerFiles').files];$('pickerFiles').value='';pickerUpload(files);};
$('closeImageLibrary').onclick=()=>$('imageLibrary').close();
$('pickerAttach').onclick=()=>{
  const chosen=[...pickerSelected].map(id=>pickerItems.find(item=>item.id===id)).filter(Boolean);if(!chosen.length)return;
  if(pickerTarget==='characters'){
    const fresh=chosen.filter(item=>!fixedCharacters.some(c=>c.token===item.id));
    if(fixedCharacters.length+fresh.length>10)return toast('Tối đa 10 ảnh nhân vật cố định.',true);
    fixedCharacters.push(...fresh.map(item=>({token:item.id,name:item.display_name})));saveVideoSettings();renderFixedCharacters();
    toast(`Đã thêm ${fresh.length} ảnh nhân vật: gắn vào mọi cảnh khi chạy.`);
  }else if(pickerTarget==='studio'){
    if(!studioEditing)return toast('Prompt này không còn sửa được.',true);
    const fresh=chosen.filter(item=>!studioImages.includes(item.id));
    if(studioImages.length+fresh.length>10)return toast(`Mỗi prompt tối đa 10 ảnh; prompt đang có ${studioImages.length} ảnh.`,true);
    studioImages.push(...fresh.map(item=>item.id));renderJobImages();
    toast(`Đã gắn ${fresh.length} ảnh. Bấm Lưu thay đổi để giữ.`);
  }else{
    const prompts=splitPrompts(),index=Number($('imagePromptIndex').value);
    if(!prompts[index])return toast('Prompt không còn tồn tại. Chọn lại prompt.',true);
    let entry=promptImages.get(index);if(entry&&entry.prompt!==prompts[index])entry=null;
    const assets=entry?.assets||[],fresh=chosen.filter(item=>!assets.some(a=>a.token===item.id));
    if(assets.length+fresh.length>10)return toast(`Mỗi prompt tối đa 10 ảnh; prompt đang có ${assets.length} ảnh.`,true);
    promptImages.set(index,{prompt:prompts[index],assets:[...assets,...fresh.map(item=>({token:item.id,name:item.display_name,type:item.mime_type}))]});
    renderImageAssignments();toast(`Đã gắn ${fresh.length} ảnh vào Prompt ${index+1}.`);
  }
  $('imageLibrary').close();
};
$('jobLibrary').onclick=()=>openPicker('studio');
function renderLibraryIfOpen(){if($('imageLibrary').open)loadPicker();}

// ===== Thiết lập video của Studio: tỉ lệ, thời lượng, độ phân giải, nick, tự ghép, ảnh nhân vật cố định =====
function videoOptions(){
  return {aspect:$('vAspect').value,duration:Number($('vDuration').value)||0,resolution:$('vResolution').value,
    stitch:$('vStitch').checked,characters:fixedCharacters.map(c=>c.token),account_id:$('vAccount').value||null};
}
function saveVideoSettings(){try{localStorage.setItem(VIDEO_KEY,JSON.stringify({...videoOptions(),fixed:fixedCharacters}));}catch{}}
function loadVideoSettings(){
  try{const v=JSON.parse(localStorage.getItem(VIDEO_KEY)||'null');if(!v)return;
    for(const [id,key] of [['vAspect','aspect'],['vResolution','resolution']])if([...$(id).options].some(o=>o.value===v[key]))$(id).value=v[key];
    if([...$('vDuration').options].some(o=>o.value===String(v.duration||'')))$('vDuration').value=String(v.duration||'');
    $('vStitch').checked=Boolean(v.stitch);$('vAccount').dataset.want=v.account_id||'';
    fixedCharacters=Array.isArray(v.fixed)?v.fixed.filter(c=>/^[a-f0-9]{32}$/.test(c.token||'')).slice(0,10):[];
  }catch{}
}
function renderContentCharacters(){
  const box=$('contentCharacterChips');if(!box)return;
  box.innerHTML=fixedCharacters.map((c,i)=>`<span class="image-chip" title="${esc(c.name)}"><img src="/api/library/image/${c.token}" alt=""><span>${esc(c.name)}</span><button type="button" data-remove-fixed="${i}" aria-label="Bỏ ${esc(c.name)}">×</button></span>`).join('')||'<small class="detail">Chưa chọn ảnh nhân vật (không bắt buộc).</small>';
}
function renderFixedCharacters(){
  renderContentCharacters();
  $('vCharacterChips').innerHTML=fixedCharacters.map((c,i)=>`<span class="image-chip" title="${esc(c.name)}"><img src="/api/library/image/${c.token}" alt=""><span>${esc(c.name)}</span><button type="button" data-remove-fixed="${i}" aria-label="Bỏ ${esc(c.name)}">×</button></span>`).join('')||'<span class="detail">Chưa chọn — bấm “+ Nhân vật” để lấy ảnh từ Thư viện.</span>';
  $('vClearCharacters').hidden=!fixedCharacters.length;
}
function renderNickOptions(){
  const select=$('vAccount'),want=select.dataset.want??select.value;delete select.dataset.want;
  const options='<option value="">🔄 Xoay vòng nick</option>'+state.accounts.map((a,i)=>`<option value="${a.id}">Nick ${i+1} · ${esc(a.email)}${a.state==='ready'?'':a.state==='attention'?' (cần kiểm tra)':''}</option>`).join('');
  if(select.dataset.sig!==options){select.innerHTML=options;select.dataset.sig=options;}
  select.value=state.accounts.some(a=>a.id===want)?want:'';
}
function videoSpecCell(job){
  const parts=[job.aspect||'tự chọn',job.duration?`${job.duration}s`:'—',job.resolution||'gốc'];
  const post=job.status==='done'&&job.resolution?postLabel(job.post_status):'';
  return `<span class="spec-chip">${esc(parts.join(' · '))}</span>${post?`<span class="detail${job.post_status.startsWith('error:')?' error':''}" title="${esc(job.post_status.startsWith('error:')?job.post_status.slice(6):'')}">${esc(post)}</span>`:''}`;
}
let stitchHidden=false;try{stitchHidden=localStorage.getItem('hana-hide-stitch')==='1';}catch{}
function renderStitchPanel(){
  const batches=(state.batches||[]).filter(b=>Number(b.scenes)>0),panel=$('stitchPanel');panel.hidden=!batches.length||stitchHidden;
  panel.innerHTML=batches.length?`<div class="stitch-head"><b>🔗 Video ghép</b><button type="button" id="hideStitch" title="Ẩn khung này. Bật lại bằng cách tick “Ghép” khi tạo video.">✕ Ẩn</button></div>`+batches.map(b=>`<div class="stitch-row"><span>Lượt ${esc(new Date(b.created*1000).toLocaleString('vi-VN',{dateStyle:'short',timeStyle:'short'}))} · ${b.done}/${b.scenes} cảnh xong${b.failed?` · ${b.failed} lỗi`:''}</span><span class="stitch-state ${b.stitch_status}">${esc(stitchNames[b.stitch_status]||b.stitch_status)}</span>${b.stitch_error?`<span class="detail error">${esc(b.stitch_error)}</span>`:''}${b.stitch_status==='done'?`<a class="button-link" href="/api/batch/${esc(b.batch)}/video" target="_blank" rel="noopener">▶ Xem</a><a class="button-link" href="/api/batch/${esc(b.batch)}/video?download=1">⇩ Tải</a>`:''}${b.stitch_status!=='running'&&b.done>=2?`<button type="button" data-stitch-now="${esc(b.batch)}" title="Ghép các cảnh đã xong (bỏ qua cảnh lỗi/chưa xong)">${b.stitch_status==='done'?'↻ Ghép lại':'Ghép ngay'}</button>`:''}</div>`).join(''):'';
}
document.addEventListener('click',e=>{if(e.target.closest('#hideStitch')){stitchHidden=true;try{localStorage.setItem('hana-hide-stitch','1');}catch{}renderStitchPanel();toast('Đã ẩn khung Video ghép. Tick “Ghép” khi tạo video để hiện lại.');return;}
  const b=e.target.closest('[data-stitch-now]');if(!b)return;
  if(!confirm('Ghép ngay các cảnh đã xong của lượt này? Cảnh lỗi hoặc chưa xong sẽ bị bỏ qua.'))return;
  action(async()=>{await api('/api/batch/stitch',{batch:b.dataset.stitchNow});toast('Đang ghép video. Xem tiến độ ở khung Video ghép.');});});
for(const id of ['vAspect','vDuration','vResolution','vStitch','vAccount'])$(id).addEventListener('change',saveVideoSettings);
$('vStitch').addEventListener('change',()=>{if($('vStitch').checked&&stitchHidden){stitchHidden=false;try{localStorage.removeItem('hana-hide-stitch');}catch{}if(state)renderStitchPanel();}});
$('vCharacters').onclick=()=>openPicker('characters');
$('contentCharacters').onclick=()=>openPicker('characters');
$('vClearCharacters').onclick=()=>{fixedCharacters=[];saveVideoSettings();renderFixedCharacters();};
$('vCharacterChips').onclick=e=>{const b=e.target.closest('[data-remove-fixed]');if(!b)return;fixedCharacters.splice(Number(b.dataset.removeFixed),1);saveVideoSettings();renderFixedCharacters();}
$('contentCharacterChips').onclick=$('vCharacterChips').onclick;;
loadVideoSettings();renderFixedCharacters();if(state){renderNickOptions();renderStitchPanel();}

// ===== Thanh hỗ trợ trên cùng (lời nhắn, SĐT, ảnh QR do người dùng tải lên; không có popup) =====
function renderSupport(){
  const sp=state?.support;if(!sp)return;
  $('supportMessage').textContent=sp.message;$('supportPhone').textContent=sp.phone;
  $('supportQr').hidden=!sp.qr;$('supportQrEmpty').hidden=Boolean(sp.qr);
  if(sp.qr&&$('supportQr').dataset.token!==sp.qr){$('supportQr').src='/api/library/image/'+sp.qr;$('supportQr').dataset.token=sp.qr;}
}
setInterval(renderSupport,1000);renderSupport();
$('copySupportPhone').onclick=()=>{const phone=$('supportPhone').textContent.replace(/\s|\./g,'');
  navigator.clipboard.writeText(phone).then(()=>toast(`Đã sao chép số ${phone}.`)).catch(()=>toast('Không sao chép được. Chọn và sao chép số trên thanh.',true));};
// ===== Trang Cộng đồng: nhóm Zalo/Facebook và video demo do người dùng tự thêm; mời cafe (SĐT + QR, không popup) =====
function communityList(kind){
  const items=state?.community?.[kind]||[],icon=kind==='groups'?'💬':'▶',action=kind==='groups'?'Tham gia →':'Xem video →';
  return items.length?items.map((item,i)=>`<div class="community-item"><span class="community-icon">${icon}</span><div class="community-info"><b>${esc(item.name)}</b><small>${esc(item.url)}</small></div><button type="button" class="community-open" data-open-link="${esc(item.url)}">${action}</button><button type="button" class="community-remove danger-outline" data-remove-link="${kind}:${i}" aria-label="Xóa ${esc(item.name)}" title="Xóa">✕</button></div>`).join('')
    :`<p class="community-empty">${kind==='groups'?'Chưa có nhóm nào. Thêm nhóm Zalo (hoặc Facebook) ở ô bên dưới.':'Chưa có video demo. Thêm link video ở ô bên dưới.'}</p>`;
}
function renderCommunity(force=true){
  if(!state)return;const sig=JSON.stringify([state.community,state.support]);if(!force&&sig===communitySig)return;communitySig=sig;
  for(const kind of ['groups','videos'])$(kind==='groups'?'communityGroups':'communityVideos').innerHTML=communityList(kind);
  const sp=state.support||{};
  $('communityPhone').textContent=sp.phone||'';$('communityMessage').textContent=sp.message||'';
  $('communityQr').hidden=!sp.qr;$('communityQrEmpty').hidden=Boolean(sp.qr);
  if(sp.qr&&$('communityQr').dataset.token!==sp.qr){$('communityQr').src='/api/library/image/'+sp.qr;$('communityQr').dataset.token=sp.qr;}
}
async function saveCommunity(groups,videos,message){
  const r=await api('/api/settings/community',{groups,videos});state.community=r.community;renderCommunity();if(message)toast(message);
}
document.addEventListener('click',e=>{
  const open=e.target.closest('[data-open-link]');
  if(open)api('/api/open-link',{url:open.dataset.openLink}).then(()=>toast('Đã mở link trong trình duyệt.')).catch(error=>toast(error.message,true));
  const remove=e.target.closest('[data-remove-link]');
  if(remove){const [kind,index]=remove.dataset.removeLink.split(':'),data=structuredClone(state.community);const item=data[kind][Number(index)];
    if(!item||!confirm(`Xóa "${item.name}" khỏi trang Cộng đồng?`))return;data[kind].splice(Number(index),1);
    saveCommunity(data.groups,data.videos,'Đã xóa.').catch(error=>toast(error.message,true));}
});
for(const [form,kind] of [['communityGroupForm','groups'],['communityVideoForm','videos']]){
  $(form).onsubmit=e=>{e.preventDefault();const f=$(form),name=f.querySelector('[name=name]').value.trim(),url=f.querySelector('[name=url]').value.trim();
    const data=structuredClone(state.community);data[kind].push({name,url});
    saveCommunity(data.groups,data.videos,kind==='groups'?`Đã thêm nhóm "${name}".`:`Đã thêm video "${name}".`).then(()=>f.reset()).catch(error=>toast(error.message,true));};
}
$('communityCopyPhone').onclick=()=>$('copySupportPhone').click();
setInterval(()=>{if(!$('communityView').hidden)renderCommunity(false);},1000);

// ===== Bản quyền & phân quyền (key ký số) =====
function addMonthsJs(iso,months){const [y,m,d]=iso.split('-').map(Number);const t=new Date(y,m-1+months,1);const last=new Date(t.getFullYear(),t.getMonth()+1,0).getDate();const e=new Date(t.getFullYear(),t.getMonth(),Math.min(d,last));e.setDate(e.getDate()-1);return `${e.getFullYear()}-${String(e.getMonth()+1).padStart(2,'0')}-${String(e.getDate()).padStart(2,'0')}`;}
function renderLicenseBanner(){
  const l=state?.license;if(!l)return;
  const roleNames={super_admin:'Super Admin',admin:'Admin',manager:'Manager',user:'User',viewer:'Viewer'};
  const showRole=l.enforced&&(l.state==='owner'||['active','expiring'].includes(l.state));
  $('sideRole').hidden=!showRole;$('sideRole').textContent=showRole?`🔑 ${roleNames[l.role]||l.role}${l.expires?` · đến ${l.expires.split('-').reverse().join('/')}`:''}`:'';const ok=['not_enforced','owner','active'].includes(l.state);
  $('licenseBanner').hidden=ok&&l.state!=='expiring';
  $('licenseBanner').className='license-banner'+(l.state==='expiring'?' warn':'');
  $('licenseBannerText').textContent=`🔑 ${l.message}`;
  if(l.state==='expiring')$('licenseBanner').hidden=false;
}
setInterval(renderLicenseBanner,1500);
function renderUpdate(){
  const u=state?.update;if(!u)return;
  const busy=u.phase==='downloading'||u.phase==='installing';
  const text=u.phase==='downloading'?`⟳ Đang tải bản ${u.latest}… ${u.progress||0}%`:u.phase==='installing'?`⟳ Đang cài bản ${u.latest}. App sẽ tự tắt rồi mở lại sau khoảng 1 phút.`:u.phase==='error'?`⚠ Cập nhật lỗi: ${u.error}`:`✨ Có bản mới ${u.latest} (đang dùng ${state.app_version}). Cập nhật ngay trên app, dữ liệu và bản quyền giữ nguyên.`;
  $('updateBanner').hidden=!(u.supported&&(u.available||busy||u.phase==='error'));
  $('updateBannerText').textContent=text;$('updateBannerButton').hidden=busy;$('updateBannerButton').textContent=u.phase==='error'?'Thử lại':u.manual?'⇩ Tải bản Mac mới':'⟳ Cập nhật ngay';
  $('updateChip').textContent=!u.supported?'Bản chạy thử':busy?'Đang cập nhật':u.available?`Có bản ${u.latest}`:'Mới nhất';
  $('updateChip').className='status-chip '+(u.available?'review':'approved');
  $('updateSummary').textContent=!u.supported?`Phiên bản ${state.app_version}. Tự cập nhật chỉ có ở bản đã cài trên Windows.`:u.available?`Đang dùng ${state.app_version} · bản mới ${u.latest}.`:`Đang dùng ${state.app_version}${u.error?` · ${u.error}`:' (mới nhất)'}.`;
  $('updateInstall').hidden=!u.available||busy;$('updateCheck').disabled=busy||!u.supported;
}
setInterval(renderUpdate,1500);
// Giao diện sáng / tối (lưu trong dữ liệu app, áp dụng ngay).
let themeChoice=null;const darkQuery=window.matchMedia?matchMedia('(prefers-color-scheme: dark)'):null;
function applyTheme(choice){themeChoice=choice||'dark';const light=themeChoice==='light'||(themeChoice==='system'&&darkQuery&&!darkQuery.matches);
  document.documentElement.dataset.theme=light?'light':'dark';
  document.querySelectorAll('[data-theme-choice]').forEach(b=>b.classList.toggle('active',b.dataset.themeChoice===themeChoice));}
setInterval(()=>{const t=state?.theme||'dark';if(t!==themeChoice)applyTheme(t);},800);
if(darkQuery)darkQuery.addEventListener('change',()=>themeChoice==='system'&&applyTheme('system'));
document.addEventListener('click',e=>{const b=e.target.closest('[data-theme-choice]');if(!b)return;applyTheme(b.dataset.themeChoice);
  api('/api/settings/theme',{theme:b.dataset.themeChoice}).then(()=>refresh()).catch(err=>toast(err.message,true));});
$('logCopy').onclick=()=>action(async()=>{const r=await fetch('/api/logs');const d=await r.json();if(!r.ok)throw Error(d.error||'Không đọc được nhật ký.');
  try{await navigator.clipboard.writeText(d.text);toast('Đã sao chép nhật ký lỗi. Dán (Ctrl+V) gửi cho hỗ trợ.');}
  catch{const box=document.createElement('textarea');box.value=d.text;document.body.appendChild(box);box.select();const ok=document.execCommand('copy');box.remove();toast(ok?'Đã sao chép nhật ký lỗi. Dán (Ctrl+V) gửi cho hỗ trợ.':'Không sao chép được: bấm "Mở thư mục nhật ký" và gửi file runner.log.',!ok);}});
$('logOpen').onclick=()=>action(async()=>{await api('/api/logs/open-folder',{});});
// Số luồng tối đa theo cấu hình của máy đang chạy (tool tự đo RAM / CPU), GPU cho bước xử lý video.
let parallelMax=0;
function renderHardware(){const e=state?.engine,hw=e?.hardware;if(!hw)return;
  const max=e.max_parallel||1;
  if(max!==parallelMax){const sel=$('parallel'),prev=Number(sel.value)||0,first=!parallelMax;parallelMax=max;
    sel.innerHTML=Array.from({length:max},(_,i)=>`<option value="${i+1}">${i+1} tài khoản</option>`).join('');
    sel.value=String(first?max:Math.min(prev||max,max));
    const login=$('loginParallel'),lp=Number(login.value)||2,loginMax=Math.min(max,5);
    login.innerHTML=Array.from({length:loginMax},(_,i)=>`<option value="${i+1}">${i+1}</option>`).join('');login.value=String(Math.min(lp,loginMax));}
  $('parallelLabel').textContent=`Số luồng (máy này chạy tối đa ${max} tài khoản cùng lúc)`;
  const ram=hw.ram_gb?`RAM ${hw.ram_gb} GB`:'RAM ?';
  $('machineInfo').textContent=`Máy này: ${ram}, CPU ${hw.cores} nhân${hw.gpu?`, GPU ${hw.gpu}`:''} → tối đa ${max} luồng.`;
  $('hardwareSummary').textContent=`${ram} (còn trống ${hw.free_gb??'?'} GB) · CPU ${hw.cores} nhân · tối đa ${max} tài khoản chạy cùng lúc. ${hw.gpu?`Card đồ họa: ${hw.gpu}.`:'Không tìm thấy card đồ họa hỗ trợ mã hóa video: nâng 4K / ghép video dùng CPU.'}`;
  $('gpuChip').textContent=hw.gpu?(hw.gpu_enabled?'GPU đang dùng':'GPU đang tắt'):'Dùng CPU';$('gpuChip').className='status-chip '+(hw.gpu&&hw.gpu_enabled?'approved':'');
  const box=$('gpuEncode');if(document.activeElement!==box){box.checked=hw.gpu_enabled;box.disabled=!hw.gpu;}}
setInterval(renderHardware,1500);
$('gpuEncode').onchange=e=>action(async()=>{await api('/api/settings/gpu',{enabled:e.target.checked});toast(e.target.checked?'Đã bật tăng tốc GPU.':'Đã tắt GPU: dùng CPU.');});
// Bảng báo có phiên bản mới: hiện ngay khi app phát hiện bản mới (mỗi lần mở app, mỗi phiên bản một lần).
let updatePrompted='';
function promptUpdate(){const u=state?.update;if(!u||!u.supported||!u.available||u.phase==='downloading'||u.phase==='installing'||updatePrompted===u.latest)return;
  updatePrompted=u.latest;const running=(state.totals?.running||0)>0;
  $('updateDialogTitle').textContent=`Có phiên bản mới ${u.latest}`;$('updateDialogText').textContent=`Bạn đang dùng ${state.app_version}. Cập nhật ngay để có bản sửa lỗi và tính năng mới nhất.`;
  $('updateDialogBusy').hidden=!running;$('updateDialogNow').disabled=running;if(!$('updateDialog').open)$('updateDialog').showModal();}
setInterval(promptUpdate,2000);
$('updateDialogLater').onclick=()=>$('updateDialog').close();
$('updateDialogNow').onclick=()=>{$('updateDialog').close();action(async()=>{await api('/api/update/install',{});toast('Đang tải bản cập nhật…');});};
function renderAssignHint(){if(!state)return;$('assignHint').hidden=!state.assign_hint;$('assignHintText').textContent=state.assign_hint?`⚠ Prompt đang chờ chưa được chia tài khoản: ${state.assign_hint}`:'';}
setInterval(renderAssignHint,1500);
async function installUpdate(){if(state.update.manual){action(async()=>{await api('/api/update/install',{});toast(`Đã mở trang tải bản ${state.update.latest}. Tải file HanaMedia-…-mac.dmg, mở ra và kéo HANA MEDIA vào Applications (thay bản cũ). Dữ liệu giữ nguyên.`);});return;}
  if(!confirm(`Cập nhật lên bản ${state.update.latest}?\n\nApp sẽ tải bộ cài, tự tắt, cài đè rồi tự mở lại (khoảng 1–2 phút). Tài khoản, hàng đợi, video và bản quyền giữ nguyên.`))return;
  action(async()=>{await api('/api/update/install',{});toast('Đang tải bản cập nhật…');});}
$('updateBannerButton').onclick=installUpdate;$('updateInstall').onclick=installUpdate;
$('updateCheck').onclick=()=>action(async()=>{const r=await api('/api/update/check',{});toast(r.update.available?`Có bản mới ${r.update.latest}.`:r.update.error||'Bạn đang dùng bản mới nhất.',Boolean(r.update.error));});
async function loadLicense(){
  try{const r=await fetch('/api/license');licenseData=await r.json();if(!r.ok)throw Error(licenseData.error);renderLicense();}catch(error){toast(error.message||'Không tải được thông tin bản quyền.',true);}
}
function renderLicense(){
  const d=licenseData,st=d.status,[label,cls]=licenseStates[st.state]||[st.state,''];
  $('licenseChip').textContent=label;$('licenseChip').className='status-chip '+cls;$('licenseMessage').textContent=st.message;
  $('licenseMachine').textContent=st.request_code||st.machine;
  const rows=st.id?[['Tài khoản',st.name],['Role',d.roles[st.role]||st.role],['Gói',d.plans[st.plan]||st.plan],['Bắt đầu',dmy(st.start)],['Hết hạn',dmy(st.expires)],['Quota video',st.quota?`${st.used}/${st.quota}`:'Không giới hạn']]:[];
  $('licenseInfo').innerHTML=rows.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
  $('licenseActivate').hidden=!st.enforced||st.state==='owner';
  $('licenseRemove').hidden=!st.id;
  $('licenseOnline').hidden=!st.online_check||!st.id;
  $('licenseOnlineText').textContent=st.checked_at?`Kiểm tra thu hồi online lần cuối: ${new Date(st.checked_at*1000).toLocaleString('vi-VN',{dateStyle:'short',timeStyle:'short'})}${st.check_error?` · lần thử gần nhất lỗi: ${st.check_error}`:''}`:`Chưa kiểm tra được thu hồi online${st.check_error?`: ${st.check_error}`:''}.`;
  $('issuerInit').hidden=st.enforced||st.issuer;$('issuerPanel').hidden=!st.issuer;
  if(!st.issuer)return;
  $('issuerTrust').hidden=st.issuer_trusted||st.issuer_mode!=='root'||st.enforced;
  $('issuerTrust').innerHTML=st.issuer_trusted?'':`<b>Khóa công khai của bạn chưa được gắn cứng vào app.</b> Key bạn tạo đã ký được, nhưng app phát cho khách chỉ chấp nhận khi khóa công khai dưới đây được gắn vào bản đóng gói. Sao chép và gửi chuỗi này cho người đóng gói app:<code class="pubkey">${esc(st.public_key)}</code><button type="button" id="copyPubkey">📋 Sao chép khóa công khai</button>`;
  const db=d.dashboard,s=db.stats,scope=db.scope;
  $('issuerScope').hidden=scope.mode!=='delegate';
  $('issuerScope').innerHTML=scope.mode==='delegate'?`<b>Bạn đang dùng quyền ${esc(d.roles[scope.role])}${st.issued_by?` do ${esc(st.issued_by)} cấp`:''}.</b> Bạn cấp được role từ ${esc(d.roles[scope.role])} trở xuống, chỉ những quyền mình đang có, hạn tối đa ${dmy(scope.max_expires)}.${scope.can_create?'':' Tài khoản của bạn không được tạo tài khoản mới.'}${scope.can_renew?'':' Tài khoản của bạn không được gia hạn.'}`:'';
  $('licenseAdd').disabled=!scope.can_create;
  $('licenseStats').innerHTML=[['👥','Tổng tài khoản',s.total,''],['👑','Tài khoản Admin',s.admins,''],['⚠','Sắp hết hạn (≤ 7 ngày)',s.expiring,'warn'],['▶','Đang hoạt động',s.active,'ok'],['💰','Doanh thu',vnd(s.revenue),'']].map(([i,l,v,c])=>`<div class="license-stat ${c}"><span>${i}</span><div><small>${esc(l)}</small><b>${esc(String(v))}</b></div></div>`).join('');
  renderLicenseRows();
  $('licenseRenewals').innerHTML=db.events.filter(e=>e.action==='create'||e.action==='renew').map(e=>`<tr><td>${new Date(e.at*1000).toLocaleString('vi-VN',{dateStyle:'short',timeStyle:'short'})}</td><td>${esc(e.name||'—')}</td><td>${esc(d.plans[e.plan]||e.plan)} · ${e.action==='create'?'Tạo mới':'Gia hạn'}</td><td>${dmy(e.expires)}</td><td>${vnd(e.amount)}</td></tr>`).join('')||'<tr><td colspan="5" class="detail">Chưa có giao dịch.</td></tr>';
  const perms=Object.keys(d.permissions);
  $('matrixHead').innerHTML=`<tr><th>ROLE</th>${perms.map(p=>`<th>${esc(d.permissions[p])}</th>`).join('')}</tr>`;
  $('matrixRows').innerHTML=Object.keys(d.roles).map(r=>`<tr><td>${esc(d.roles[r])}</td>${perms.map(p=>`<td class="${d.defaults[r].includes(p)?'yes':'no'}">${d.defaults[r].includes(p)?'✓':'—'}</td>`).join('')}</tr>`).join('');
  $('licenseRevenue').innerHTML=db.revenue.length?db.revenue.map(r=>`<div class="rev-row"><span>${esc(r.month)}</span><b>${vnd(r.total)}</b><small>${r.count} giao dịch</small></div>`).join(''):'<p class="detail">Chưa có doanh thu. Nhập số tiền khi tạo hoặc gia hạn tài khoản.</p>';
  $('licenseAudit').innerHTML=db.events.slice(0,60).map(e=>`<div><time>${new Date(e.at*1000).toLocaleString('vi-VN',{dateStyle:'short',timeStyle:'short'})}</time><span>${esc(e.detail||e.action)}</span></div>`).join('')||'<p class="detail">Chưa có nhật ký.</p>';
  renderRevocation(db.revocation,scope);
  if(!licenseEditing)fillLicenseForm(null);
}
function renderRevocation(r,scope){
  const root=scope.mode==='root';
  $('ldRevokeNote').textContent=!r.enabled?'Bản chạy từ mã nguồn: khóa / xóa chỉ lưu trên máy này.':root?'Khóa hoặc xóa: máy khách ngừng dùng được trong khoảng 1 giờ (hoặc ngay khi mở lại app). Xóa là thu hồi vĩnh viễn.':'Khóa / xóa chỉ lưu trên máy bạn: key đã cấp vẫn chạy tới ngày hết hạn. Muốn chặn ngay, nhờ Super Admin khóa tài khoản đại lý của bạn.';
  $('revokeCard').hidden=!root||!r.enabled;if(!root||!r.enabled)return;
  const bad=Boolean(r.error)||r.pending;
  $('revokeChip').textContent=!r.has_token?'Chưa cài token':bad?'Chưa đăng được':'Đã đồng bộ';$('revokeChip').className='status-chip '+(!r.has_token||bad?'error':'approved');
  const when=r.published_at?new Date(r.published_at*1000).toLocaleString('vi-VN',{dateStyle:'short',timeStyle:'short'}):'chưa đăng lần nào';
  $('revokeText').textContent=`Đang thu hồi ${r.revoked} key (đã khóa hoặc đã xóa). Đăng gần nhất: ${when}${r.published_at?` (${r.count} key)`:''}.${r.pending?' Có thay đổi chưa đăng: máy khách đó vẫn dùng được cho tới khi đăng xong.':''}${r.error?` Lỗi: ${r.error}`:''}`;
  $('revokeRepo').textContent=r.repo||'';$('revokePublish').hidden=!r.has_token;$('revokeTokenRemove').hidden=!r.has_token;
  $('revokeSetupTitle').textContent=r.has_token?'Đổi / xóa token GitHub':'Cài token GitHub để đăng danh sách thu hồi';
  if(!r.has_token)$('revokeSetup').open=true;
}
function revokeToast(r,done){if(!r)return toast(done);if(r.local)return toast(done+' '+r.message);if(r.error)return toast(`${done} Nhưng chưa đăng thu hồi online: ${r.error}`,true);toast(done+' Đã đăng danh sách thu hồi online.');}
function renderLicenseRows(){
  const d=licenseData,q=$('licenseSearch').value.trim().toLowerCase();
  const list=d.dashboard.accounts.filter(a=>!q||a.name.toLowerCase().includes(q)||a.email.includes(q));
  $('licenseRows').innerHTML=list.map((a,i)=>{const [label,cls]=accountStates[a.state];return `<tr class="${licenseEditing===a.id?'selected':''}"><td>${i+1}</td><td><b>${esc(a.name)}</b><small>${esc(a.email)}</small></td><td><span class="role-chip ${a.role}">${esc(d.roles[a.role])}</span></td><td>${esc(d.plans[a.plan]||a.plan)}</td><td>${dmy(a.expires)}</td><td>${a.quota?a.quota.toLocaleString('vi-VN'):'∞'}</td><td>${a.devices.length||'Mọi máy'}${a.can_issue?' · <span class="role-chip admin" title="Máy này được tự cấp key cho khách của mình">quản lý</span>':''}</td><td><span class="status-chip ${cls}">${label}</span></td><td class="row-actions"><button type="button" data-license-edit="${a.id}" title="Xem / sửa chi tiết">✎</button><button type="button" data-license-renew="${a.id}" title="Gia hạn">↻</button><button type="button" data-license-key="${a.id}" title="Sao chép key">📋</button><button type="button" data-license-delete="${a.id}" title="Xóa tài khoản">🗑</button></td></tr>`;}).join('')||'<tr><td colspan="9" class="detail">Chưa có tài khoản. Bấm “+ Thêm tài khoản”.</td></tr>';
  const scroller=$('licenseRows').closest('.table-scroll');if(scroller)scroller.scrollLeft=0;
}
function fillLicenseForm(account){
  const d=licenseData;licenseEditing=account?.id||null;
  $('ldTitle').textContent=account?`Đang sửa: ${account.name}`:'Thêm tài khoản mới';
  const scope=d.dashboard.scope;$('ldRole').innerHTML=Object.entries(d.roles).filter(([k])=>scope.roles.includes(k)).map(([k,v])=>`<option value="${k}">${esc(v)}</option>`).join('');
  $('ldName').value=account?.name||'';$('ldEmail').value=account?.email||'';$('ldRole').value=account?.role&&scope.roles.includes(account.role)?account.role:(scope.roles.includes('user')?'user':scope.roles[scope.roles.length-1]);
  licensePlan=account?.plan||'1y';
  $('ldPlans').innerHTML=Object.entries(d.plans).map(([k,v])=>`<label class="ld-plan-opt"><input type="radio" name="ldPlan" value="${k}" ${k===licensePlan?'checked':''}> ${esc(v)}</label>`).join('');
  $('ldStart').value=account?.start||new Date().toISOString().slice(0,10);$('ldStart').disabled=false;$('ldExpires').disabled=false;
  $('ldDevices').value=(account?.device_codes||account?.devices||[]).join('\n');$('ldQuota').value=account?.quota||0;$('ldAmount').value=0;$('ldAmountWrap').hidden=Boolean(account);
  renderPerms(account?.permissions||d.defaults[$('ldRole').value].filter(p=>scope.permissions.includes(p)));
  updateExpiryPreview(account);
  $('ldRenew').disabled=!account||!scope.can_renew;$('ldLock').disabled=!account;$('ldDelete').disabled=!account;$('ldLock').textContent=account?.state==='locked'?'🔓 Mở khóa':'🔒 Khóa';
  $('ldSave').textContent=account?'💾 Lưu thay đổi & tạo key mới':'💾 Lưu & tạo key';
  $('ldKeyBox').hidden=true;
}
function renderPerms(selected){const allowed=licenseData.dashboard?.scope?.permissions||Object.keys(licenseData.permissions);$('ldPerms').innerHTML=Object.entries(licenseData.permissions).map(([k,v])=>`<label class="check${allowed.includes(k)?'':' disabled'}"><input type="checkbox" value="${k}" ${selected.includes(k)&&allowed.includes(k)?'checked':''} ${allowed.includes(k)?'':'disabled'}><span>${esc(v)}</span></label>`).join('');}
function updateExpiryPreview(account){if(account){$('ldExpires').value=account.expires;return;}const start=$('ldStart').value||new Date().toISOString().slice(0,10);
  if(licensePlan==='trial'){const [y,m,d]=start.split('-').map(Number);const e=new Date(y,m-1,d+1);$('ldExpires').value=`${e.getFullYear()}-${String(e.getMonth()+1).padStart(2,'0')}-${String(e.getDate()).padStart(2,'0')}`;return;}
  $('ldExpires').value=addMonthsJs(start,{'1m':1,'1y':12,'3y':36}[licensePlan]);}
function showKey(key,name,short){$('ldKey').value=key;$('ldKeyName').textContent=name?`Key gửi khách: ${name}`:'Key gửi khách';$('ldKeyBox').hidden=false;
  const code=short?.code||'';$('ldShort').textContent=code;$('ldShortBox').hidden=!code;$('ldLongKey').open=!code;
  $('ldShortNote').textContent=code?'Khách mở HANA MEDIA → Bản quyền → nhập mã này → Kích hoạt (máy cần có mạng). Mã giữ nguyên khi gia hạn.':(short?.error||'');}
$('ldRole').onchange=()=>renderPerms(licenseData.defaults[$('ldRole').value]);
$('ldPlans').onchange=e=>{licensePlan=e.target.value;updateExpiryPreview(null);};
$('ldStart').onchange=()=>updateExpiryPreview(null);
$('licenseAdd').onclick=()=>{fillLicenseForm(null);$('ldName').focus();};
$('licenseSearch').oninput=renderLicenseRows;
$('ldSave').onclick=async()=>{
  const body={id:licenseEditing,name:$('ldName').value,email:$('ldEmail').value,role:$('ldRole').value,plan:licensePlan,start:$('ldStart').value,expires:$('ldExpires').value,
    devices:$('ldDevices').value,quota:Number($('ldQuota').value)||0,amount:Number($('ldAmount').value)||0,
    permissions:[...$('ldPerms').querySelectorAll('input:checked')].map(x=>x.value)};
  const old=licenseEditing&&licenseData.dashboard.accounts.find(a=>a.id===licenseEditing);
  if(old&&old.name.trim()!==body.name.trim()&&!confirm(`Bạn đang SỬA tài khoản “${old.name}” thành “${body.name.trim()}”, KHÔNG tạo tài khoản mới.\n\nMuốn thêm khách mới: bấm Hủy rồi bấm “+ Thêm tài khoản”.\n\nVẫn đổi tên tài khoản này?`))return;
  try{const r=await api('/api/license/accounts',body);await loadLicense();
    if(old)fillLicenseForm(r.account);else fillLicenseForm(null);  // tạo xong: khung trở về "Thêm tài khoản" để nhập khách tiếp theo
    renderLicenseRows();showKey(r.key,r.account.name,r.short);toast(old?'Đã lưu thay đổi và tạo key mới.':`Đã tạo tài khoản ${r.account.name}. Sao chép key gửi cho khách.`);}catch(error){toast(error.message,true);}
};
async function renewAccount(id){
  const a=licenseData.dashboard.accounts.find(x=>x.id===id);if(!a)return;
  const plan=prompt(`Gia hạn "${a.name}" (hết hạn ${dmy(a.expires)}).\nNhập gói: 1m = 1 tháng, 1y = 1 năm, 3y = 3 năm, trial = thêm 2 ngày dùng thử`,'1y');if(plan===null)return;
  const amount=prompt('Số tiền thu (VNĐ, để 0 nếu không tính):','0');if(amount===null)return;
  try{const r=await api('/api/license/renew',{id,plan:plan.trim(),amount:Number(amount)||0});await loadLicense();fillLicenseForm(r.account);renderLicenseRows();showKey(r.key,r.account.name,r.short);toast(`Đã gia hạn tới ${dmy(r.account.expires)}. Gửi key mới cho khách.`);}catch(error){toast(error.message,true);}
}
$('ldRenew').onclick=()=>licenseEditing&&renewAccount(licenseEditing);
$('ldLock').onclick=async()=>{const a=licenseData.dashboard.accounts.find(x=>x.id===licenseEditing);if(!a)return;
  try{const r=await api('/api/license/lock',{id:a.id,locked:a.state!=='locked'});await loadLicense();fillLicenseForm(r.account);renderLicenseRows();revokeToast(r.revocation,r.account.state==='locked'?'Đã khóa tài khoản.':'Đã mở khóa.');}catch(error){toast(error.message,true);}};
async function deleteAccount(id){const a=licenseData.dashboard.accounts.find(x=>x.id===id);if(!a)return;
  if(!confirm(`Xóa tài khoản “${a.name}”?\n\nKey của tài khoản này bị thu hồi vĩnh viễn, không mở lại được (muốn dùng lại phải tạo tài khoản mới).`))return;
  try{const r=await api('/api/license/delete',{id,confirm:true});await loadLicense();if(licenseEditing===id)fillLicenseForm(null);renderLicenseRows();revokeToast(r.revocation,`Đã xóa ${a.name}.`);}catch(error){toast(error.message,true);}}
$('ldDelete').onclick=()=>licenseEditing&&deleteAccount(licenseEditing);
$('revokePublish').onclick=()=>action(async()=>{const r=await api('/api/license/publish',{});await loadLicense();r.revocation.error?toast(r.revocation.error,true):toast('Đã đăng danh sách thu hồi.');});
$('revokeTokenForm').onsubmit=e=>{e.preventDefault();const token=$('revokeToken').value.trim();if(!token)return toast('Dán token GitHub.',true);action(async()=>{const r=await api('/api/license/publisher',{token});$('revokeToken').value='';await loadLicense();r.revocation.error?toast('Đã lưu token nhưng chưa đăng được: '+r.revocation.error,true):toast('Đã lưu token và đăng danh sách thu hồi.');});};
$('revokeTokenRemove').onclick=()=>{if(!confirm('Xóa token GitHub khỏi máy này? Sau đó khóa / xóa tài khoản sẽ không đăng thu hồi online được.'))return;action(async()=>{await api('/api/license/publisher',{token:''});await loadLicense();toast('Đã xóa token.');});};
$('licenseCheck').onclick=()=>action(async()=>{const r=await api('/api/license/check',{});await loadLicense();r.check.ok?toast(r.status.message):toast('Chưa kiểm tra được: '+r.check.error,true);});
$('licenseRows').onclick=e=>{const edit=e.target.closest('[data-license-edit]'),renew=e.target.closest('[data-license-renew]'),key=e.target.closest('[data-license-key]'),del=e.target.closest('[data-license-delete]');
  if(del)return deleteAccount(del.dataset.licenseDelete);
  const find=id=>licenseData.dashboard.accounts.find(a=>a.id===id);
  if(edit){fillLicenseForm(find(edit.dataset.licenseEdit));renderLicenseRows();$('licenseDetail').scrollIntoView({behavior:'smooth',block:'start'});}
  if(renew)renewAccount(renew.dataset.licenseRenew);
  if(key){const a=find(key.dataset.licenseKey);const text=a.short_code||a.token;navigator.clipboard.writeText(text).then(()=>toast(a.short_code?`Đã sao chép mã kích hoạt ngắn của ${a.name}.`:`Đã sao chép key của ${a.name}.`)).catch(()=>{fillLicenseForm(a);showKey(a.token,a.name,{code:a.short_code||''});});}};
$('ldCopyShort').onclick=()=>navigator.clipboard.writeText($('ldShort').textContent).then(()=>toast('Đã sao chép mã kích hoạt ngắn.')).catch(()=>toast('Chọn và sao chép mã.',true));
$('ldCopyKey').onclick=()=>navigator.clipboard.writeText($('ldKey').value).then(()=>toast('Đã sao chép key.')).catch(()=>toast('Chọn và sao chép key trong ô.',true));
$('copyMachine').onclick=()=>navigator.clipboard.writeText($('licenseMachine').textContent).then(()=>toast('Đã sao chép mã máy.')).catch(()=>toast('Chọn và sao chép mã máy.',true));
document.addEventListener('click',e=>{if(e.target.id==='copyPubkey')navigator.clipboard.writeText(licenseData.status.public_key).then(()=>toast('Đã sao chép khóa công khai.')).catch(()=>toast('Chọn và sao chép chuỗi khóa.',true));});
$('licenseActivate').onsubmit=e=>{e.preventDefault();action(async()=>{const r=await api('/api/license/activate',{key:$('licenseKey').value});$('licenseKey').value='';await loadLicense();toast(r.status.message);});};
$('licenseRemove').onclick=()=>{if(!confirm('Gỡ key bản quyền khỏi máy này?'))return;action(async()=>{await api('/api/license/deactivate',{});await loadLicense();toast('Đã gỡ key.');});};
$('issuerInitButton').onclick=()=>{if(!confirm('Khởi tạo khóa Super Admin trên máy này?\n\nChỉ làm trên máy của người bán. Khóa ký được lưu trong thư mục dữ liệu: hãy sao lưu và KHÔNG gửi cho ai.'))return;
  action(async()=>{await api('/api/license/init-signing',{confirm:true});await loadLicense();toast('Đã tạo khóa Super Admin. Sao chép khóa công khai để gắn vào app.');});};

// ---------- API key: Gemini (phân tích video) và ChatGPT (viết + kiểm duyệt prompt) ----------
function renderApiCards(force){for(const engine of Object.keys(API_CARDS))renderApiCard(engine,force);renderPromptProvider();}
// Viết + kiểm duyệt prompt bằng Gemini API (miễn phí) hay ChatGPT (OpenAI API / Chrome).
function renderPromptProvider(){
  const info=state?.content?.chatgpt_api;if(!info)return;
  const gemini=info.provider==='gemini';
  for(const radio of document.querySelectorAll('input[name=promptProvider]'))radio.checked=radio.value===(gemini?'gemini':'openai');
  $('openaiBox').hidden=gemini;$('promptGeminiNote').hidden=!gemini;
  if(gemini){const g=state.content.api||{};$('chatgptBadge').textContent=g.configured?`✓ Viết prompt bằng Gemini API · ${g.model}`:'Chưa có API key Gemini';$('chatgptBadge').className='status-chip '+(g.configured?'approved':'error');}
}
document.querySelectorAll('input[name=promptProvider]').forEach(radio=>radio.onchange=async()=>{
  if(radio.value==='gemini'&&!state?.content?.api?.configured){renderPromptProvider();return toast('Nhập API key Gemini ở thẻ Gemini Pro trước.',true);}
  try{state.content.chatgpt_api=await api('/api/chatgpt-api/settings',{provider:radio.value});renderApiCards(true);
    toast(radio.value==='gemini'?'Viết và kiểm duyệt prompt sẽ dùng Gemini API (miễn phí).':'Viết và kiểm duyệt prompt sẽ dùng ChatGPT.');}
  catch(error){toast(error.message,true);}
});
function renderApiCard(engine,force){
  const c=API_CARDS[engine],p=c.p,info=state?.content?.[c.state];if(!info)return;
  // Chưa có khóa: hiện sẵn ô nhập API key (cách khuyên dùng); chọn tay thì giữ lựa chọn đó.
  const shown=apiChoice[engine]||(info.configured?info.mode:'api'),apiMode=shown==='api';
  for(const radio of document.querySelectorAll(`input[name=${p}Mode]`))radio.checked=radio.value===shown;
  $(p+'Box').hidden=!apiMode;$(p+'WebBox').hidden=apiMode;
  $(p+'Remove').hidden=!info.configured;$(p+'Test').disabled=$(p+'Models').disabled=!info.configured;
  if(!$(p+'State').dataset.busy)$(p+'State').textContent=info.configured?(info.source==='environment'?'✓ Đang dùng khóa từ biến môi trường':'✓ Đã lưu API key (mã hóa trên máy)'):'Chưa có API key';
  const select=$(p+'Model'),options=(apiModels[engine]||[]).slice();
  if(!options.some(m=>m.id===info.model))options.unshift({id:info.model,name:info.model});
  const html=options.map(m=>`<option value="${esc(m.id)}">${esc(m.name)}${m.name!==m.id?` · ${esc(m.id)}`:''}</option>`).join('');
  if(select.dataset.html!==html){select.innerHTML=html;select.dataset.html=html;}
  if(force||document.activeElement!==select)select.value=info.model;
  if(force||document.activeElement!==$(p+'Thinking'))$(p+'Thinking').value=info.thinking||'';
  if(force||document.activeElement!==$(p+'Rpm'))$(p+'Rpm').value=info.rpm??'';
  if(info.mode==='api'){$(c.badge).textContent=`✓ ${c.label} · ${info.model}`;$(c.badge).className='status-chip approved';}
  else if(apiMode){$(c.badge).textContent='Chưa có API key';$(c.badge).className='status-chip error';}
}
async function saveApi(engine,body,message){
  try{state.content[API_CARDS[engine].state]=await api(`/api/${engine}-api/settings`,body);renderApiCard(engine,true);if(message)toast(message);}
  catch(error){toast(error.message,true);}
}
async function loadApiModels(engine,quiet){
  const status=$(API_CARDS[engine].p+'State');status.dataset.busy='1';status.textContent='Đang tải danh sách model…';
  try{apiModels[engine]=(await api(`/api/${engine}-api/models`,{})).models;renderApiCard(engine,true);if(!quiet)toast(`API key dùng được ${apiModels[engine].length} model.`);}
  catch(error){if(!quiet)toast(error.message,true);}
  finally{delete status.dataset.busy;renderApiCard(engine);}
}
for(const [engine,c] of Object.entries(API_CARDS)){
  const p=c.p;
  document.querySelectorAll(`input[name=${p}Mode]`).forEach(radio=>radio.onchange=()=>{
    apiChoice[engine]=radio.value;
    if(radio.value==='api'&&!state?.content?.[c.state]?.configured){renderApiCard(engine);return toast('Dán API key rồi bấm Lưu để chuyển sang '+c.label+'.');}
    saveApi(engine,{mode:radio.value},`${c.use} sẽ dùng ${radio.value==='api'?c.label:c.web}.`);
  });
  $(p+'Model').onchange=e=>saveApi(engine,{model:e.target.value},'Đã chọn model '+e.target.value);
  $(p+'Thinking').onchange=e=>saveApi(engine,{thinking:e.target.value},'Đã lưu mức suy nghĩ.');
  $(p+'Rpm').onchange=e=>{const v=Math.round(Number(e.target.value));if(!Number.isFinite(v)||v<0||v>1000)return toast('Nhập số từ 0 đến 1000.',true);saveApi(engine,{rpm:v},v?`Tool sẽ gửi tối đa ${v} yêu cầu mỗi phút.`:'Không giới hạn số yêu cầu mỗi phút.');};
  $(p+'KeyPage').onclick=()=>api(`/api/${engine}-api/key-page`,{}).then(()=>toast('Đã mở trang lấy API key trong trình duyệt. Tạo khóa rồi dán vào đây.')).catch(error=>toast(error.message,true));
  $(p+'Save').onclick=async()=>{
    const value=$(p+'Key').value.trim();if(!value)return toast('Dán API key vào ô trước.',true);
    await saveApi(engine,{key:value,mode:'api'},`Đã lưu API key. ${c.use} sẽ dùng ${c.label}.`);
    if(state.content[c.state].configured){$(p+'Key').value='';apiChoice[engine]='';loadApiModels(engine,true);}
  };
  $(p+'Remove').onclick=()=>{if(confirm(`Xóa API key ${c.label} khỏi máy này?`)){apiChoice[engine]='';saveApi(engine,{remove:true,mode:'web'},`Đã xóa API key. ${c.use} quay lại dùng ${c.web}.`);}};
  $(p+'Models').onclick=()=>loadApiModels(engine,false);
  $(p+'Test').onclick=async()=>{
    const status=$(p+'State');status.dataset.busy='1';status.textContent='Đang gửi câu hỏi thử…';
    try{const r=await api(`/api/${engine}-api/test`,{});toast(`API key hoạt động · model ${r.model} trả lời: ${r.answer}`);}
    catch(error){toast(error.message,true);}
    finally{delete status.dataset.busy;renderApiCard(engine);}
  };
}

// ---------- Chatbot hỗ trợ (góc phải): FAQ trước, rồi trợ lý AI; hết lượt → Zalo ----------
const supportChat={history:[],busy:false,greeted:false};
function supportAdd(role,text,extra=''){
  const box=$('supportMessages'),div=document.createElement('div');div.className='support-msg '+role;
  div.innerHTML=`<div class="support-bubble">${esc(text).replace(/\n/g,'<br>')}</div>${extra}`;box.appendChild(div);box.scrollTop=box.scrollHeight;return div;
}
function supportQuota(info){
  if(!info)return;
  $('supportQuota').textContent=info.ai?`Trợ lý AI · còn ${info.remaining}/${info.limit} lượt hôm nay`:'Câu hỏi thường gặp · Zalo hỗ trợ';
}
function supportOpen(){
  $('supportPanel').hidden=false;$('supportFab').classList.add('open');supportQuota(state?.content?.support);
  if(!supportChat.greeted){supportChat.greeted=true;supportAdd('bot','Xin chào! Mình là trợ lý HANA MEDIA. Bạn cần giúp gì về tool? Có thể chọn câu hỏi nhanh bên dưới.');}
  setTimeout(()=>$('supportInput').focus(),50);
}
async function supportSend(text){
  text=String(text||'').trim();if(!text||supportChat.busy)return;
  supportChat.busy=true;$('supportInput').value='';$('supportSuggest').hidden=true;
  supportAdd('user',text);const typing=supportAdd('bot','Đang trả lời…');typing.classList.add('typing');
  try{
    const r=await api('/api/support/chat',{message:text,history:supportChat.history});typing.remove();
    const zalo=r.source==='zalo'?`<button type="button" class="support-zalo inline" data-support-zalo>Chat Zalo ${esc(r.zalo)}</button>`:'';
    supportAdd('bot',r.answer,(r.source==='faq'?'<small class="support-tag">Câu hỏi thường gặp</small>':'')+zalo);
    supportChat.history.push({role:'user',text},{role:'model',text:r.answer});supportChat.history=supportChat.history.slice(-12);
    supportQuota(r);if(state?.content)state.content.support={...state.content.support,...r};
  }catch(error){typing.remove();supportAdd('bot',error.message||'Không gửi được câu hỏi.',`<button type="button" class="support-zalo inline" data-support-zalo>Chat Zalo 0983105651</button>`);}
  finally{supportChat.busy=false;}
}
$('supportFab').onclick=()=>$('supportPanel').hidden?supportOpen():($('supportPanel').hidden=true,$('supportFab').classList.remove('open'));
$('supportClose').onclick=()=>{$('supportPanel').hidden=true;$('supportFab').classList.remove('open');};
$('supportForm').onsubmit=e=>{e.preventDefault();supportSend($('supportInput').value);};
$('supportSuggest').onclick=e=>{const b=e.target.closest('button');if(b)supportSend(b.textContent);};
document.addEventListener('click',e=>{if(e.target.closest('#supportZalo,[data-support-zalo]'))api('/api/support/zalo',{}).then(()=>toast('Đã mở Zalo hỗ trợ trong trình duyệt.')).catch(error=>toast(error.message,true));});
