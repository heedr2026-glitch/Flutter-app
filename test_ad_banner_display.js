const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const source=fs.readFileSync('owner_dashboard.js','utf8');
const styles=[];
const intervals=new Map();let handle=0,now=0;
const fields={};
const context=vm.createContext({
 Date:class extends Date{static now(){return now}},
 document:{getElementById:id=>fields[id],createElement:()=>({}),head:{append:x=>styles.push(x.textContent)}},
 esc:value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
 bannerConfig:ad=>typeof ad.banner_config==='string'?JSON.parse(ad.banner_config):ad.banner_config||{},
 setInterval:callback=>{intervals.set(++handle,callback);return handle},
 clearInterval:id=>intervals.delete(id),setTimeout:()=>{},
 api:async()=>[],
});
vm.runInContext("let current='ads-pending',version=1,adsPublishedPreviewTimer=null,adsPublishedPreviewIndex=0;"+
 source.slice(source.indexOf('function khdoomRequestBanner('),source.indexOf('const adsTrialDurations=')),context);
for(const name of ['khdoomAdsUnifiedRatioStyle','khdoomRequestBannerIsolatedStyle']){
 const start=source.indexOf('const '+name+'=');vm.runInContext(source.slice(start,source.indexOf('\n',start)),context);
}
const image='data:image/png;base64,'+'A'.repeat(100);
for(const [label,message] of [['short','عرض خاص'],['long','نص عربي طويل '.repeat(80)]]){
 const html=context.khdoomRequestBanner({title:label,message,image_data:image,banner_config:{logoPosition:'right'}});
 assert(html.includes('khdoom-ad-layout'));
 assert(html.includes('khdoom-request-banner__logo'));
 assert(html.includes('dir="rtl"'));
 assert(!html.includes('height:'));
}
for(const dimensions of [[100,100],[300,80]]){
 const html=context.khdoomRequestBanner({title:'شعار',image_data:image,banner_config:{imageWidth:dimensions[0],imageHeight:dimensions[1]}});
 assert(html.includes('شعار الإعلان'));
 assert(!html.includes('object-fit:cover'));
}
const full=context.khdoomRequestBanner({title:'DO_NOT_OVERLAY',message:'HIDDEN_COPY',banner_config:{adType:'image',bannerImageData:image}});
assert(full.includes('khdoom-request-banner__full-image'));
assert(!full.includes('DO_NOT_OVERLAY'));
assert(!full.includes('khdoom-request-banner__copy'));
assert(!full.includes('full-shade'));
assert(!context.khdoomRequestBanner({title:'<script>x</script>',message:'<img onerror=x>'}).includes('<script>'));
const css=styles.join('\n');
assert(css.includes('aspect-ratio:3/1'));
assert(css.includes('min-height:0!important'));
assert(css.includes('flex:0 0 23cqw'));
assert(css.includes('object-fit:contain'));
assert(css.includes('-webkit-line-clamp:2'));
const ads=[{id:1,ad_source:'organization',display_seconds:3,title:'نص'},{id:1,ad_source:'platform',display_seconds:60,title:'عرض',banner_config:{adType:'image',bannerImageData:image}},{id:3,display_seconds:8,title:'ثالث'}];
for(const [time,index] of [[0,0],[2999,0],[3000,1],[62999,1],[63000,2],[71000,0]])assert.equal(context.adsClockIndex(ads,time),index);
assert.equal(context.adDisplaySeconds(1),3);assert.equal(context.adDisplaySeconds(99),60);
let inserts=0;
fields.adsPublishedRotator={firstElementChild:null,insertAdjacentHTML(){inserts++},lastElementChild:{classList:{add(){}}}};
fields.adsPublishedCounter={textContent:''};
context.adsPublishedStripMarkup(ads);context.startAdsPublishedPreview(ads);
now=3000;[...intervals.values()][0]();assert.equal(fields.adsPublishedCounter.textContent,'2 / 3');assert.equal(inserts,1);
now=63000;[...intervals.values()][0]();assert.equal(fields.adsPublishedCounter.textContent,'3 / 3');
context.startAdsPublishedPreview(ads);assert.equal(intervals.size,1);
assert(source.includes("published=await api('ads/live')"));
// Isolate trial helpers, then verify +/- trials never issue a network mutation.
let writes=0;context.api=async()=>{writes++;return {}};
vm.runInContext(source.slice(source.indexOf('const adsTrialDurations='),source.indexOf("document.addEventListener('input',e=>",source.indexOf('const adsTrialDurations='))),context);
fields.adsSinglePreview={innerHTML:'',isConnected:true};fields.adsTrialSeconds={value:8};fields.adsTrialCountdown={textContent:''};
assert.equal(context.updateAdsTrialDuration(ads[0],11),11);
assert.equal(fields.adsTrialSeconds.value,11);assert.equal(writes,0);
assert.equal(context.updateAdsTrialDuration(ads[0],99),60);
context.stopAdsSingleTrial();
console.log('Advertisement rendering, RTL, image-only, shared size, duration trial and rotation tests passed.');
