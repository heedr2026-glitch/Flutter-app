'use strict';
// صفحة «فيديو الإعلانات»: نفس ترتيب صفحة الإعلانات (طلب جديد / منشور / مرفوض) بالإضافة للمنتهي والإعدادات.
(function(){
 const at=tree.findIndex(x=>x[0]==='الإعلانات');
 tree.splice(at<0?tree.length-1:at+1,0,['🎬 فيديو الإعلانات','video-ads','ads']);
})();
let videoAdTab='pending';
const videoAdUrls=new Map();
const videoAdTabs=[['pending','طلب جديد'],['published','منشور'],['rejected','مرفوض'],['expired','منتهي'],['sounds','🎵 الأصوات العامة'],['settings','⚙️ الإعدادات']];
const videoAdSize=b=>b>1048576?(b/1048576).toFixed(1)+' ميقا':Math.round((b||0)/1024)+' كيلو';
const previousRenderAddons=window.renderAddons||(typeof renderAddons==='function'?renderAddons:null);
window.renderAddons=async function(route,v){
 if(route!=='video-ads')return previousRenderAddons?previousRenderAddons(route,v):false;
 const c=$('content'),valid=()=>v===version;
 if(videoAdTab==='sounds'){
  const d=await api('video-ads/sounds'),counts=(await api('video-ads?status=pending')).counts;if(!valid())return true;
  c.innerHTML='';c.append(videoAdTabBar(counts));c.append(videoAdSoundsPanel(d.items||[]));return true;
 }
 if(videoAdTab==='settings'){
  const d=await api('video-ads/settings'),counts=(await api('video-ads?status=pending')).counts;if(!valid())return true;
  c.innerHTML='';c.append(videoAdTabBar(counts));c.append(videoAdSettingsPanel(d));return true;
 }
 const d=await api('video-ads?status='+videoAdTab+'&page='+(currentPage||1));if(!valid())return true;
 c.innerHTML='';c.append(videoAdTabBar(d.counts));
 videoAdBadge(d.counts.pending);
 if(!d.items.length){c.insertAdjacentHTML('beforeend',note({pending:'لا توجد طلبات جديدة.',published:'لا توجد فيديوهات منشورة حاليًا.',rejected:'لا توجد طلبات مرفوضة.',expired:'لا توجد إعلانات منتهية.'}[videoAdTab]));return true}
 const list=document.createElement('div');list.className='cards';list.style.gridTemplateColumns='repeat(auto-fill,minmax(280px,1fr))';
 for(const x of d.items)list.append(videoAdCard(x,d.durations));
 c.append(list);
 if(d.total>d.pageSize){const pages=Math.ceil(d.total/d.pageSize),bar=document.createElement('div');bar.className='toolbar';for(let p=1;p<=pages;p++){const b=action(String(p),()=>{currentPage=p;render()});if(p===(currentPage||1))b.classList.add('primary');bar.append(b)}c.append(bar)}
 return true;
};
function videoAdTabBar(counts){
 const top=document.createElement('div');top.style.marginBottom='10px';top.append(action('➕ مقطع خدوم ✔️',openOfficialComposer,'primary'));
 const wrap=document.createElement('div');wrap.append(top);
 const bar=document.createElement('div');bar.className='toolbar';bar.style.marginBottom='16px';wrap.append(bar);
 for(const [key,label] of videoAdTabs){const n=counts&&key in counts?counts[key]:null;const b=action(label+(n!==null?` (${n})`:''),()=>{videoAdTab=key;currentPage=1;render()});if(key===videoAdTab)b.classList.add('primary');bar.append(b)}
 return wrap;
}
function videoAdBadge(n){const b=document.querySelector('#nav [data-route="video-ads"]');if(b)b.textContent='🎬 فيديو الإعلانات'+(n?` (${n})`:'')}
function videoAdCard(x,durations){
 const card=document.createElement('div');card.className='panel';
 const star=x.is_official?' ✔️':x.package==='vip'?' ⭐':'';
 card.innerHTML=`<div data-player style="background:#0b1020;border-radius:12px;aspect-ratio:9/16;max-height:420px;display:flex;align-items:center;justify-content:center;margin-bottom:10px;overflow:hidden"><button type="button" data-play>▶️ تشغيل الفيديو</button></div>
 <h3 style="margin:6px 0">${x.pinned?'📌 ':''}${esc(x.organization_name)}${star}</h3>
 ${x.title?`<p><b>العنوان:</b> ${esc(x.title)}</p>`:''}
 ${x.overlay_text?`<p><b>النص على الفيديو:</b> ${esc(x.overlay_text)}</p>`:''}
 ${x.caption?`<p><b>الوصف:</b> ${esc(x.caption)}</p>`:''}
 <p class="muted">النشاط: ${esc(x.activity||'—')} · المدة: ${Math.round(x.seconds||0)} ث · الحجم: ${videoAdSize(x.size_bytes)} · التنزيل: ${x.allow_download?'مسموح':'مقفول'} · ❤️ ${x.likes||0}</p>
 <p class="muted">أُرسل: ${when(x.submitted_at)}${x.published_at?' · نُشر: '+when(x.published_at):''}${x.expires_at?' · ينتهي: '+when(x.expires_at):''}${x.duration_label?' ('+esc(x.duration_label)+')':''}</p>
 ${x.is_official?'<p class="muted">مقطع رسمي من خدوم</p>':x.paid?`<p style="color:#b45309;font-weight:bold">💳 إعلان مدفوع ${Number(x.price_sar||0).toFixed(0)} ريال — التحويل باسم: ${esc(x.transfer_name||'غير مكتوب')}</p>`:'<p class="muted">من رصيد VIP الشهري</p>'}
 ${x.review_note?`<p style="color:#b32635"><b>سبب الرفض:</b> ${esc(x.review_note)}</p>`:''}
 <div data-actions></div>`;
 card.querySelector('[data-play]').onclick=()=>videoAdPlay(x.id,card.querySelector('[data-player]'));
 const box=card.querySelector('[data-actions]');
 if(x.status==='pending'){
  box.innerHTML=`<label style="display:block;font-weight:bold;margin:8px 0 4px">مدة الإعلان</label><div class="toolbar" data-durations>${durations.map(d=>`<label style="border:1px solid var(--line);border-radius:9px;padding:8px 12px;cursor:pointer"><input type="radio" name="dur-${x.id}" value="${d.key}" ${d.key==='1w'?'checked':''}> ${esc(d.label)}</label>`).join('')}</div>${x.paid?`<label class="check" style="display:block;margin:8px 0"><input type="checkbox" data-paid> تأكدت من وصول التحويل</label>`:''}<div class="toolbar" style="margin-top:10px"></div>`;
  const row=box.querySelector('.toolbar:last-child');
  row.append(action('✅ موافقة ونشر',async()=>{const key=box.querySelector(`input[name="dur-${x.id}"]:checked`)?.value;try{await api('video-ads/'+x.id,'PUT',{action:'approve',duration:key,paymentConfirmed:!!box.querySelector('[data-paid]')?.checked});flash('تم نشر الإعلان ✓');render()}catch(e){flash(e.message)}},'primary'));
  row.append(action('❌ رفض',()=>openRejectDialog(x),'danger'));
 }
 if(x.is_official&&(x.status==='published'||x.status==='expired'))box.append(action('✏️ تعديل',()=>openOfficialEditor(x)));
 if(x.status==='published'){
  box.append(action('⏹️ إيقاف الإعلان',async()=>{if(!confirm('إيقاف الإعلان الحين؟ يبقى محفوظًا عند المشترك.'))return;try{await api('video-ads/'+x.id,'PUT',{action:'stop'});flash('تم إيقاف الإعلان');render()}catch(e){flash(e.message)}},'danger'));
 }
 return card;
}
async function videoAdPlay(id,holder){
 holder.innerHTML='<p class="muted" style="color:#cbd5e1">جارٍ تحميل الفيديو…</p>';
 try{
  let url=videoAdUrls.get(id);
  if(!url){const r=await fetch('/owner/api/v2/video-ads/'+id+'/file',{headers:authHeaders(),cache:'no-store'});if(!r.ok){let m='تعذر تحميل الفيديو';try{m=(await r.json()).error||m}catch(_){}throw Error(m)}url=URL.createObjectURL(await r.blob());videoAdUrls.set(id,url)}
  holder.innerHTML='';const video=document.createElement('video');video.src=url;video.controls=true;video.autoplay=true;video.playsInline=true;video.style.cssText='width:100%;height:100%;object-fit:contain;background:#000';holder.append(video);
 }catch(e){holder.innerHTML=`<p style="color:#fecaca;padding:12px">${esc(e.message)}</p>`}
}
function videoAdSettingsPanel(d){
 const box=document.createElement('div');
 const used=d.storage_used_bytes||0;
 box.innerHTML=`<div class="panel"><h3>إعدادات إعلانات الفيديو</h3>
 <p class="muted">أي تغيير هنا يطبق على التطبيق فورًا بدون نسخة جديدة.</p>
 <div class="formgrid">
  <label>المدة القصوى للفيديو (ثانية)<input data-k="max_seconds" type="number" min="5" max="600" value="${d.max_seconds}"></label>
  <label>VIP: عدد الإعلانات المجانية بالشهر<input data-k="quota_vip" type="number" min="0" max="1000" value="${d.quota_vip}"></label>
  <label>الأساسية: عدد الإعلانات المجانية بالشهر<input data-k="quota_basic" type="number" min="0" max="1000" value="${d.quota_basic}"></label>
  <label>المجانية: عدد الإعلانات المجانية بالشهر<input data-k="quota_free" type="number" min="0" max="1000" value="${d.quota_free}"></label>
  <label>سعر الإعلان الواحد لغير VIP (ريال)<input data-k="paid_price_sar" type="number" min="0" step="1" value="${d.paid_price_sar}"></label>
  <label>أقصى فيديوهات محفوظة لكل مؤسسة<input data-k="max_saved" type="number" min="1" max="100" value="${d.max_saved}"></label>
  <label>أقصى حجم للرفع (ميقا)<input data-k="max_upload_mb" type="number" min="1" max="500" value="${d.max_upload_mb}"></label>
 </div>
 <label class="check" style="display:block;margin:10px 0"><input data-k="enabled" type="checkbox" ${d.enabled?'checked':''}> إعلانات الفيديو شغالة</label>
 <label class="check" style="display:block;margin:10px 0"><input data-k="sounds_enabled" type="checkbox" ${d.sounds_enabled?'checked':''}> 🎵 ميزة الأصوات شغالة (إنشاء صوت من مقطع، وإضافة الصوت على الفيديو والصورة)</label>
 <p class="muted">مساحة الفيديوهات المستخدمة على السيرفر: <b>${videoAdSize(used)}</b></p>
 <div class="toolbar"></div></div>
 <div class="panel"><h3>حد خاص لمؤسسة</h3><p class="muted">اكتب رقم المؤسسة وعدد إعلاناتها بالشهر. اتركه فاضي ليرجع لحد باقتها.</p>
 <div class="formgrid"><label>رقم المؤسسة<input data-q="org" type="number" min="1"></label><label>عدد الإعلانات بالشهر<input data-q="limit" type="number" min="0" max="1000" placeholder="فاضي = حسب الباقة"></label></div>
 <div class="toolbar" data-qbar></div>
 ${(d.overrides||[]).length?table(d.overrides,[['المؤسسة',x=>esc(x.organization_name)+' (#'+x.organization_id+')'],['الباقة',x=>pkg[x.package]||esc(x.package)],['الحد الشهري',x=>x.monthly_limit],['آخر تعديل',x=>when(x.updated_at)]]):'<p class="muted">لا توجد حدود خاصة.</p>'}</div>`;
 box.querySelector('.toolbar').append(action('حفظ الإعدادات',async()=>{const body={};box.querySelectorAll('[data-k]').forEach(i=>{body[i.dataset.k]=i.type==='checkbox'?(i.checked?1:0):i.value});try{await api('video-ads/settings','PUT',body);flash('تم حفظ الإعدادات ✓');render()}catch(e){flash(e.message)}},'primary'));
 box.querySelector('[data-qbar]').append(action('حفظ الحد الخاص',async()=>{const org=box.querySelector('[data-q="org"]').value,limit=box.querySelector('[data-q="limit"]').value.trim();if(!org){flash('اكتب رقم المؤسسة');return}try{await api('video-ads/quota/'+org,'PUT',{limit:limit===''?null:limit});flash(limit===''?'رجعت المؤسسة لحد باقتها':'تم حفظ الحد الخاص ✓');render()}catch(e){flash(e.message)}}));
 return box;
}
function videoAdSoundsPanel(items){
 const box=document.createElement('div');
 if(!items.length){box.innerHTML=note('لا توجد أصوات عامة بعد.');return box}
 box.insertAdjacentHTML('beforeend','<p class="muted">الأصوات اللي اختار أصحابها تكون عامة لكل المشتركين. أوقف أي صوت فيه أغنية بحقوق أو غير لائق.</p>');
 const list=document.createElement('div');list.className='cards';list.style.gridTemplateColumns='repeat(auto-fill,minmax(260px,1fr))';
 for(const x of items){
  const card=document.createElement('div');card.className='panel';if(x.hidden)card.style.opacity='.6';
  card.innerHTML=`<p class="muted" style="margin:0">${esc(x.organizationName)}</p><h3 style="margin:6px 0">🎵 ${esc(x.name)}</h3><p style="margin:0 0 8px">▶️ ${Number(x.uses||0).toLocaleString('en')} استخدام · ${Math.round(x.seconds||0)} ث ${x.hidden?'· <b style="color:#b32635">موقوف</b>':''}</p><div data-audio></div><div class="toolbar"></div>`;
  const bar=card.querySelector('.toolbar'),holder=card.querySelector('[data-audio]');
  bar.append(action('▶️ استماع',async()=>{holder.textContent='جارٍ التحميل…';try{const r=await fetch('/owner/api/v2/video-ads/sounds/'+x.id+'/file',{headers:authHeaders(),cache:'no-store'});if(!r.ok)throw Error('تعذر تحميل الصوت');const a=document.createElement('audio');a.controls=true;a.autoplay=true;a.style.width='100%';a.src=URL.createObjectURL(await r.blob());holder.replaceChildren(a)}catch(e){holder.textContent=e.message}}));
  bar.append(action(x.hidden?'✅ إعادة تشغيل':'⛔ إيقاف الصوت',async()=>{if(!x.hidden&&!confirm('إيقاف هذا الصوت؟ يختفي من الأصوات العامة ولا أحد يقدر يستخدمه.'))return;try{await api('video-ads/sounds/'+x.id,'PUT',{hidden:!x.hidden});flash(x.hidden?'رجع الصوت ✓':'تم إيقاف الصوت');render()}catch(e){flash(e.message)}},x.hidden?'':'danger'));
  list.append(card);
 }
 box.append(list);return box;
}
function videoAdDialog(title,html){
 const dlg=document.createElement('dialog');dlg.style.cssText='max-width:560px;width:94vw;border:0;border-radius:16px;padding:0';
 dlg.innerHTML=`<div style="padding:20px;max-height:86vh;overflow:auto" dir="rtl"><h2 style="margin-top:0">${title}</h2>${html}<p data-error class="error" hidden></p><div class="toolbar" data-buttons style="margin-top:16px"></div></div>`;
 document.body.append(dlg);dlg.addEventListener('close',()=>dlg.remove());dlg.showModal();
 const err=m=>{const e=dlg.querySelector('[data-error]');e.textContent=m;e.hidden=!m};
 return {dlg,err,buttons:dlg.querySelector('[data-buttons]'),close:()=>dlg.close()};
}
const officialDurations=[['1h','ساعة'],['1d','يوم'],['1w','أسبوع'],['1m','شهر'],['forever','دايم']];
function officialFields(x={},withDuration=true,keepOption=false){
 return `<div class="formgrid">
 <label>النص على الفيديو<input data-f="overlayText" maxlength="120" value="${esc(x.overlay_text||'')}" placeholder="مثال: عرض اليوم 🔥"></label>
 <label>العنوان<input data-f="title" maxlength="80" value="${esc(x.title||'')}"></label>
 <label>الوصف<textarea data-f="caption" maxlength="300">${esc(x.caption||'')}</textarea></label>
 <label>مكان النص<select data-f="overlayY"><option value="0.15">فوق</option><option value="0.5">بالنص</option><option value="0.75">تحت</option></select></label>
 </div>
 ${withDuration?`<p style="font-weight:bold;margin:12px 0 6px">المدة</p><div class="toolbar">${keepOption?'<label style="border:1px solid var(--line);border-radius:9px;padding:8px 12px"><input type="radio" name="off-dur" value="" checked> بدون تغيير</label>':''}${officialDurations.map(([k,l])=>`<label style="border:1px solid var(--line);border-radius:9px;padding:8px 12px"><input type="radio" name="off-dur" value="${k}" ${!keepOption&&k==='forever'?'checked':''}> ${l}</label>`).join('')}</div>`:''}
 <label class="check" style="display:block;margin:10px 0"><input type="checkbox" data-f="pinned" ${x.pinned?'checked':''}> 📌 تثبيت أول (يطلع أول مقطع للكل)</label>
 <label class="check" style="display:block;margin:10px 0"><input type="checkbox" data-f="allowDownload" ${x.allow_download===0?'':'checked'}> السماح بالتنزيل</label>`;
}
function readOfficial(dlg){
 const v=k=>dlg.querySelector(`[data-f="${k}"]`);
 return {overlayText:v('overlayText').value.trim(),title:v('title').value.trim(),caption:v('caption').value.trim(),overlayY:Number(v('overlayY').value),pinned:v('pinned').checked,allowDownload:v('allowDownload').checked,duration:dlg.querySelector('input[name="off-dur"]:checked')?.value||''};
}
async function openOfficialComposer(){
 let sounds=[];try{sounds=(await api('video-ads/all-sounds')).items||[]}catch(_){}
 const m=videoAdDialog('➕ مقطع خدوم ✔️',`<label style="display:block;font-weight:bold">الفيديو أو الصورة<input type="file" data-file accept="video/*,image/*"></label>
 <div data-preview style="margin:10px 0;max-height:320px;overflow:hidden;border-radius:12px;background:#0b1020"></div>
 ${officialFields()}
 <label style="display:block;margin-top:8px">🎵 الصوت<select data-sound><option value="">بدون (صوت الفيديو الأصلي)</option>${sounds.map(x=>`<option value="${x.id}">${esc(x.name)} — ${esc(x.organizationName||'خدوم')} (▶️ ${x.uses})</option>`).join('')}</select></label>
 <p data-progress class="muted"></p>`);
 const fileInput=m.dlg.querySelector('[data-file]'),preview=m.dlg.querySelector('[data-preview]');
 fileInput.onchange=()=>{const f=fileInput.files[0];preview.innerHTML='';if(!f)return;const url=URL.createObjectURL(f);const el=document.createElement(f.type.startsWith('image/')?'img':'video');el.src=url;el.style.cssText='width:100%;max-height:320px;object-fit:contain';if(el.tagName==='VIDEO')el.controls=true;preview.append(el)};
 const publish=action('🚀 نشر الحين',async()=>{
  const f=fileInput.files[0];if(!f){m.err('اختار فيديو أو صورة');return}
  const type=f.type||'video/mp4';if(!type.startsWith('video/')&&!type.startsWith('image/')){m.err('الملف لازم فيديو أو صورة');return}
  const body={...readOfficial(m.dlg),contentType:type==='image/jpg'?'image/jpeg':type};const sid=m.dlg.querySelector('[data-sound]').value;if(sid)body.soundId=Number(sid);
  publish.disabled=true;m.err('');const prog=m.dlg.querySelector('[data-progress]');
  try{
   const draft=await api('video-ads/official','POST',body);
   await new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('PUT',draft.uploadUrl);const h=authHeaders();for(const k in h)xhr.setRequestHeader(k,h[k]);xhr.setRequestHeader('Content-Type',body.contentType);xhr.upload.onprogress=e=>{if(e.lengthComputable)prog.textContent='جاري الرفع… '+Math.round(e.loaded*100/e.total)+'%'+(e.loaded===e.total?' — جاري التجهيز…':'')};xhr.onload=()=>{let d={};try{d=JSON.parse(xhr.responseText||'{}')}catch(_){}xhr.status<300?resolve(d):reject(Error(d.error||'تعذر الرفع'))};xhr.onerror=()=>reject(Error('انقطع الاتصال'));xhr.send(f)});
   m.close();flash('تم نشر مقطع خدوم ✓');videoAdTab='published';render();
  }catch(e){m.err(e.message);publish.disabled=false;prog.textContent=''}
 },'primary');
 m.buttons.append(publish,action('إلغاء',()=>m.close()));
}
function openOfficialEditor(x){
 const m=videoAdDialog('✏️ تعديل مقطع خدوم',officialFields(x,true,true));
 const sel=m.dlg.querySelector('[data-f="overlayY"]');sel.value=String(Number(x.overlay_y)>0.6?0.75:Number(x.overlay_y)>0.3?0.5:0.15);
 const save=action('حفظ التعديل',async()=>{const body=readOfficial(m.dlg);if(!body.duration)delete body.duration;save.disabled=true;try{await api('video-ads/official/'+x.id,'PUT',body);m.close();flash('تم حفظ التعديل ✓');render()}catch(e){m.err(e.message);save.disabled=false}},'primary');
 m.buttons.append(save,action('إلغاء',()=>m.close()));
}
const rejectReasons=['الفيديو أو الصورة غير واضح','فيه أغنية أو صوت بحقوق','محتوى غير لائق','معلومات التواصل ناقصة'];
function openRejectDialog(x){
 const m=videoAdDialog('❌ رفض الطلب',`<p class="muted">${esc(x.organization_name)} — السبب يوصل للمشترك في «المكتبة» وبإشعار.</p>
 ${rejectReasons.map((r,i)=>`<label style="display:block;margin:6px 0"><input type="radio" name="rej" value="${esc(r)}" ${i===0?'checked':''}> ${esc(r)}</label>`).join('')}
 <label style="display:block;margin:6px 0"><input type="radio" name="rej" value="other"> سبب ثاني: <input data-other style="width:60%"></label>
 <label style="display:block;margin-top:10px">ملاحظة إضافية (اختياري)<textarea data-extra maxlength="200"></textarea></label>`);
 const send=action('إرسال الرفض',async()=>{
  let reason=m.dlg.querySelector('input[name="rej"]:checked')?.value||'';if(reason==='other')reason=m.dlg.querySelector('[data-other]').value.trim();
  const extra=m.dlg.querySelector('[data-extra]').value.trim();if(!reason){m.err('اكتب السبب');return}
  send.disabled=true;try{await api('video-ads/'+x.id,'PUT',{action:'reject',note:reason+(extra?' — '+extra:'')});m.close();flash('تم رفض الطلب');render()}catch(e){m.err(e.message);send.disabled=false}
 },'danger');
 m.buttons.append(send,action('إلغاء',()=>m.close()));
}
// شارة الطلبات الجديدة بجانب الزر في القائمة.
setInterval(()=>{if(typeof me!=='undefined'&&me&&can('ads'))api('video-ads?status=pending').then(d=>videoAdBadge(d.counts.pending)).catch(()=>{})},60000);
