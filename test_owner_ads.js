const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
let reviewMovedFirst = false;
const panel = {prepend: card => { assert.equal(card, reviewCard); reviewMovedFirst = true; }};
const reviewCard = {parentElement: panel};
const fields = {
  ads: {innerHTML:'',textContent:'',closest: () => reviewCard},
  adsPanel: panel,
  'ad-days-1': {value:'40'},
  'ad-unlimited-1': {checked:false},
  'ad-note-1': {value:'ملاحظة'},
};
const writes = [];
const savedAd = {status:'expired',requested_days:7,approved:true,
  approved_at:'2025-12-17T00:00:00.574Z',expires_at:'2026-01-01T00:00:00Z'};
const context = vm.createContext({
  document: {getElementById: id => fields[id]},
  headers: () => ({'X-Owner-Key':'test-only'}),
  esc: value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  alert: () => {}, confirm: () => true,
  fetch: async (url, options) => {
    if (options.method === 'PUT') {
      const body = JSON.parse(options.body);
      writes.push(body);
      savedAd.status = 'published';
      savedAd.approved_at = '2026-09-03T05:12:18.073Z';
      savedAd.expires_at = body.durationDays === null ? null :
        new Date(Date.parse(savedAd.approved_at) + body.durationDays * 86400000 - 574).toISOString();
    }
    return {ok:true,json:async () => options.method === 'PUT' ? {saved:true} : [{
      id:1,title:'<script>unsafe</script>',organization_name:'Test',message:'Test',
      ...savedAd,review_note:'</textarea>',
    }, {id: 3, title: 'NEWEST REQUEST', status:'pending', requested_days:10},
    {id: 2, title: 'SECOND REQUEST', status:'pending', requested_days:2}]};
  },
});
vm.runInContext(fs.readFileSync('owner_ads.js','utf8'), context);
(async () => {
  await context.loadAds();
  assert(reviewMovedFirst);
  assert(fields.ads.innerHTML.indexOf('NEWEST REQUEST') < fields.ads.innerHTML.indexOf('SECOND REQUEST'));
  assert(fields.ads.innerHTML.indexOf('SECOND REQUEST') < fields.ads.innerHTML.indexOf('&lt;script&gt;unsafe'));
  assert(fields.ads.innerHTML.includes('7 أيام'));
  assert(fields.ads.innerHTML.includes('انتهى الإعلان'));
  assert(!fields.ads.innerHTML.includes('<script>unsafe'));
  assert(fields.ads.innerHTML.includes('&lt;/textarea&gt;'));
  assert.match(fields.ads.innerHTML, /id="ad-days-1"[^>]*value="15"/);
  assert.equal(context.adApprovedDays({approved:true, approved_at:'bad', expires_at:'bad', requested_days:6}), 6);
  assert.equal(context.adApprovedDays({approved:false, requested_days:3}), 3);
  await context.reviewAd(1,'approve');
  assert.equal(writes.at(-1).durationDays,40);
  assert.match(fields.ads.innerHTML, /id="ad-days-1"[^>]*value="40"/);
  fields['ad-days-1'].value='15';
  await context.reviewAd(1,'approve');
  await context.loadAds();
  assert.equal(writes.at(-1).durationDays,15);
  assert.match(fields.ads.innerHTML, /id="ad-days-1"[^>]*value="15"/);
  fields['ad-days-1'].value='2';
  await context.reviewAd(1,'approve');
  assert.equal(writes.at(-1).durationDays,2);
  assert.match(fields.ads.innerHTML, /id="ad-days-1"[^>]*value="2"/);
  fields['ad-unlimited-1'].checked=true;
  await context.reviewAd(1,'approve');
  assert.equal(writes.at(-1).durationDays,null);
  assert.match(fields.ads.innerHTML, /id="ad-unlimited-1" checked/);
  assert.match(fields.ads.innerHTML, /id="ad-days-1"[^>]*disabled/);
  fields['ad-unlimited-1'].checked=false;
  fields['ad-days-1'].value='0';
  await context.reviewAd(1,'approve');
  assert.equal(writes.length,4);
  console.log('Owner advertisement UI tests passed');
})().catch(error => { console.error(error); process.exitCode=1; });
