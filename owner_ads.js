// Owner durations are independent of the subscriber's 1–10 day request limit.
let adLoadVersion = 0;
function adDisplayDate(value, fallback) {
  if (!value) return fallback;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? fallback : new Intl.DateTimeFormat('ar-SA-u-ca-gregory', {dateStyle:'medium',timeStyle:'short'}).format(date);
}

if (typeof showOwnerPanel === 'function') {
  const originalShowOwnerPanel = showOwnerPanel;
  showOwnerPanel = function(id) {
    originalShowOwnerPanel(id);
    if (id === 'adsPanel') loadAds();
  };
}

async function loadAds() {
  const box = document.getElementById('ads');
  const version = ++adLoadVersion;
  const panel = document.getElementById('adsPanel');
  const reviewCard = box.closest('.card');
  if (panel && reviewCard && reviewCard.parentElement === panel) {
    panel.prepend(reviewCard);
  }
  try {
    const response = await fetch('/owner/api/ads', {headers: headers()});
    const data = await response.json();
    if (!response.ok || !Array.isArray(data)) throw new Error(data.error || 'تعذر تحميل الإعلانات');
    if (version !== adLoadVersion) return;
    data.sort((a,b) => Number(b.id) - Number(a.id));
    const labels = {pending:'قيد المراجعة', published:'منشور', rejected:'مرفوض', expired:'انتهى الإعلان', paused:'متوقف'};
    box.innerHTML = data.length ? '<p>طلبات الإعلانات — الأحدث أولًا</p>' + data.map(ad => `<div class="card">
      <b>${esc(ad.title)}</b><p>المؤسسة: ${esc(ad.organization_name)}</p>
      <p style="white-space:pre-wrap">${esc(ad.message)}</p><p>التواصل: ${esc(ad.contact)}</p>
      <p>الحالة: ${labels[ad.status] || 'غير معروفة'}</p>
      <p>طلب المشترك: ${ad.requested_days == null ? 'غير محدد (إعلان سابق)' : esc(ad.requested_days)+' أيام'}</p>
      <p>بداية العرض: <bdi>${esc(adDisplayDate(ad.approved_at, 'لم يعتمد بعد'))}</bdi></p>
      <p>النهاية المعتمدة: <bdi>${esc(adDisplayDate(ad.expires_at, ad.approved ? 'بدون نهاية' : 'لم تعتمد بعد'))}</bdi></p>
      <label for="ad-days-${ad.id}">مدة العرض من الآن بالأيام — للمالك دون حد 10 أيام</label>
      <input id="ad-days-${ad.id}" type="number" min="1" step="1" value="${ad.requested_days || 10}">
      <label style="display:flex;align-items:center;gap:8px"><input style="width:auto" type="checkbox" id="ad-unlimited-${ad.id}" onchange="document.getElementById('ad-days-${ad.id}').disabled=this.checked"> عرض بدون تاريخ نهاية</label>
      <label for="ad-note-${ad.id}">ملاحظة تظهر للمشترك / سبب الرفض</label>
      <textarea style="box-sizing:border-box;width:100%;min-height:70px;background:#0b1020;color:white;border:1px solid #38bdf8;border-radius:10px;padding:10px" id="ad-note-${ad.id}" maxlength="1000">${esc(ad.review_note || '')}</textarea>
      <button onclick="reviewAd(${ad.id},'approve')">${ad.approved ? 'اعتماد المدة الجديدة ونشر' : 'قبول ونشر'}</button>
      <button class="vip" onclick="reviewAd(${ad.id},'reject')">رفض / إيقاف</button>
    </div>`).join('') : 'لا توجد إعلانات للمراجعة';
  } catch (error) { if (version === adLoadVersion) box.textContent = error.message || 'تعذر تحميل الإعلانات'; }
}

async function reviewAd(id, action) {
  const unlimited = document.getElementById('ad-unlimited-'+id).checked;
  const days = Number(document.getElementById('ad-days-'+id).value);
  if (action === 'approve' && !unlimited && (!Number.isSafeInteger(days) || days < 1)) {
    alert('اكتب عدد أيام صحيحًا أكبر من صفر أو اختر بدون نهاية'); return;
  }
  if (!confirm(action === 'approve' ? 'اعتماد المدة المختارة بدءًا من الآن ونشر الإعلان؟' : 'رفض الإعلان وإيقاف عرضه؟')) return;
  try {
    const response = await fetch('/owner/api/ads/'+id, {
      method:'PUT', headers:headers(),
      body:JSON.stringify({action, durationDays:unlimited ? null : days,
        reviewNote:document.getElementById('ad-note-'+id).value.trim()})
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'تعذر تحديث الإعلان');
    alert(action === 'approve' ? 'تم اعتماد المدة ونشر الإعلان' : 'تم رفض الإعلان');
    await loadAds();
  } catch (error) { alert(error.message || 'تعذر الاتصال'); }
}
