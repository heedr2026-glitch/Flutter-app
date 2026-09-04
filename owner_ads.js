// Owner durations are independent of the subscriber's 1–10 day request limit.
// Keep request history, but present one institution header instead of repeating
// it for every previous upgrade request. IDs, not institution names, group rows.
if (typeof loadSubscriptionRequests === 'function') {
  loadSubscriptionRequests = async function() {
    const box = document.getElementById('subscriptionRequests');
    try {
      const response = await fetch('/owner/api/subscription-requests', {headers: headers()});
      const data = await response.json();
      if (!response.ok || !Array.isArray(data)) throw new Error(data.error || 'تعذر التحميل');
      const pendingCount = data.filter(row => row.status === 'pending').length;
      const badge = document.getElementById('subscriptionRequestBadge');
      if (badge) { badge.textContent = String(pendingCount); badge.classList.toggle('show', pendingCount > 0); }
      const groups = new Map();
      for (const row of data) {
        const key = row.organization_id ?? ('request-'+row.id);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(row);
      }
      box.innerHTML = [...groups.values()].map(rows => `<section class="card"><h3>${esc(rows[0].organization_name)}</h3><p>التواصل: ${esc(rows[0].phone)} — عدد الطلبات: ${rows.length}</p>${rows.map(x => `<details ${x.status === 'pending' ? 'open' : ''}><summary>طلب #${esc(x.id)} — ${x.requested_package === 'basic' ? 'الأساسية' : 'VIP'} — ${x.status === 'pending' ? 'بانتظار المراجعة' : x.status === 'approved' ? 'مقبول' : 'مرفوض'}</summary><p><strong>تنبيه تحويل باسم: ${esc(x.transfer_name || 'غير مسجل')}</strong></p><p>المبلغ المطلوب: ${Number(x.quoted_price||0).toFixed(2)} ر.س — المدة: ${Number(x.paid_months||0)} شهر${Number(x.bonus_months||0)>0?' + '+Number(x.bonus_months)+' مجانًا':''}</p>${x.transfer_receipt?`<p><strong>وصل التحويل:</strong></p><a href="${x.transfer_receipt}" target="_blank"><img src="${x.transfer_receipt}" alt="وصل التحويل" style="width:100%;max-height:320px;object-fit:contain;border-radius:12px;border:1px solid #38bdf8"></a>`:'<p>لم يرفق المشترك وصلًا.</p>'}<p>تاريخ الطلب: ${esc(adDisplayDate(x.created_at, 'غير محدد'))}</p><p>كود الخصم: ${esc(x.discount_code || 'بدون كود')} — الخصم: ${Number(x.discount_percent)||0}%</p>${x.status === 'pending' ? `<label for="request-package-${x.id}">الباقة المراد تفعيلها</label><select id="request-package-${x.id}"><option value="free">المجانية</option><option value="basic" ${x.requested_package === 'basic' ? 'selected' : ''}>الأساسية</option><option value="vip" ${x.requested_package === 'vip' ? 'selected' : ''}>VIP</option></select><p>عند التأكد من وصول الحوالة اضغط التفعيل؛ مدة العرض المحفوظة ستُطبق تلقائيًا.</p><button onclick="reviewSubscriptionRequest(${x.id},'approve')">تأكيد وصول التحويل وتفعيل الباقة</button><button onclick="reviewSubscriptionRequest(${x.id},'reject')">رفض الطلب</button>` : ''}</details>`).join('')}</section>`).join('') || 'لا توجد طلبات ترقية';
    } catch (error) { box.textContent = error.message || 'تعذر التحميل'; }
  };
}

let adLoadVersion = 0;
if (typeof loadUsageAlerts === 'function') {
  loadUsageAlerts = async function() {
    const box = document.getElementById('usageAlerts'), badge = document.getElementById('usageAlertBadge');
    try {
      const response = await fetch('/owner/api/organizations', {headers:headers(), cache:'no-store'}), data = await response.json();
      if (!response.ok || !Array.isArray(data)) throw new Error(data.error || 'تعذر تحميل حدود المؤسسات');
      const sorted = [...data].sort((a,b)=>(b.ai_usage_percent||0)-(a.ai_usage_percent||0));
      const important = sorted.filter(o=>o.ai_usage_status!=='normal');
      badge.textContent = String(important.length); badge.classList.toggle('show', important.length>0);
      box.innerHTML = `<div class="card"><h3>تعديل حد جميع المؤسسات</h3><p>يستبدل الحد الحالي لكل المؤسسات بالعدد الجديد.</p><input id="all-ai-limit" type="number" min="1" max="100000" value="100" placeholder="الحد اليومي لجميع المؤسسات"><button class="vip" onclick="setAllAiLimits()">تطبيق الحد على جميع المؤسسات</button></div>` + (sorted.length ? sorted.map(o=>`<div class="card" style="border-right:5px solid ${o.ai_usage_status==='danger'?'#ef4444':o.ai_usage_status==='warning'?'#facc15':'#22c55e'}"><b>${esc(o.name)}</b><p>الباقة: ${esc(o.package)} — اليوم: ${o.ai_today||0} من <strong>${o.ai_daily_limit||0}</strong> (${o.ai_usage_percent||0}%)</p><label for="usage-ai-limit-${o.id}">الحد اليومي لهذه المؤسسة</label><input id="usage-ai-limit-${o.id}" type="number" min="1" max="100000" value="${o.ai_daily_limit||1}"><button onclick="setUsageAiLimit(${o.id})">حفظ حد هذه المؤسسة</button><p>الشهر: ${o.ai_month||0} استخدام — التكلفة التقديرية: ${Number(o.ai_estimated_cost_sar||0).toFixed(2)} ر.س</p></div>`).join('') : '<p>لا توجد مؤسسات بعد.</p>');
    } catch (error) { box.textContent = error.message || 'تعذر تحميل حدود المؤسسات'; }
  };
}
async function saveOwnerAiLimit(url, dailyLimit) { const response=await fetch(url,{method:'PUT',headers:headers(),body:JSON.stringify({dailyLimit})}),result=await response.json();if(!response.ok||!result.saved)throw new Error(result.error||'تعذر حفظ الحد');return result; }
async function setUsageAiLimit(id) { const dailyLimit=Number(document.getElementById('usage-ai-limit-'+id).value);if(!Number.isSafeInteger(dailyLimit)||dailyLimit<1||dailyLimit>100000){alert('اكتب حدًا صحيحًا بين 1 و100000');return}try{await saveOwnerAiLimit('/owner/api/organizations/'+id+'/ai-limit',dailyLimit);alert('تم حفظ حد المؤسسة ✓');await loadUsageAlerts()}catch(error){alert(error.message||'تعذر حفظ الحد')} }
async function setAllAiLimits() { const dailyLimit=Number(document.getElementById('all-ai-limit').value);if(!Number.isSafeInteger(dailyLimit)||dailyLimit<1||dailyLimit>100000){alert('اكتب حدًا صحيحًا بين 1 و100000');return}if(!confirm('تطبيق الحد '+dailyLimit+' على جميع المؤسسات؟ سيُستبدل الحد الحالي لكل مؤسسة.'))return;try{const result=await saveOwnerAiLimit('/owner/api/organizations/ai-limit',dailyLimit);alert('تم تحديث '+result.updatedOrganizations+' مؤسسة ✓');await loadUsageAlerts();await loadOrganizations()}catch(error){alert(error.message||'تعذر حفظ الحد العام')} }
const ownerCategories = document.querySelector('.category-grid');
if (ownerCategories) {
  const subscriptionButton = [...ownerCategories.querySelectorAll('button')].find(button => button.textContent.includes('طلبات الترقية'));
  if (subscriptionButton && !document.getElementById('subscriptionRequestBadge')) subscriptionButton.insertAdjacentHTML('beforeend', '<b id="subscriptionRequestBadge" class="support-badge">0</b>');
  const paymentButton = document.createElement('button');
  paymentButton.className = 'category-button'; paymentButton.innerHTML = 'بيانات التحويل البنكي<span>الحساب الظاهر للمشتركين</span>';
  paymentButton.onclick = () => { showOwnerPanel('paymentSettingsPanel'); loadPaymentSettings(); };
  ownerCategories.append(paymentButton);
  const giftLink = document.createElement('a');
  giftLink.className = 'category-button'; giftLink.href = '/owner/signup-offer';
  giftLink.textContent = 'هدية أول المشتركين — العدد والباقة والأشهر';
  ownerCategories.append(giftLink);
}
const ownerDashboard = document.getElementById('ownerDashboard');
if (ownerDashboard && !document.getElementById('paymentSettingsPanel')) {
  ownerDashboard.insertAdjacentHTML('beforeend', '<section id="paymentSettingsPanel" class="owner-panel"><div class="card"><h2>الحساب البنكي لاستقبال الاشتراكات</h2><p>تظهر هذه البيانات للمشترك قبل إرسال طلب التفعيل.</p><label for="paymentBankName">اسم البنك</label><input id="paymentBankName" maxlength="120"><label for="paymentAccountName">اسم صاحب الحساب</label><input id="paymentAccountName" maxlength="160"><label for="paymentIban">الآيبان السعودي</label><input id="paymentIban" maxlength="34" dir="ltr" placeholder="SA0000000000000000000000"><label for="paymentAccountNumber">رقم الحساب (اختياري)</label><input id="paymentAccountNumber" maxlength="50" dir="ltr"><label for="paymentInstructions">تعليمات إضافية (اختياري)</label><textarea id="paymentInstructions" maxlength="500" style="box-sizing:border-box;width:100%;min-height:90px;background:#09152e;color:white;border:1px solid #285682;border-radius:10px;padding:10px"></textarea><button onclick="savePaymentSettings()">حفظ بيانات التحويل</button><div id="paymentSettingsStatus" class="result"></div></div></section>');
}
async function loadPaymentSettings(){const status=document.getElementById('paymentSettingsStatus');try{const r=await fetch('/owner/api/payment-settings',{headers:headers(),cache:'no-store'}),d=await r.json();if(!r.ok)throw new Error(d.error||'تعذر التحميل');document.getElementById('paymentBankName').value=d.bank_name||'';document.getElementById('paymentAccountName').value=d.account_name||'';document.getElementById('paymentIban').value=d.iban||'';document.getElementById('paymentAccountNumber').value=d.account_number||'';document.getElementById('paymentInstructions').value=d.instructions||'';status.textContent=d.iban?'البيانات محفوظة وتظهر للمشتركين ✓':'أدخل بيانات الحساب ثم احفظها.'}catch(error){status.textContent=error.message||'تعذر التحميل'}}
async function savePaymentSettings(){const body={bankName:document.getElementById('paymentBankName').value,accountName:document.getElementById('paymentAccountName').value,iban:document.getElementById('paymentIban').value,accountNumber:document.getElementById('paymentAccountNumber').value,instructions:document.getElementById('paymentInstructions').value};try{const r=await fetch('/owner/api/payment-settings',{method:'PUT',headers:headers(),body:JSON.stringify(body)}),d=await r.json();if(!r.ok)throw new Error(d.error||'تعذر الحفظ');document.getElementById('paymentSettingsStatus').textContent='تم حفظ بيانات التحويل وستظهر للمشتركين ✓'}catch(error){document.getElementById('paymentSettingsStatus').textContent=error.message||'تعذر الحفظ'}}
const maintenancePending = new Set();
if (typeof loadOrganizations === 'function') {
  const original = loadOrganizations;
  loadOrganizations = async function() {
    await original();
    for (const input of document.querySelectorAll('[id^="maintenance-hours-"]')) {
      if (input.previousElementSibling?.dataset.hoursLabel) continue;
      const label = document.createElement('label');
      label.dataset.hoursLabel = 'true'; label.htmlFor = input.id;
      label.textContent = 'مدة الإيقاف بالساعات: 24 = يوم، 48 = يومين. تنتهي الصيانة تلقائيًا، ويمكن تشغيل الخدمة قبلها.';
      input.before(label); input.min = '1'; input.max = '8760'; input.step = '1';
    }
  };
}
if (typeof toggleMaintenance === 'function') {
  toggleMaintenance = async function(input, id, service) {
    const key = id + ':' + service, enabled = !input.checked;
    if (maintenancePending.has(key)) return;
    const hours = Number(document.getElementById('maintenance-hours-'+id).value);
    const message = document.getElementById('maintenance-message-'+id).value;
    if (enabled && (!Number.isInteger(hours) || hours < 1 || hours > 8760)) {
      input.checked = enabled; alert('حدد مدة الإيقاف من 1 إلى 8760 ساعة'); return;
    }
    if (enabled && !confirm('إيقاف الخدمة لمدة '+hours+' ساعة؟')) { input.checked = enabled; return; }
    maintenancePending.add(key); input.disabled = true;
    try {
      const response = await fetch('/owner/api/organizations/'+id+'/maintenance', {
        method:'PUT',headers:headers(),body:JSON.stringify({service,enabled,hours:enabled?hours:24,message})
      });
      const result = await response.json();
      if (!response.ok || !result.saved) throw new Error(result.error || 'تعذر حفظ الحالة');
      const check = await fetch('/owner/api/organizations', {headers:headers(),cache:'no-store'});
      const rows = await check.json();
      if (!check.ok || !Array.isArray(rows)) throw new Error('تم الحفظ لكن تعذر التحقق؛ حدّث الصفحة');
      const row = rows.find(x => String(x.id) === String(id));
      if (!row || maintenanceActive(row[service+'_until']) !== enabled) throw new Error('تعذر تأكيد الحالة المحفوظة؛ حدّث الصفحة');
      await loadOrganizations();
      alert(enabled ? 'تم إيقاف الخدمة لمدة '+hours+' ساعة' : 'تم تشغيلها للمؤسسة. إذا كان الإيقاف العام فعالًا، شغّلها أيضًا من لوحة الأمن.');
    } catch (error) {
      input.checked = enabled; alert(error.message || 'تعذر الاتصال');
    } finally { maintenancePending.delete(key); input.disabled = false; }
  };
}
function adApprovedDays(ad) {
  if (ad.approved && ad.approved_at && ad.expires_at) {
    const duration = (new Date(ad.expires_at) - new Date(ad.approved_at)) / 86400000;
    // The server records start/end a few milliseconds apart. Round that skew,
    // and use the full approved period, never the remaining time or user cap.
    if (Number.isFinite(duration) && duration > 0) return Math.max(1, Math.round(duration));
  }
  return ad.requested_days || 10;
}
function adUnlimited(ad) { return Boolean(ad.approved && !ad.expires_at); }
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
    const counts = Object.fromEntries(Object.keys(labels).map(status => [status, data.filter(ad => ad.status === status).length]));
    const summary = '<div class="card"><h3>إعلانات المؤسسات — إجمالي '+data.length+'</h3>' + Object.keys(labels).map(status => '<span style="display:inline-block;margin:8px">'+labels[status]+': '+counts[status]+'</span>').join('') + '</div>';
    box.innerHTML = summary + (data.length ? '<p>طلبات الإعلانات — الأحدث أولًا</p>' + data.map(ad => `<div class="card">
      <b>${esc(ad.title)}</b><p>رقم طلب الإعلان: #${esc(ad.id)}</p><p>المؤسسة: ${esc(ad.organization_name)}</p>
      <p style="white-space:pre-wrap">${esc(ad.message)}</p><p>التواصل: ${esc(ad.contact)}</p>
      <p>الحالة: ${labels[ad.status] || 'غير معروفة'}</p>
      <p>طلب المشترك: ${ad.requested_days == null ? 'غير محدد (إعلان سابق)' : esc(ad.requested_days)+' أيام'}</p>
      <p>بداية العرض: <bdi>${esc(adDisplayDate(ad.approved_at, 'لم يعتمد بعد'))}</bdi></p>
      <p>النهاية المعتمدة: <bdi>${esc(adDisplayDate(ad.expires_at, ad.approved ? 'بدون نهاية' : 'لم تعتمد بعد'))}</bdi></p>
      <label for="ad-days-${ad.id}">مدة العرض من الآن بالأيام — للمالك دون حد 10 أيام</label>
      <input id="ad-days-${ad.id}" type="number" min="1" step="1" value="${adUnlimited(ad) ? '' : adApprovedDays(ad)}" ${adUnlimited(ad) ? 'disabled' : ''}>
      <small>للإعلان المعتمد تظهر مدته المحفوظة. الضغط على اعتماد يبدأ المدة المختارة من الآن.</small>
      <label style="display:flex;align-items:center;gap:8px"><input style="width:auto" type="checkbox" id="ad-unlimited-${ad.id}" ${adUnlimited(ad) ? 'checked' : ''} onchange="document.getElementById('ad-days-${ad.id}').disabled=this.checked"> عرض بدون تاريخ نهاية</label>
      <label for="ad-note-${ad.id}">ملاحظة تظهر للمشترك / سبب الرفض</label>
      <textarea style="box-sizing:border-box;width:100%;min-height:70px;background:#0b1020;color:white;border:1px solid #38bdf8;border-radius:10px;padding:10px" id="ad-note-${ad.id}" maxlength="1000">${esc(ad.review_note || '')}</textarea>
      <button onclick="reviewAd(${ad.id},'approve')">${ad.approved ? 'اعتماد المدة الجديدة ونشر' : 'قبول ونشر'}</button>
      <button class="vip" onclick="reviewAd(${ad.id},'reject')">رفض / إيقاف</button>
    </div>`).join('') : 'لا توجد إعلانات للمراجعة');
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
