/* Cross-device sync for Done / Favorites / Deleted marks (Cloudflare Worker dd-sync + KV).
   Page sets window.DD_SYNC={page:'rw'|'xbm'|'cs',keys:{done:'..',fav:'..',del:'..'},apply:fn} before loading this.
   localStorage stays the working copy (offline fallback); changes are queued and pushed; server is truth after first merge. */
(function(){
const API='https://dd-sync.sells8008.workers.dev/state',KEY='67fbf3e4c3d2c77f4967408b';
const C=window.DD_SYNC;if(!C)return;
const P=C.page,QK='dd-sync-q-'+P,IK='dd-sync-init-'+P,KINDS=Object.keys(C.keys);
const rd=k=>{try{return JSON.parse(localStorage.getItem(k)||'{}')}catch(e){return{}}};
const raw=Storage.prototype.setItem;let quiet=false;
const prev={};KINDS.forEach(k=>prev[k]=rd(C.keys[k]));
const q=()=>{try{return JSON.parse(localStorage.getItem(QK)||'[]')}catch(e){return[]}};
const setq=a=>raw.call(localStorage,QK,JSON.stringify(a));
Storage.prototype.setItem=function(k,v){raw.call(this,k,v);if(quiet||this!==localStorage)return;
 const kind=KINDS.find(x=>C.keys[x]===k);if(!kind)return;let now={};try{now=JSON.parse(v||'{}')}catch(e){}
 const ops=[];for(const id in now)if(prev[kind][id]!==now[id])ops.push({k:kind,id,v:now[id]});
 for(const id in prev[kind])if(!(id in now))ops.push({k:kind,id,v:null});
 prev[kind]=now;if(ops.length){setq(q().concat(ops));flush()}};
let busy=false;
async function call(ops){const r=await fetch(API+'?page='+P,ops?{method:'POST',headers:{'Content-Type':'application/json','X-Key':KEY},body:JSON.stringify({ops})}:{headers:{'X-Key':KEY}});if(!r.ok)throw new Error(r.status);return r.json()}
function adopt(st){quiet=true;KINDS.forEach(k=>{const m=st[k]||{};raw.call(localStorage,C.keys[k],JSON.stringify(m));prev[k]=m});quiet=false;try{C.apply&&C.apply()}catch(e){}}
async function flush(){if(busy)return;busy=true;try{const ops=q();if(!ops.length)return;const st=await call(ops);setq(q().slice(ops.length));}catch(e){}finally{busy=false}}
async function init(){try{
 if(!localStorage.getItem(IK)){const ops=[];KINDS.forEach(k=>{const m=rd(C.keys[k]);for(const id in m)ops.push({k,id,v:m[id]})});if(ops.length)setq(q().concat(ops));}
 const ops=q();const st=await call(ops.length?ops:null);setq(q().slice(ops.length));raw.call(localStorage,IK,'1');adopt(st);
}catch(e){/* offline: keep local, retry later */}}
init();window.addEventListener('online',()=>{flush().then(init)});
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')init()});
})();
