const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const fields = {
  ads: {innerHTML:'',textContent:''},
  'ad-days-1': {value:'40'},
  'ad-unlimited-1': {checked:false},
  'ad-note-1': {value:'ملاحظة'},
};
const writes = [];
const context = vm.createContext({
  document: {getElementById: id => fields[id]},
  headers: () => ({'X-Owner-Key':'test-only'}),
  esc: value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  alert: () => {}, confirm: () => true,
  fetch: async (url, options) => {
    if (options.method === 'PUT') writes.push(JSON.parse(options.body));
    return {ok:true,json:async () => options.method === 'PUT' ? {saved:true} : [{
      id:1,title:'<script>unsafe</script>',organization_name:'Test',message:'Test',
      status:'expired',requested_days:7,approved:true,expires_at:'2026-01-01',review_note:'</textarea>',
    }]};
  },
});
vm.runInContext(fs.readFileSync('owner_ads.js','utf8'), context);
(async () => {
  await context.loadAds();
  assert(fields.ads.innerHTML.includes('7 أيام'));
  assert(fields.ads.innerHTML.includes('انتهى الإعلان'));
  assert(!fields.ads.innerHTML.includes('<script>unsafe'));
  assert(fields.ads.innerHTML.includes('&lt;/textarea&gt;'));
  await context.reviewAd(1,'approve');
  assert.equal(writes.at(-1).durationDays,40);
  fields['ad-days-1'].value='2';
  await context.reviewAd(1,'approve');
  assert.equal(writes.at(-1).durationDays,2);
  fields['ad-unlimited-1'].checked=true;
  await context.reviewAd(1,'approve');
  assert.equal(writes.at(-1).durationDays,null);
  fields['ad-unlimited-1'].checked=false;
  fields['ad-days-1'].value='0';
  await context.reviewAd(1,'approve');
  assert.equal(writes.length,3);
  console.log('Owner advertisement UI tests passed');
})().catch(error => { console.error(error); process.exitCode=1; });
