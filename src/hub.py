"""
IGNITION HUB — renders the phone dashboard as one static HTML file.

Design constraints: must be readable half-asleep on a phone, zero external
assets (works from any static host, loads instantly, no CDN, no tracking),
auto-refreshes itself, and looks like an instrument, not a website.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os

from .intel import edge_verdict, ext_of, rot_of
from .tape import TAPE_HTML
from .util import NY, fmt_big

TAG_HEX = {"bold magenta": "#e879f9", "magenta": "#e879f9", "cyan": "#22d3ee",
           "bold yellow": "#facc15", "yellow": "#fde047", "bold red": "#f87171",
           "red": "#f87171", "green": "#4ade80", "bold green": "#4ade80"}
STATE_HEX = {"IGNITING": "#f87171", "NEW MONEY": "#e879f9", "RUNNING": "#facc15",
             "CHURN": "#22d3ee", "FADING": "#fb923c", "LEAVING": "#ef4444",
             "COOLING": "#9ca3af", "QUIET": "#4b5563", "OPEN DRIVE": "#fde047",
             "PM HOT": "#4ade80", "PRE-OPEN": "#4b5563",
             "PM IGNITION": "#ff5a1f", "AH IGNITION": "#ff5a1f"}
INTERESTING = ["LEAVING", "FADING", "IGNITING", "NEW MONEY", "CHURN",
               "OPEN DRIVE", "PM HOT", "RUNNING"]

CSS = """
:root{--bg:#0b0d10;--card:#14181d;--edge:#242a31;--tx:#e7e9ec;--dim:#8b939c;
--mut:#5b636c;--hot:#ff5a1f;--ok:#4ade80;--bad:#f87171}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font:15px/1.45 -apple-system,BlinkMacSystemFont,
"Segoe UI",Roboto,sans-serif;padding:14px 12px 60px;max-width:640px;margin:0 auto;
-webkit-font-smoothing:antialiased}
h1{font-size:20px;letter-spacing:.06em}h1 s{color:var(--hot);text-decoration:none}
.sub{color:var(--dim);font-size:12px;margin-top:2px}
h2{font-size:11px;letter-spacing:.14em;color:var(--dim);margin:22px 2px 8px;
text-transform:uppercase}
.card{background:var(--card);border:1px solid var(--edge);border-radius:12px;
padding:10px 12px;margin-bottom:8px}
.row{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.tk{font-weight:700;font-size:16px}
.px{color:var(--dim);font-size:13px}
.up{color:var(--ok)}.dn{color:var(--bad)}
.chip{display:inline-block;font-size:10.5px;font-weight:700;letter-spacing:.04em;
padding:2px 7px;border-radius:999px;border:1px solid}
.note{color:var(--dim);font-size:12.5px;margin-top:4px}
.bar{height:5px;border-radius:3px;background:#20262d;margin-top:7px;overflow:hidden}
.bar i{display:block;height:100%;background:linear-gradient(90deg,#7a3010,var(--hot))}
.score{font-weight:800;font-size:15px}
.meta{display:flex;gap:14px;color:var(--dim);font-size:12px;margin-top:5px;flex-wrap:wrap}
.meta b{color:var(--tx);font-weight:600}
.rot{display:flex;gap:8px;flex-wrap:wrap;margin:4px 0 2px}
.ev{font-size:12.5px;color:var(--dim);padding:3px 2px;border-bottom:1px solid var(--edge)}
.ev:last-child{border:none}
.ev b{color:var(--tx)}
.quiet{color:var(--mut);font-size:12px;margin-top:6px}
.audit{display:flex;gap:10px;overflow-x:auto;padding-bottom:4px}
.audit .card{min-width:118px;flex:0 0 auto;text-align:center}
.big{font-size:19px;font-weight:800}
.foot{color:var(--mut);font-size:11px;margin-top:26px;line-height:1.6}
.stale{background:#3a1d12;border:1px solid #7a3010;color:#ffb08c;border-radius:10px;
padding:8px 12px;font-size:12.5px;margin-bottom:10px}
.tapelink{display:block;margin:12px 0 0;padding:11px 14px;border-radius:12px;
background:linear-gradient(135deg,#2a1408,#1b0f08);border:1px solid #7a3010;
color:#ffb08c;font-size:13.5px;font-weight:700;text-decoration:none}
.pulse{margin:12px 0 6px}
.pulse input{width:100%;background:var(--card);border:1px solid var(--edge);
border-radius:12px;color:var(--tx);font-family:inherit;font-size:16px;
font-weight:700;letter-spacing:.1em;padding:12px 14px;outline:none;
text-transform:uppercase}
.pulse input:focus{border-color:var(--hot)}
.pulse input::placeholder{color:var(--mut);font-weight:400;letter-spacing:.02em;
text-transform:none}
#pr{margin-top:8px}
.wed{margin:2px 0 8px}
.addrow{display:flex;gap:8px;margin:2px 0 6px}
.addrow input{flex:1;background:var(--card);border:1px solid var(--edge);
border-radius:10px;color:var(--tx);font-family:inherit;font-size:16px;
font-weight:700;letter-spacing:.1em;padding:10px 12px;outline:none;
text-transform:uppercase;min-width:0}
.addrow input:focus{border-color:var(--hot)}
.addrow button{background:var(--card);border:1px solid var(--edge);
border-radius:10px;color:var(--tx);font-family:inherit;font-size:14px;
padding:10px 14px;font-weight:700}
.addrow button.go{background:var(--hot);border-color:var(--hot);color:#fff}
.wrm{margin-left:auto;color:#5b636c;font-weight:700;padding:0 4px}
.klab{color:#8b939c;font-size:11px;letter-spacing:.1em;text-transform:uppercase;
margin:12px 2px 4px}
.vhead{margin-left:8px;text-shadow:0 0 14px currentColor}
.card.sym{background:linear-gradient(180deg,#171c22,#12161b);
box-shadow:0 2px 10px rgba(0,0,0,.35)}
.meta,.score,.px,.last{font-variant-numeric:tabular-nums}
.spk{width:100%;height:44px;margin-top:6px;display:block;opacity:.95}
@keyframes cflash{30%{box-shadow:0 0 0 3px var(--hot)}}
.card.flash{animation:cflash .6s}
@keyframes vpulse{50%{opacity:.5}}
.vhead.hot{animation:vpulse 1.5s infinite}
.hb{font-size:11px;margin-left:6px;font-variant-numeric:tabular-nums}
.mre summary{color:#5b636c;font-size:11px;letter-spacing:.1em;
text-transform:uppercase;cursor:pointer;margin-top:4px;list-style:none}
.mre summary::before{content:'▸ '}
.mre[open] summary::before{content:'▾ '}
"""

JS = """
const el=document.getElementById('ago');
if(el){const t=Date.parse(el.dataset.ts);const f=()=>{const m=Math.max(0,
Math.round((Date.now()-t)/60000));el.textContent=m<1?'just now':m+' min ago';
if(m>=45)document.getElementById('stale')?.removeAttribute('hidden')};
f();setInterval(f,20000)}
/* PULSE lookup */
let PULSE=null,PTS=0;
fetch('pulse.json?'+Date.now()).then(r=>r.ok?r.json():null).then(j=>{
if(!j||!j.rows)return;PTS=Date.parse(j.ts)||0;PULSE={};
for(const r of j.rows)PULSE[r[0]]={last:r[1],d:r[2],dol:r[3],pace:r[4],
rng:r[5],offh:r[6],heat:r[7],sw:r[8],st:r[9],fs:r[10]};
const q=document.getElementById('pq');if(q&&q.value)pshow(q.value);
}).catch(()=>{});
function pverdict(x){
if(!x)return['COLD','#5b636c','no meaningful tape today'];
const ad=Math.abs(x.d||0);
if(x.heat!=null){
if(x.st==='FADING'||x.st==='LEAVING')
return['MONEY LEAVING','#fb923c','was hot \u2014 participation is exiting'];
if(x.heat>=70)return['HOT','#ff5a1f',
(x.sw?x.sw+' swings \u2014 ':'')+'real travel, still moving'];
if(x.heat>=40)return['WARM','#facc15','moving on real tape'];
if(x.heat>=15)return['COOLING','#8b939c','travel is stalling'];
return['DEAD','#5b636c','printed, but not tradable'];}
if((x.pace||0)>=1.3&&ad>=.15)
return['HOT','#ff5a1f','big move on elevated participation'];
if((x.pace||0)>=1.2&&(ad>=.07||(x.rng||0)>=.12))
return['WARM','#facc15','elevated tape with real travel'];
if((x.pace||0)>=3&&ad<.03&&(x.rng||0)<.05)
return['CHURN','#8b939c','volume without travel'];
return['COLD','#5b636c','nothing unusual today'];}
function pshow(v){
const out=document.getElementById('pr');if(!out)return;
v=(v||'').trim().toUpperCase().replace(/[^A-Z0-9.\\-]/g,'').slice(0,6);
if(!v){out.innerHTML='';return}
if(!PULSE){out.innerHTML='<div class="card note">pulse comes alive with the '+
'live shift (7a\u20137p ET weekdays)</div>';return}
const x=PULSE[v],r=pverdict(x),age=PTS?Math.round((Date.now()-PTS)/60000):null;
let m='';
if(x){m='<div class="meta"><b>'+((x.d||0)*100).toFixed(1)+'%</b>'+
'<span>$'+(x.dol>=1e6?(x.dol/1e6).toFixed(1)+'M':Math.round((x.dol||0)/1e3)+'k')+
' iex</span>'+(x.pace!=null?'<span>'+x.pace+'x pace</span>':'')+
(x.rng!=null?'<span>range '+(x.rng*100).toFixed(0)+'%</span>':'')+
(x.st?'<span>'+x.st+'</span>':'')+(x.fs?'<span>since '+x.fs+'</span>':'')+
'</div>'+(x.offh!=null&&x.offh<=-.25&&(x.d||0)>0?
'<div class="note">well off the high \u2014 the top may already be in</div>':'');}
out.innerHTML='<div class="card"><div class="row"><span class="tk">'+v+'</span>'+
'<span class="score" style="color:'+r[1]+'">'+r[0]+'</span>'+
(x&&x.last?'<span class="px">'+x.last+'</span>':'')+
(x&&x.heat!=null?'<span class="px">heat '+x.heat+'</span>':'')+
'</div>'+m+'<div class="note">'+r[2]+
(age!=null&&age>12?' \u00b7 reading is '+age+'m old':'')+'</div></div>';}
document.getElementById('pq')?.addEventListener('input',e=>pshow(e.target.value));
/* ================= MY NAMES: on-page editor + live refresh =================
   The list is edited HERE, on the phone. Local edits render instantly from
   the pulse feed; a one-time GitHub fine-grained token (localStorage ONLY,
   sent ONLY to api.github.com) lets the page write watchlist.txt so the
   engine's full intel follows within ~2 minutes. Explicit sync states —
   never a silent failure. */
const WREPO='ALANKK11/ignition',WFILE='watchlist.txt';
const $w=i=>document.getElementById(i);
let WV=null,WLOC=null,WDIRTY=localStorage.hub_dirty==='1',WPT=null;
try{WLOC=JSON.parse(localStorage.hub_w)}catch(e){}
const wsan=t=>(t||'').toUpperCase().replace(/[^A-Z0-9.\\-]/g,'').slice(0,6);
const wesc=s=>(''+s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function wlist(){return WLOC?WLOC:((WV&&WV.tickers)||[])}
function wsave(){localStorage.hub_w=JSON.stringify(WLOC);
 localStorage.hub_dirty=WDIRTY?'1':'0'}
function wstat(m,c){const e=$w('wst');if(e){e.textContent=m;e.style.color=c||'#8b939c'}}
function wadd(t){t=wsan(t);if(!t)return;const l=wlist().slice();
 if(l.indexOf(t)>=0)return;if(!WLOC)WLOC=l;WLOC.push(t);WDIRTY=true;wsave();
 wrender();wpush()}
function wrm(t){if(!WLOC)WLOC=wlist().slice();WLOC=WLOC.filter(x=>x!==t);
 WDIRTY=true;wsave();wrender();wpush()}
const MOODC={'MONEY HERE':'#4ade80','COOLING':'#facc15','MONEY LEAVING':'#fb923c',
 'DEAD':'#f87171','STALLED':'#8b939c','WARMING':'#8b939c','NO TAPE':'#5b636c','THIN TAPE':'#5b636c'};
const DILC={'FRESH PAPER':'#f87171','S-1 PENDING':'#facc15','OPEN SHELF':'#fb923c','CLEAN':'#4ade80'};
const STC={'IGNITING':'#f87171','NEW MONEY':'#e879f9','RUNNING':'#facc15','CHURN':'#22d3ee',
 'FADING':'#fb923c','LEAVING':'#ef4444','COOLING':'#9ca3af','QUIET':'#4b5563',
 'OPEN DRIVE':'#fde047','PM HOT':'#4ade80','PRE-OPEN':'#4b5563'};
const wchip=(t,c)=>'<span class="chip" style="color:'+c+';border-color:'+c+'55;background:'+c+'14">'+wesc(t)+'</span>';
function whm(h){if(h==null)return'';const n=Math.max(0,Math.min(5,Math.round(h/20)));
 const col=h>=70?'#f87171':h>=40?'#facc15':'#5b636c';
 return '<span style="color:'+col+';letter-spacing:1px;font-size:13px">'+
  '▰'.repeat(n)+'▱'.repeat(5-n)+'</span><span class="px" style="margin-left:4px">'+Math.round(h)+'</span>'}
const wfb=x=>x>=1e6?('$'+(x/1e6).toFixed(1)+'M'):x>=1e3?('$'+Math.round(x/1e3)+'k'):('$'+Math.round(x||0));
function wcard(r){
 const t=r.ticker,dp=r.day_pct,up=(dp||0)>=0,mood=r.mood,ev=r.ev;
 const hc=MOODC[mood]||(up?'#4ade80':'#f87171');
 let chips='';
 if(mood)chips+=wchip(mood,MOODC[mood]||'#9ca3af');
 if(r.state)chips+=wchip(r.state,STC[r.state]||'#9ca3af');
 if(r.ssr)chips+=wchip('SSR','#fb923c');
 if(r.halts&&r.halts.n)chips+=wchip('×'+r.halts.n+' halt'+(r.halts.n>1?'s':'')+
  (r.halts.res?' · res '+r.halts.res:''),r.halts.n>=3?'#f87171':'#facc15');
 if(r.dil)chips+=wchip(r.dil,DILC[r.dil]||'#9ca3af');
 if(r.ped&&r.ped.n)chips+=wchip(r.ped.grade,r.ped.n>=3?'#f87171':'#facc15');
 if(r.shape)chips+=wchip(r.shape,'#22d3ee');
 if(r.rot)chips+=wchip('rot '+r.rot+'x','#e879f9');
 if(r.headline)chips+=wchip('PR '+(r.pr_ts||''),'#ff5a1f');
 const meta=[];
 if(dp!=null)meta.push('day '+(dp>=0?'+':'')+(dp*100).toFixed(0)+'%');
 if(r.dollars)meta.push(wfb(r.dollars)+' iex');
 if(r.vs_vwap!=null)meta.push((r.vs_vwap>=0?'above':'BELOW')+' vwap');
 if(r.off_hi!=null)meta.push(Math.round(r.off_hi*100)+'% off high');
 const last=(typeof r.last==='number')?r.last.toFixed(3):'—';
 let more='<div class="note" style="color:#5b636c;font-size:11px">engine research · as of '+((WV&&WV.ts)?WV.ts.slice(11,16):'—')+' ET · not live</div>';
 if(ev)more+='<div class="note" style="margin-top:4px"><b style="color:'+ev[1]+'">'+wesc(ev[0])+'</b><span style="color:#8b939c"> — '+wesc(ev[2])+'</span></div>';
 if(r.playbook)more+='<div class="note" style="color:#facc15;font-size:12.5px">'+wesc(r.playbook)+'</div>';
 if(r.reason)more+='<div class="note" style="color:#8b939c;font-style:italic">'+wesc(r.reason)+'</div>';
 if(r.headline)more+='<div class="note" style="color:#c9ced4">📰 '+wesc(r.headline)+'</div>';
 if(r.dossier)more+=r.dossier.split('</summary>')[1].split('</details>')[0];
 return '<div class="card sym" id="wc_'+t+'" style="border-left:3px solid '+hc+'">'+
  '<div class="row"><span class="tk" style="font-size:18px">'+wesc(t)+'</span>'+
  '<span class="vhead" id="lvh_'+t+'" style="font-weight:800;font-size:15px;letter-spacing:.03em;color:'+hc+'">'+wesc(mood||'…')+'</span>'+
  '<span class="px" style="margin-left:auto">'+last+'</span>'+
  '<span class="hb" id="hb_'+t+'" title="seconds since last print">●</span>'+
  '<span class="wrm" data-t="'+t+'">×</span></div>'+
  '<canvas class="spk" id="sp_'+t+'" width="600" height="76"></canvas>'+
  '<div class="note" id="lv_'+t+'" style="font-size:13px;color:#5b636c"></div>'+
  '<div class="note" id="nw_'+t+'" style="font-size:12.5px;color:#c9ced4;display:none"></div>'+
  '<details class="mre"><summary>more</summary>'+more+'</details></div>'}
function wmini(t){
 const x=PULSE?PULSE[t]:null;const v=x?pverdict(x):null;const hc=v?v[1]:'#5b636c';
 return '<div class="card sym" id="wc_'+t+'" style="border-left:3px solid '+hc+'">'+
  '<div class="row"><span class="tk" style="font-size:18px">'+wesc(t)+'</span>'+
  '<span class="vhead" id="lvh_'+t+'" style="font-weight:800;font-size:15px;color:'+hc+'">'+wesc(v?v[0]:'…')+'</span>'+
  '<span class="px" style="margin-left:auto">'+((x&&x.last)?x.last:'')+'</span>'+
  '<span class="wrm" data-t="'+t+'">×</span></div>'+
  '<div class="note" id="lv_'+t+'" style="font-size:13px;color:#5b636c">live: tap ⚙ keys once for the per-second read</div>'+
  '<canvas class="spk" id="sp_'+t+'" width="600" height="76"></canvas>'+
  '<div class="note" style="font-size:12.5px">'+(v?wesc(v[2])+' · ':'')+
  (WDIRTY?'waiting for sync — engine intel follows':'engine picks it up next tick (≤2 min in shift hours)')+'</div></div>'}
function wrender(){
 const root=$w('wroot');if(!root)return;
 const l=wlist();
 if(!l.length){root.innerHTML='<div class="card note">no names yet — type a ticker above. '+
  'It renders instantly from the live pulse feed; with sync set up the engine follows '+
  'with full EDGAR/halt/fade intel within ~2 minutes.</div>';return}
 const by={};if(WV&&WV.rows)WV.rows.forEach(r=>{by[r.ticker]=r});
 const open={};root.querySelectorAll('.card details[open]').forEach(d=>{
  const c=d.closest('.card');if(c&&c.id)open[c.id]=1});
 root.innerHTML=l.map(t=>{const r=by[t];
  return (r&&r.present)?wcard(r):wmini(t)}).join('');
 Object.keys(open).forEach(id=>{const c=$w(id);
  const d=c&&c.querySelector('details');if(d)d.open=true});
 if(typeof paintCard==='function'){const nw=Date.now();
  l.slice(0,8).forEach(t=>paintCard(t,nw))}}
function wtokv(){return localStorage.gh_t||''}
function wpush(){clearTimeout(WPT);WPT=setTimeout(wpushNow,1500)}
function wpushNow(retried){
 if(!WDIRTY)return;
 const tk=wtokv();
 if(!tk){wstat('saved on this phone — tap sync once to let the engine follow your list','#facc15');return}
 wstat('syncing to engine…','#8b939c');
 const url='https://api.github.com/repos/'+WREPO+'/contents/'+WFILE;
 const hdr={Authorization:'Bearer '+tk,Accept:'application/vnd.github+json'};
 fetch(url,{headers:hdr}).then(r=>{
  if(r.status===404)return null;
  if(!r.ok)throw new Error('read '+r.status);
  return r.json()})
 .then(j=>{
  const body='# IGNITION watchlist - edited from the hub. One ticker per line.\\n'+
   wlist().join('\\n')+'\\n';
  const p={message:'watchlist: from hub',content:btoa(body)};
  if(j&&j.sha)p.sha=j.sha;
  return fetch(url,{method:'PUT',headers:hdr,body:JSON.stringify(p)})})
 .then(r=>{
  if(r&&r.status===409&&!retried)return wpushNow(true);
  if(!r||!r.ok)throw new Error('write '+(r?r.status:'?'));
  WDIRTY=false;wsave();
  wstat('engine list updated ✓ — full intel lands within ~2 min','#4ade80')})
 .catch(e=>{const m=''+e;
  wstat(/401|403/.test(m)?'sync failed — token rejected. Tap sync and re-enter it.'
   :'sync failed ('+m.replace('Error: ','')+') — retrying on the next refresh','#f87171')})}
function wpoll(){
 const q='?'+Date.now();
 fetch('watch.json'+q).then(r=>r.ok?r.json():null).then(j=>{
  if(!j||j.v!==4)return;
  WV=j;
  const e=$w('wts');if(e&&j.ts)e.textContent=j.ts.slice(11,16)+' ET';
  if(!WDIRTY&&WLOC&&JSON.stringify(j.tickers)===JSON.stringify(WLOC)){
   WLOC=null;delete localStorage.hub_w}   // converged — engine is canonical
  wrender()}).catch(()=>{});
 fetch('pulse.json'+q).then(r=>r.ok?r.json():null).then(j=>{
  if(!j||!j.rows)return;PTS=Date.parse(j.ts)||0;PULSE={};
  for(const r of j.rows)PULSE[r[0]]={last:r[1],d:r[2],dol:r[3],pace:r[4],
   rng:r[5],offh:r[6],heat:r[7],sw:r[8],st:r[9],fs:r[10]};
  wrender()}).catch(()=>{});
 if(WDIRTY&&wtokv())wpushNow();}
$w('wqb')&&($w('wqb').onclick=()=>{wadd($w('wq').value);$w('wq').value=''});
$w('wq')&&$w('wq').addEventListener('keydown',e=>{if(e.key==='Enter'){
 wadd($w('wq').value);$w('wq').value=''}});
/* ⚙ keys — ONE panel for every key, enter once and forget. Values live in
   this browser's localStorage only (shared with TAPE, so either page's
   entry covers both). A public page cannot read repo secrets — that is why
   the phone needs its own copy of the same values. */
function fsDraw(){const p=localStorage.feed_pref||'auto';const e=$w('feedsel');
 if(e)e.textContent='feed: '+p.toUpperCase()+(p==='auto'?' — fullest your plan allows':'')}
$w('wkeys')&&($w('wkeys').onclick=()=>{const s=$w('wsetup');
 s.hidden=!s.hidden;                 // ALWAYS reopenable — re-entry never locked
 if(!s.hidden){const kk=lkeys();
  $w('akey').placeholder=kk[0]?'Alpaca key — saved ✓ (paste to replace)':'Alpaca API key';
  $w('asec').placeholder=kk[1]?'Alpaca secret — saved ✓ (paste to replace)':'Alpaca secret';
  $w('wtok').placeholder=wtokv()?'GitHub token — saved ✓ (paste to replace)':'github_pat_…';
  fsDraw()}
 if(wtokv()&&WDIRTY)wpushNow()});
$w('feedsel')&&($w('feedsel').onclick=()=>{const seq=['auto','sip','iex'];
 const p=localStorage.feed_pref||'auto';
 localStorage.feed_pref=seq[(seq.indexOf(p)+1)%3];
 delete localStorage.feed_sip;fsDraw()});
$w('ksave')&&($w('ksave').onclick=()=>{
 const k=$w('akey').value.trim(),s2=$w('asec').value.trim(),g=$w('wtok').value.trim();
 const fh=$w('fhkey').value.trim();
 if(k)localStorage.tape_k=k;
 if(s2)localStorage.tape_s=s2;
 if(g)localStorage.gh_t=g;
 if(fh){localStorage.fh_k=fh;FDELAYED=false;ftries=0;
  try{fws&&fws.close()}catch(e){};fws=null;setTimeout(fconnect,300)}
 $w('akey').value='';$w('asec').value='';$w('wtok').value='';$w('fhkey').value='';
 $w('wsetup').hidden=true;
 wstat('keys saved on this phone ✓','#4ade80');
 if(g){WDIRTY=true;wsave();wpushNow()}
 if(k||s2){delete localStorage.feed_sip}   // re-probe SIP with the new keys
 ltries=0;try{lws&&lws.close()}catch(e){};lws=null;setTimeout(lconnect,200)});
$w('wroot')&&$w('wroot').addEventListener('click',e=>{
 const x=e.target.closest('.wrm');if(x)wrm(x.dataset.t)});
if(WDIRTY)wstat('unsynced local edits — syncing…','#facc15');
wpoll();setInterval(wpoll,45000);
/* ============ LIVE STRIP: the last-30-seconds read, on-device ============
   The commit lane physically cannot answer "did the volume die in the last
   30 seconds" (HANDOFF item 20). This can: it opens the same free IEX
   stream TAPE uses, with the SAME keys already saved on this phone
   (localStorage is shared across pages on this site — nothing new is
   stored, nothing leaves the phone), and paints one line per card, every
   second: dollars in the last 30s against the name's own best 30s burst
   of the last 10 minutes, with an explicit downshift call. Alpaca allows
   ONE stream connection — if TAPE is open somewhere, this strip says so
   instead of silently showing nothing. */
/*LIVE-BEGIN*/
function liveRead(buf,nowMs){
 var d30=0,dPrev=0,best30=0,lastMs=0,first=nowMs,i,bk={},b30=0,s30=0;
 for(i=buf.length-1;i>=0;i--){var a=buf[i],ag=nowMs-a[0];
  if(ag>600000)break;
  if(ag<=30000){d30+=a[1];if(a[3]>0)b30+=a[1];else if(a[3]<0)s30+=a[1]}
  else if(ag<=60000)dPrev+=a[1];
  if(a[0]>lastMs)lastMs=a[0];
  if(a[0]<first)first=a[0];
  var k=Math.floor(ag/30000);bk[k]=(bk[k]||0)+a[1];}
 for(var k2 in bk)if(bk[k2]>best30)best30=bk[k2];
 return {d30:d30,dPrev:dPrev,best30:best30,b30:b30,s30:s30,
  bshare:(b30+s30)>0?b30/(b30+s30):null,
  lastAgo:lastMs?(nowMs-lastMs)/1000:1e9,warm:(nowMs-first)/1000};
}
/* top-of-book pressure — the free proxy for "sell orders queuing": when
   one side of the NBBO stacks 2.5x+ over the other, say so. Ratio only —
   unit-independent, so lot-size conventions can't lie to us. */
function bookLine(qb,nowMs){
 if(!qb||!qb.bs||!qb.as||nowMs-qb.t>15000)return '';
 var r=qb.as/qb.bs;
 if(r>=2.5)return ' · book: ask '+r.toFixed(1)+'× stacked — sellers queuing';
 if(r<=0.4)return ' · book: bid '+(1/r).toFixed(1)+'× stacked — buyers queuing';
 return '';
}
function liveState(s){
 if(s.warm<45)return 'WARM';
 if(s.lastAgo>=30)return 'SILENT';
 var r=s.best30>0?s.d30/s.best30:1;
 if(r>=0.55)return 'FLOW';
 if(r>=0.25)return 'MID';
 return 'DRY';
}
var LVRANK={FLOW:3,MID:2,DRY:1,SILENT:0,WARM:2};
function lvSticky(store,key,cand,nowMs){
 var s=store[key];
 if(!s){store[key]=s={cur:cand,cand:cand,since:nowMs};return cand}
 if(cand===s.cur){s.cand=cand;s.since=nowMs;return s.cur}
 if(cand!==s.cand){s.cand=cand;s.since=nowMs;return s.cur}
 var worse=LVRANK[cand]<LVRANK[s.cur];
 if(nowMs-s.since>=(worse?4000:8000)){s.cur=cand;s.since=nowMs}
 return s.cur;
}
function liveLabel(st,s,qb,nowMs){
 var F=x=>x>=1e6?('$'+(x/1e6).toFixed(1)+'M'):x>=1e3?('$'+(x/1e3).toFixed(1)+'k'):('$'+Math.round(x||0));
 var down=s.dPrev>0&&s.d30<s.dPrev*0.5;
 var pc=s.best30>0?Math.round(s.d30/s.best30*100):null;
 var side=(s.bshare!=null&&(s.b30+s.s30)>0)?
  (' · ▲'+F(s.b30)+' buy / ▼'+F(s.s30)+' sell ('+Math.round(s.bshare*100)+'% buyers)'):'';
 var bk=bookLine(qb,nowMs||0);
 if(st==='WARM')return ['live: reading the stream…','#5b636c'];
 if(st==='SILENT')return ['live: NO PRINTS for '+Math.round(s.lastAgo)+'s'+bk,'#f87171'];
 if(st==='FLOW')return ['live: '+F(s.d30)+'/30s'+
  (pc!=null?' ('+pc+'% of its best burst)':'')+side+bk,'#4ade80'];
 if(st==='MID')return ['live: '+F(s.d30)+'/30s — '+pc+'% of its burst'+
  (down?' · DOWNSHIFT':'')+side+bk,'#facc15'];
 return ['live: drying up — '+F(s.d30)+'/30s vs '+F(s.best30)+' burst'+
  (down?' · DOWNSHIFT':'')+side+bk,'#fb923c'];
}
/* THE word (item 41): derived from the cumulative money line's slope over
   the last ~4 minutes — an accumulating read at minute scale, so it can
   bend but never flicker. Sparse sample says THIN FEED, never a verdict. */
function cvdWord(buf,nowMs){
 var cum=0,c4=null,tot=0,n5=0,last=0,first=nowMs,i;
 for(i=0;i<buf.length;i++){var x=buf[i];if(nowMs-x[0]>600000)continue;
  if(x[0]<first)first=x[0];
  cum+=x[3]>0?x[1]:x[3]<0?-x[1]:0;tot+=x[1];
  if(nowMs-x[0]>240000)c4=cum;
  if(nowMs-x[0]<=300000)n5++;
  if(x[0]>last)last=x[0];}
 if(c4===null)c4=0;
 if(!last||nowMs-first<45000)return ['reading…','#5b636c',0];
 if(nowMs-last>=60000)return ['NO PRINTS '+Math.round((nowMs-last)/1000)+'s','#8b939c',0];
 if(n5<6||tot<3000)return ['THIN FEED · '+n5+' prints/5m','#5b636c',0];
 var sl=(cum-c4)/Math.max(tot,1);
 if(sl>=0.12)return ['ACCUMULATING','#4ade80',sl];
 if(sl<=-0.12)return ['DISTRIBUTION','#f87171',sl];
 return ['BALANCED','#facc15',sl];
}
/* the big word — trade language, side-aware: WHOSE money is it right now */
function liveHead(st,s){
 var down=s.dPrev>0&&s.d30<s.dPrev*0.5,b=s.bshare;
 if(st==='WARM')return ['reading…','#5b636c'];
 if(st==='SILENT')return ['STALLED','#f87171'];
 if(st==='FLOW'){
  if(b!=null&&b>=0.65)return ['BUYERS IN','#4ade80'];
  if(b!=null&&b<=0.35)return ['SELLERS HITTING','#f87171'];
  return ['MONEY IN','#4ade80'];}
 if(st==='MID')return [b!=null&&b<=0.35?'SELLERS · EASING':(down?'EASING ↓':'EASING'),'#facc15'];
 return [down?'DRAINING ↓':'DRAINING','#fb923c'];  // DRY
}
/* rank his names by who's hottest RIGHT NOW — state first (relative to each
   name's own burst), dollar flow breaks ties. His "relative to the others". */
function liveScore(st,s){return (LVRANK[st]||0)*1e12+(s.d30||0);}
/*LIVE-END*/
var LBUF={},LVS={},lws=null,ltries=0,lsubs=[],LFEED='iex';
function lvnote(m,c){const e=$w('lvst');if(e){e.textContent=m;e.style.color=c||'#5b636c'}}
function lkeys(){return [localStorage.tape_k||'',localStorage.tape_s||'']}
function lmkt(){const d=new Date(new Date().toLocaleString('en-US',{timeZone:'America/New_York'}));
 const h=d.getHours();return d.getDay()>0&&d.getDay()<6&&h>=4&&h<20}
/* ACCURACY: SIP is the full consolidated tape (every US exchange); IEX is a
   ~3% sample. Auto mode probes SIP with his keys and falls back to IEX the
   moment Alpaca says his plan doesn't include it — cached, so it costs one
   reconnect ever, and re-probed whenever keys change or he flips the pref. */
function lfeed(){const p=localStorage.feed_pref||'auto';
 if(p==='sip')return 'sip';
 if(p==='iex')return 'iex';
 return localStorage.feed_sip==='0'?'iex':'sip'}
function lNoSip(){localStorage.feed_sip='0';
 lvnote('SIP not in your Alpaca plan — using IEX sample (upgrade Alpaca and it switches itself)','#facc15');
 try{lws&&lws.close()}catch(e){};lws=null;setTimeout(lconnect,300)}
function lsub(){
 if(!lws||lws.readyState!==1)return;
 const want=wlist().slice(0,8);
 const drop=lsubs.filter(t=>want.indexOf(t)<0),add=want.filter(t=>lsubs.indexOf(t)<0);
 try{if(drop.length)lws.send(JSON.stringify({action:'unsubscribe',trades:drop,quotes:drop}));
  if(add.length)lws.send(JSON.stringify({action:'subscribe',trades:add,quotes:add}))}catch(e){}
 lsubs=want;fsub();}
var QB={},LASTP={};
function classify(t,p){const q=QB[t];
 if(q&&q.ap&&p>=q.ap)return 1;
 if(q&&q.bp&&p<=q.bp)return -1;
 const lp=LASTP[t];LASTP[t]=p;
 return lp==null?0:(p>lp?1:p<lp?-1:0)}
function lconnect(){
 const [k,s]=lkeys();
 if(!k||!s){lvnote('live per-second read is OFF — tap ⚙ keys above, paste your Alpaca key once','#facc15');return}
 if(!lmkt()){lvnote('live strip resumes with the tape (4a–8p ET)');return}
 try{lws&&lws.close()}catch(e){}
 LFEED=lfeed();
 const auto=(localStorage.feed_pref||'auto')==='auto';
 lws=new WebSocket('wss://stream.data.alpaca.markets/v2/'+LFEED);
 lws.onmessage=ev=>{let arr;try{arr=JSON.parse(ev.data)}catch(e){return}
  (Array.isArray(arr)?arr:[arr]).forEach(m=>{
   if(m.T==='success'&&m.msg==='connected')
    lws.send(JSON.stringify({action:'auth',key:k,secret:s}));
   else if(m.T==='success'&&m.msg==='authenticated'){ltries=0;lsubs=[];lsub();
    if(LFEED==='sip')localStorage.feed_sip='1';
    lvnote(LFEED==='sip'?'live ● SIP full tape — every exchange, every print'
     :'live ● IEX sample (~3% of volume)'+(localStorage.feed_sip==='0'?' — SIP needs a paid Alpaca plan':''),
     LFEED==='sip'?'#4ade80':'#facc15')}
   else if(m.T==='error'){
    // entitlement rejections while probing SIP: drop to IEX, remember, move on
    if(LFEED==='sip'&&auto&&(m.code===409||m.code===402
      ||/insufficient|subscription/i.test(m.msg||''))){lNoSip();return}
    lvnote(m.code===406?'live strip paused — TAPE is using the stream (one connection allowed); close TAPE to stream here'
     :(m.code===401||m.code===402)?'live strip: keys rejected — tap ⚙ keys and re-enter them'
     :'live strip: stream error '+(m.msg||m.code),'#f87171');
    if(m.code===406){try{lws.close()}catch(e){};lws=null}}
   else if(m.T==='q'&&m.S){QB[m.S]={bp:+m.bp,bs:+m.bs,ap:+m.ap,as:+m.as,t:Date.now()}}
   else if(m.T==='t'&&m.S){const b=LBUF[m.S]=LBUF[m.S]||[];
    b.push([Date.now(),(+m.p)*(+m.s),+m.p,classify(m.S,+m.p)]);
    if(b.length>3000)b.splice(0,600)}})};
 lws.onclose=()=>{if(!lws)return;lvnote('live strip reconnecting…','#facc15');
  const w=Math.min(30000,1000*Math.pow(2,ltries++));setTimeout(lconnect,w)};
 lws.onerror=()=>{try{lws.close()}catch(e){}};}
/* FINNHUB — the free second pipe. His FINNHUB_KEY (same value as the repo
   secret) opens finnhub's own live US trade stream: different source, no
   conflict with Alpaca's one-connection rule. Per name we feed the strip
   from whichever pipe printed more in the last minute, and if finnhub's
   prints arrive >5s late (free tiers sometimes delay) we drop it and say
   so — a delayed feed shown as live would be a lie. */
var FBUF={},fws=null,ftries=0,FSUBS=[],FLAG=[],FDELAYED=false;
function fsub(){
 if(!fws||fws.readyState!==1)return;
 const want=wlist().slice(0,8);
 FSUBS.filter(t=>want.indexOf(t)<0).forEach(t=>{
  try{fws.send(JSON.stringify({type:'unsubscribe',symbol:t}))}catch(e){}});
 want.filter(t=>FSUBS.indexOf(t)<0).forEach(t=>{
  try{fws.send(JSON.stringify({type:'subscribe',symbol:t}))}catch(e){}});
 FSUBS=want;}
function fconnect(){
 const k=localStorage.fh_k||'';
 if(!k||FDELAYED||!lmkt())return;
 try{fws&&fws.close()}catch(e){}
 fws=new WebSocket('wss://ws.finnhub.io/?token='+encodeURIComponent(k));
 fws.onopen=()=>{ftries=0;FSUBS=[];fsub()};
 fws.onmessage=ev=>{let m;try{m=JSON.parse(ev.data)}catch(e){return}
  if(m.type!=='trade'||!m.data)return;
  const now=Date.now();
  m.data.forEach(d=>{if(!d.s)return;
   if(d.t){FLAG.push(now-d.t);if(FLAG.length>200)FLAG.shift()}
   const b=FBUF[d.s]=FBUF[d.s]||[];
   b.push([now,(+d.p)*(+d.v||0),+d.p,0]);
   if(b.length>3000)b.splice(0,600)});
  if(FLAG.length>=30){
   const a=FLAG.slice().sort((x,y)=>x-y),med=a[a.length>>1];
   if(med>5000){FDELAYED=true;try{fws.close()}catch(e){};fws=null;
    console.log('finnhub feed delayed '+Math.round(med/1000)+'s — dropped')}}};
 fws.onclose=()=>{if(FDELAYED||!fws)return;
  setTimeout(fconnect,Math.min(30000,1000*Math.pow(2,ftries++)))};
 fws.onerror=()=>{try{fws.close()}catch(e){}};}
/* the satisfying part: 2 minutes of tape per card — price line glowing in
   the live-state color, buy/sell dollars as green/red bars underneath —
   repainted every second, straight from the stream buffer. */
function drawSpark(t,buf,now,color){
 const cv=$w('sp_'+t);if(!cv)return;const g=cv.getContext('2d');
 g.clearRect(0,0,600,76);
 const pts=[];for(let i=buf.length-1;i>=0;i--){const a=buf[i];
  if(now-a[0]>600000)break;pts.push(a)}
 pts.reverse();
 if(pts.length<3){g.fillStyle='#5b636c';
  pts.forEach(a=>{g.beginPath();
   g.arc((a[0]-(now-600000))/600000*598+1,36,3,0,7);g.fill()});return}
 let lo=1e18,hi=0;pts.forEach(a=>{lo=Math.min(lo,a[2]);hi=Math.max(hi,a[2])});
 if(hi<=lo)hi=lo*1.0005;
 const t0=now-600000,tw=600000;
 // CVD — the read that accumulates: buyer $ minus seller $, running sum.
 // Climbing = money coming in on his side; rolling over while price holds
 // = distribution. Cannot flicker: a cumulative line only bends.
 let cum=0,cmin=0,cmax=0;const dl=pts.map(a=>{
  if(a[3]>0)cum+=a[1];else if(a[3]<0)cum-=a[1];
  cmin=Math.min(cmin,cum);cmax=Math.max(cmax,cum);
  return [a[0],cum]});
 const cs=Math.max(cmax-cmin,1);
 g.strokeStyle='rgba(139,147,156,.5)';g.setLineDash([3,4]);g.beginPath();
 const zy=74-(0-cmin)/cs*30;g.moveTo(0,zy);g.lineTo(600,zy);g.stroke();g.setLineDash([]);
 g.strokeStyle='#e7e9ec';g.lineWidth=2;g.beginPath();
 dl.forEach((d,i)=>{const x=(d[0]-t0)/tw*598+1,y=74-(d[1]-cmin)/cs*30;
  i?g.lineTo(x,y):g.moveTo(x,y)});g.stroke();
 // price line above, glowing in the live-state color
 g.strokeStyle=color;g.lineWidth=2;g.shadowColor=color;g.shadowBlur=6;
 g.beginPath();
 pts.forEach((a,i)=>{const x=(a[0]-t0)/tw*598+1,
  y=36-(a[2]-lo)/(hi-lo)*32;i?g.lineTo(x,y):g.moveTo(x,y)});
 g.stroke();g.shadowBlur=0;
 const la=pts[pts.length-1];
 g.fillStyle=color;g.beginPath();
 g.arc((la[0]-t0)/tw*598+1,36-(la[2]-lo)/(hi-lo)*32,3,0,7);g.fill();
}
function cnt60(b,now){let n=0;for(let i=b.length-1;i>=0;i--){
 if(now-b[i][0]>60000)break;n++}return n}
function paintCard(t,now){
  const ab=LBUF[t]||[],fb=FBUF[t]||[];
  const useF=!FDELAYED&&cnt60(fb,now)>cnt60(ab,now)*1.5;
  const buf=useF?fb:ab,line=$w('lv_'+t),head=$w('lvh_'+t),card=$w('wc_'+t);
  // heartbeat: ticks EVERY second so liveness is visible even between the
  // sparse prints these names actually produce (his 'update every second'
  // confusion — the page is live; the tape is just quiet between trades)
  const hb=$w('hb_'+t),la=buf.length?buf[buf.length-1][0]:0;
  if(hb){const ago=la?Math.round((now-la)/1000):null;
   hb.textContent='●'+(ago==null?'':(ago<1?' now':' '+ago+'s'));
   hb.style.color=(ago!=null&&ago<2)?'#4ade80':(ago!=null&&ago<30?'#8b939c':'#5b636c');}
  if(!buf.length){
   const off=!lws||lws.readyState>1;
   if(head){head.textContent=off?'STREAM OFF':'WAITING FOR PRINTS';
    head.style.color='#5b636c';head.classList.remove('hot')}
   if(line){line.textContent=off?'live: stream not connected — tap ⚙ keys'
    :'live: connected — no prints on this name yet';line.style.color='#5b636c'}
   return [t,-1];}
  const s=liveRead(buf,now),st=lvSticky(LVS,t,liveState(s),now);
  const W=cvdWord(buf,now),L=liveLabel(st,s,QB[t],now);
  if(head){if(head.textContent!==W[0]&&card&&/[A-Z]/.test(W[0])){
    card.classList.remove('flash');void card.offsetWidth;card.classList.add('flash')}
   head.textContent=W[0];head.style.color=W[1];
   head.classList.toggle('hot',W[0]==='ACCUMULATING'||W[0]==='DISTRIBUTION')}
  if(line){line.textContent=L[0]+(useF?' · via finnhub':'');line.style.color=L[1]}
  if(card)card.style.borderLeftColor=W[1];
  drawSpark(t,buf,now,W[1]);
  return [t,W[2]*1e9+s.d30];}
setInterval(()=>{
 const now=Date.now();
 const scored=[];
 wlist().slice(0,8).forEach(t=>{scored.push(paintCard(t,now))});
 // reorder MY NAMES so the hottest-right-now sits on top; sticky states keep
 // the order from churning — only touch the DOM when the sequence changed
 const root=$w('wroot');
 if(root&&scored.length){
  scored.sort((a,b)=>b[1]-a[1]);
  const want=scored.map(x=>'wc_'+x[0]);
  const cur=Array.from(root.children).map(c=>c.id);
  if(want.join()!==cur.join())
   want.forEach(id=>{const el=$w(id);if(el)root.appendChild(el)});
 }
},1000);
document.addEventListener('visibilitychange',()=>{
 if(!document.hidden&&(!lws||lws.readyState>1))lconnect()});
/* NEWS on the card face — client-side, per-second-fresh enough (60s poll),
   straight from finnhub with his existing key. Newest headline <12h old
   renders top-level with its age; red chip when <2h. No GitHub in path. */
var NEWSS={};
function npoll(){
 const k=localStorage.fh_k||'';if(!k)return;
 const d=new Date(),iso=x=>x.toISOString().slice(0,10);
 const to=iso(d),from=iso(new Date(d-86400000*2));
 wlist().slice(0,8).forEach((t,i)=>{setTimeout(()=>{
  fetch('https://finnhub.io/api/v1/company-news?symbol='+t+'&from='+from+'&to='+to+'&token='+encodeURIComponent(k))
  .then(r=>r.ok?r.json():null).then(js=>{
   if(!js||!js.length)return;
   const n=js.sort((a,b)=>b.datetime-a.datetime)[0];
   if(!n||!n.headline)return;
   const age=Date.now()/1000-n.datetime;
   if(age>43200)return;
   NEWSS[t]={h:n.headline,age:age};
   const el=$w('nw_'+t);if(el){
    const am=age<3600?Math.round(age/60)+'m':(age/3600).toFixed(1)+'h';
    el.innerHTML=(age<7200?'<b style="color:#f87171">NEWS '+am+'</b> ':'<span style="color:#8b939c">news '+am+'</span> ')+wesc(n.headline);
    el.style.display='block'}
  }).catch(()=>{})},i*300)});}
npoll();setInterval(npoll,60000);
const _lsub0=wrender;wrender=function(){_lsub0();lsub()};
lconnect();fconnect();
"""


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _pct(x, dec=1):
    if x is None:
        return '<span class="px">--</span>'
    cls = "up" if x > 0 else ("dn" if x < 0 else "px")
    return f'<span class="{cls}">{x * 100:+.{dec}f}%</span>'


def _chip(text, hexc):
    return (f'<span class="chip" style="color:{hexc};border-color:{hexc}55;'
            f'background:{hexc}14">{html.escape(str(text))}</span>')


def _transitions_only(events):
    if not events:
        return ""
    lines = "".join(
        f'<div class="ev">{e["ts"][11:16]} &nbsp;<b>{html.escape(e["ticker"])}</b> '
        f'{html.escape(e["prev_state"] or "—")} → '
        f'<b style="color:{STATE_HEX.get(e["state"], "#e7e9ec")}">{html.escape(e["state"])}</b>'
        f'{" &nbsp;·&nbsp; " + html.escape(e["note"]) if e["note"] else ""}</div>'
        for e in events[-12:][::-1])
    return f'<h2>Transitions today</h2><div class="card">{lines}</div>'


def _scan_section(scan, confluence_only=False):
    if not scan:
        return '<h2>Scan</h2><div class="card note">No scan yet — first scheduled run will fill this in.</div>'
    ext_l = scan.get("ext_label", "AH")
    head = (f'<h2>{"Morning confirm" if scan["mode"] == "premarket" else "Tonight&rsquo;s scan"}'
            f' · {scan["trade_date"]} targets {scan["target_date"]}</h2>')
    rows_ = scan["rows"]
    if confluence_only:
        rows_ = [r for r in rows_ if r["score"] >= 40][:6]
        if not rows_:
            return ""
        head = (f'<h2>Tonight&rsquo;s setups · forecast for '
                f'{scan["target_date"]} · not today&rsquo;s movers</h2>')
    cards = []
    for r in rows_:
        tags = " ".join(_chip(t, TAG_HEX.get(c, "#9ca3af")) for t, c in r["tags"])
        drv = " · ".join(r["drivers"])
        cards.append(f'''<div class="card">
<div class="row"><span class="px">#{r["rank"]}</span><span class="tk">{html.escape(r["ticker"])}</span>
<span class="score" style="color:{'#f87171' if r["score"] >= 55 else ('#facc15' if r["score"] >= 42 else '#e7e9ec')}">{r["score"]:.1f}</span>
<span class="px">{r["close"]:.2f}</span>{_pct(r["day_pct"])}<span class="px">{ext_l}</span>{_pct(r["ext_pct"])}
<span class="px">{('%.1fx' % r["rvol"]) if r["rvol"] else ''}</span></div>
<div class="bar"><i style="width:{min(r["score"], 100):.0f}%"></i></div>
<div class="row" style="margin-top:7px">{tags}</div>
{f'<div class="note">{html.escape(drv)}</div>' if drv else ''}</div>''')
    return head + "".join(cards)


def _flow_section(flow, events):
    if not flow:
        return ""
    rows = flow["rows"]
    hot = [r for r in rows if r["state"] in INTERESTING]
    hot.sort(key=lambda r: INTERESTING.index(r["state"]))
    quiet = len(rows) - len(hot)
    ts = dt.datetime.fromisoformat(flow["ts"])
    head = f'<h2>Flow · as of {ts.strftime("%H:%M")} ET</h2>'
    rot = ""
    if flow.get("rot_in") or flow.get("rot_out"):
        chips = [_chip(f"IN {r['ticker']} {r['surge']:.1f}x its norm", "#e879f9")
                 for r in flow.get("rot_in", [])]
        chips += [_chip(f"OUT {r['ticker']} {r['frac']:.0%} of peak", "#ef4444")
                  for r in flow.get("rot_out", [])]
        rot = f'<div class="rot">{"".join(chips)}</div>'
    cards = []
    for r in hot:
        hexc = STATE_HEX.get(r["state"], "#9ca3af")
        tp = f'{r["tp"]:.1f}x now' if r["tp"] is not None else ""
        pace = f'pace {r["pace"]:.1f}x' if r["pace"] is not None else ""
        vw = f'vwap {r["vs_vwap"] * 100:+.1f}%' if r["vs_vwap"] is not None else ""
        cards.append(f'''<div class="card" style="border-left:3px solid {hexc}">
<div class="row"><span class="tk">{html.escape(r["ticker"])}</span>
{_chip(r["state"], hexc)}<span class="px">{r["last"]:.2f}</span>{_pct(r["day_pct"])}</div>
<div class="meta"><b>{tp}</b><span>{pace}</span><span>{vw}</span>
<span>${fmt_big(r["dollar_w"])} /15m</span></div>
{f'<div class="note">{html.escape(r["note"])}</div>' if r["note"] else ''}</div>''')
    evs = ""
    if events:
        lines = "".join(
            f'<div class="ev">{e["ts"][11:16]} &nbsp;<b>{html.escape(e["ticker"])}</b> '
            f'{html.escape(e["prev_state"] or "—")} → '
            f'<b style="color:{STATE_HEX.get(e["state"], "#e7e9ec")}">{html.escape(e["state"])}</b>'
            f'{" &nbsp;·&nbsp; " + html.escape(e["note"]) if e["note"] else ""}</div>'
            for e in events[-14:][::-1])
        evs = f'<h2>Transitions today</h2><div class="card">{lines}</div>'
    q = f'<div class="quiet">+ {quiet} names quiet</div>' if quiet else ""
    return head + rot + "".join(cards) + q + evs


SESSION_LABEL = {"pre": "Discovery · pre-market", "rth": "Discovery board",
                 "post": "Discovery · after-hours"}

DIL_HEX = {"FRESH PAPER": "#f87171", "S-1 PENDING": "#facc15",
           "OPEN SHELF": "#fb923c", "CLEAN": "#4ade80"}

MOOD_HEX = {"MONEY HERE": "#4ade80", "COOLING": "#facc15",
            "MONEY LEAVING": "#fb923c", "DEAD": "#f87171",
            "STALLED": "#8b939c", "WARMING": "#8b939c", "NO TAPE": "#5b636c",
            "THIN TAPE": "#5b636c"}


def _now_line(r):
    """One honest sentence about the CURRENT MOMENT, vs the name's own
    session as the base."""
    sm = r.get("stalled_min")
    if r.get("mood") == "STALLED" and sm is not None:
        return (f"sideways {sm // 60}h{sm % 60:02d}m — nothing happening now"
                if sm >= 60 else f"sideways {sm}m — nothing happening now")
    if r.get("r15") is None:
        return None
    bits = []
    if r.get("f15"):
        bits.append(f"${fmt_big(r['f15'])}/15m")
    if r.get("travel15") is not None:
        bits.append(f"{r['travel15'] * 100:.1f}% travel/15m")
    bits.append(f"{r['r15'] * 100:.0f}% of its peak 15m")
    return "now: " + " · ".join(bits)


def _story(r):
    """The card's lead read, in plain sentences — his ask (2026-07-22): the
    engine writes what a person watching the tape would say, instead of a
    row of fragments he has to decode."""
    if not r.get("present"):
        return None
    p1 = []
    if r.get("day_pct") is not None:
        p1.append(f"{r['day_pct'] * 100:+.0f}% today")
    off = r.get("off_hi")
    if off is not None:
        p1.append("at the highs" if off >= -0.03
                  else f"{abs(off) * 100:.0f}% below its high")
    vs = r.get("vs_vwap")
    if vs is not None:
        p1.append("holding above vwap" if vs >= 0 else "lost the vwap")
    mood, f15, r15 = r.get("mood"), r.get("f15"), r.get("r15")
    sm = r.get("stalled_min")
    if mood == "STALLED" and sm is not None:
        s2 = ("nothing has happened for "
              + (f"{sm // 60}h{sm % 60:02d}m" if sm >= 60 else f"{sm}m")
              + " — dead sideways")
    elif mood == "WARMING":
        s2 = "first minutes of the session — still building a read"
    elif mood == "THIN TAPE":
        s2 = ("feed sample too thin to judge — broker tape may show more "
              "(free IEX slice)")
    elif mood == "NO TAPE" or r15 is None:
        s2 = None
    else:
        peak = (f15 / r15) if (f15 and r15) else None
        pk = f" vs ${fmt_big(peak)} at its peak" if peak else ""
        pc = f" ({r15 * 100:.0f}%)" if r15 is not None else ""
        if mood == "MONEY HERE":
            s2 = f"money is here NOW — ${fmt_big(f15 or 0)}/15m, near its best pace"
        elif mood == "COOLING":
            s2 = f"flow is cooling — ${fmt_big(f15 or 0)}/15m{pk}{pc}"
        elif mood == "MONEY LEAVING":
            s2 = (f"the money is walking — ${fmt_big(f15 or 0)}/15m on the tape"
                  f"{pk}{pc}")
            if (r.get("day_pct") or 0) > 0.05:
                s2 += ", while the chart still shows green"
        else:                              # DEAD
            s2 = f"the money that was here is gone — ${fmt_big(f15 or 0)}/15m{pk}{pc}"
    out = ", ".join(p1)
    if s2:
        out = (out + ". " if out else "") + s2
    if (r.get("swings") or 0) >= 6 and (r.get("travel15") or 0) >= 0.04:
        out += f" — and it's still whipping ({r['swings']} legs today)"
    return out or None


def _playbook(r, ped_n: int):
    """One line fusing today's shape with the company's anatomy. Priors are
    labeled hist and never dressed up as calls — the item-26 rule."""
    sh = r.get("shape")
    if not sh:
        return None
    if sh in ("GAP & FADE", "FADED RUNNER") and ped_n >= 2:
        return ("playbook: classic pump-fade anatomy — scalp the bounces, "
                "don't marry it (hist: these close weak)")
    if sh in ("GAP & GO", "RUNNER") and ped_n >= 3:
        return ("playbook: running, but built to rug — momentum is real "
                "until the offering prints (hist)")
    if sh in ("GAP & GO", "RUNNER", "MIDDAY POP") and ped_n <= 1:
        return ("playbook: clean momentum profile — vwap dips historically "
                "get bought (hist, not a call)")
    if sh == "BLEEDER":
        return "playbook: supply in control all day (hist: bounces fade)"
    if sh == "GAP & CHOP":
        return ("playbook: gap without follow-through — let it pick a "
                "direction first")
    return None


def _watch_enrich(order, ws, intel, bd):
    """Join watch rows with intel + board headline into card-ready dicts —
    used by BOTH the server-side render and docs/watch.json, so the phone's
    45s client-side re-render shows exactly what the server would."""
    rows_by = {r["ticker"]: r for r in (ws or {}).get("rows", [])}
    board_by = {r["ticker"]: r for r in (bd or {}).get("rows", [])}
    intel = intel or {}
    out = []
    for t in order:
        r = dict(rows_by.get(t) or {"ticker": t})
        r["present"] = t in rows_by
        b = board_by.get(t) or {}
        _dil = (intel.get("dil") or {}).get(t)
        _hl = (intel.get("halts_by") or {}).get(t)
        _rot = rot_of((intel.get("flo") or {}).get(t) or {})
        if r["present"]:
            ev_w, ev_c, ev_y = edge_verdict(r, _dil, _hl, _rot)
            if r.get("ssr"):
                ev_y = (ev_y + " · SSR on").strip(" ·")
            r["ev"] = [ev_w, ev_c, ev_y]
        grade = (_dil or {}).get("grade")
        r["dil"] = grade if grade and grade != "UNKNOWN" else None
        r["halts"] = ({"n": _hl["n"], "res": _hl.get("res_t")}
                      if _hl and _hl.get("n") else None)
        r["rot"] = round(_rot, 1) if _rot else None
        r["headline"] = b.get("headline")
        r["pr_ts"] = b.get("pr_ts")
        ped = (intel.get("ped") or {}).get(t)
        if ped and ped.get("grade") not in (None, "UNKNOWN"):
            r["ped"] = {k: ped.get(k) for k in
                        ("grade", "n", "flags", "jur", "cik")}
        r["filings"] = (_dil or {}).get("recent")
        r["now_line"] = _now_line(r) if r["present"] else None
        r["read"] = _story(r)
        r["playbook"] = _playbook(r, (r.get("ped") or {}).get("n", 0))
        r["dossier"] = _dossier(r)   # pre-rendered: JS injects it verbatim,
        out.append(r)                # so both renders are the same HTML
    return out


def _dossier(r):
    """The tap-to-open deep dive: everything on file about this name, with
    the receipts (a link into the actual SEC record). Static HTML <details>
    — works with zero JS, renders identically server- and client-side."""
    ped = r.get("ped")
    lines = []
    if ped:
        col = ("#f87171" if ped.get("n", 0) >= 3
               else "#facc15" if ped.get("n", 0) else "#4ade80")
        lines.append(f'<b style="color:{col}">{html.escape(ped["grade"])}</b>'
                     + (f' <span style="color:#8b939c">· {html.escape(ped.get("jur") or "")}</span>'
                        if ped.get("jur") else ""))
        for fl in (ped.get("flags") or []):
            lines.append("⚠ " + html.escape(fl))
        if not ped.get("flags"):
            lines.append("no structural red flags on file")
    else:
        lines.append('<span style="color:#5b636c">company record not pulled '
                     "yet — lands with the intel pass (&le;2 min)</span>")
    if r.get("rsplits"):
        lines.append(f'⚠ {r["rsplits"]} reverse split'
                     f'{"s" if r["rsplits"] > 1 else ""} in ~13mo')
    if r.get("timeline"):
        lines.append("today: " + html.escape(r["timeline"]))
    fl_ = r.get("filings")
    if fl_:
        lines.append("filings: " + " · ".join(
            f"{html.escape(str(f))} {html.escape(str(d))}" for f, d in fl_[:5]))
    cik = (ped or {}).get("cik")
    if cik:
        lines.append(f'<a style="color:#60a5fa" href="https://www.sec.gov/cgi-bin/'
                     f'browse-edgar?action=getcompany&CIK={cik:010d}'
                     f'&type=&dateb=&owner=include&count=40" target="_blank" '
                     'rel="noopener">→ full SEC filing history</a>')
    body = "".join(f'<div class="note" style="font-size:12.5px">{x}</div>'
                   for x in lines)
    return (f'<details style="margin-top:6px"><summary style="color:#8b939c;'
            'font-size:12px;cursor:pointer">deep dive — who is this company'
            f'</summary>{body}</details>')


def _dossier_body(r):
    d = _dossier(r)
    return d.split("</summary>", 1)[1].rsplit("</details>", 1)[0]


def _watch_section(wl, ws, intel, bd, now):
    """MY NAMES — the top of the page (HANDOFF item 31). One rich card per
    watchlist ticker, HIS input order, always present. A name the engine has
    no tape for still gets a card that says exactly why — never blank,
    never 'not on radar'."""
    order = wl or (ws or {}).get("tickers") or []
    ts = (ws or {}).get("ts") or ""
    stale_day = bool(ts) and ts[:10] < now.date().isoformat()
    editor = ('<div class="wed"><div class="addrow">'
              '<input id="wq" placeholder="add ticker" maxlength="6" '
              'autocomplete="off" autocorrect="off" '
              'autocapitalize="characters" spellcheck="false" '
              'enterkeyhint="done"><button id="wqb">＋</button>'
              '<button id="wkeys" style="flex:.7">⚙ keys</button></div>'
              '<div class="note" id="wst"></div>'
              '<div class="note" id="lvst"></div>'
              '<div id="wsetup" class="card" hidden>'
              '<b>Keys — enter once, only on this phone</b>'
              '<div class="note">Saved in this browser alone (localStorage) and '
              'used across the hub and TAPE. Never committed, never in the repo, '
              'never sent anywhere but the data vendor and GitHub. These are the '
              'same values as your repo secrets — the page needs its own copy '
              'because a public web page can&rsquo;t read repo secrets (anyone '
              'could). Enter once; it sticks.</div>'
              '<div class="klab">Alpaca — the live per-second tape</div>'
              '<div class="addrow"><input id="akey" placeholder="Alpaca API key" '
              'autocomplete="off" autocapitalize="none" spellcheck="false"></div>'
              '<div class="addrow"><input id="asec" placeholder="Alpaca secret" '
              'autocomplete="off" autocapitalize="none" spellcheck="false"></div>'
              '<div class="klab">Data feed — accuracy</div>'
              '<div class="addrow"><button id="feedsel" style="flex:1"></button></div>'
              '<div class="note" style="margin-top:2px">Auto uses the fullest '
              'tape your Alpaca plan allows — <b>SIP</b> is every US exchange '
              '(most accurate). A free plan falls back to <b>IEX</b> (~3% of '
              'volume) automatically; upgrade Alpaca and it switches itself.</div>'
              '<div class="klab">Finnhub — free second tape pipe (optional)</div>'
              '<div class="note">Same value as your FINNHUB_KEY repo secret. '
              'Adds finnhub&rsquo;s own live trade stream next to Alpaca — '
              'per name the denser pipe wins; if it turns out delayed the '
              'page drops it automatically.</div>'
              '<div class="addrow"><input id="fhkey" placeholder="Finnhub key" '
              'autocomplete="off" autocapitalize="none" spellcheck="false"></div>'
              '<div class="klab">GitHub token — syncs your list to the engine</div>'
              '<div class="note">Fine-grained token, repo <b>only '
              'ALANKK11/ignition</b>, permission <b>Contents → Read and '
              'write</b>. Leave blank to keep the one already saved.</div>'
              '<div class="addrow"><input id="wtok" placeholder="github_pat_…" '
              'autocomplete="off" autocapitalize="none" spellcheck="false"></div>'
              '<div class="addrow"><button class="go" id="ksave" style="flex:1">'
              'SAVE KEYS</button></div></div></div>')
    head = '<h2 style="color:#ff5a1f;font-size:13px">MY NAMES'
    if ts:
        head += f' · <span id="wts">{ts[11:16]} ET</span>'
    if stale_day:
        head += f' · last read {ts[:10]}'
    head += '</h2>'
    cards = "".join(_watch_card(r)
                    for r in _watch_enrich(order, ws, intel, bd))
    if not order:
        cards = ('<div class="card note" id="wempty">no names yet — type a '
                 'ticker above. It renders instantly from the live pulse '
                 'feed; with sync set up the engine follows with full '
                 'EDGAR/halt/fade intel within ~2 minutes.</div>')
    return head + editor + f'<div id="wroot">{cards}</div>'


def _watch_card(r):
    """One MY NAMES card from an enriched row. MOOD leads — the slow, sticky
    read (no green-red-green flicker by construction); the NOW line is the
    current moment vs the name's own session."""
    t = r["ticker"]
    if not r.get("present"):
        return f'''<div class="card" style="border-left:3px solid #5b636c">
<div class="row"><span class="tk" style="font-size:18px">{html.escape(t)}</span>
<span class="px">—</span></div>
<div class="note">on your list — engine picks it up on its next tick
(&le;2 min during the 7a&ndash;7p ET shift)</div></div>'''
    dp = r.get("day_pct")
    up = (dp or 0) >= 0
    mood = r.get("mood")
    chips = ""
    if mood:
        chips += _chip(mood, MOOD_HEX.get(mood, "#9ca3af"))
    if r.get("state"):
        chips += _chip(r["state"], STATE_HEX.get(r["state"], "#9ca3af"))
    if r.get("ssr"):
        chips += _chip("SSR", "#fb923c")
    hl = r.get("halts")
    if hl:
        hlbl = f'×{hl["n"]} halt{"s" if hl["n"] > 1 else ""}'
        if hl.get("res"):
            hlbl += f' · res {hl["res"]}'
        chips += _chip(hlbl, "#f87171" if hl["n"] >= 3 else "#facc15")
    if r.get("dil"):
        chips += _chip(r["dil"], DIL_HEX.get(r["dil"], "#9ca3af"))
    ped = r.get("ped")
    if ped and ped.get("n"):
        chips += _chip(ped["grade"],
                       "#f87171" if ped["n"] >= 3 else "#facc15")
    if r.get("shape"):
        chips += _chip(r["shape"], "#22d3ee")
    if r.get("rot"):
        chips += _chip(f'rot {r["rot"]}x', "#e879f9")
    if r.get("headline"):
        chips += _chip(f'PR {r.get("pr_ts") or ""}', "#ff5a1f")
    meta = []
    if dp is not None:
        meta.append(f'day {dp * 100:+.0f}%')
    if r.get("dollars"):
        meta.append(f'${fmt_big(r["dollars"])} iex')
    if r.get("vs_adv"):
        meta.append(f'{r["vs_adv"]:.1f}x ADV')
    if r.get("vs_vwap") is not None:
        meta.append(("above" if r["vs_vwap"] >= 0 else "BELOW")
                    + f' vwap {r["vs_vwap"] * 100:+.1f}%')
    if r.get("off_hi") is not None:
        meta.append(f'{r["off_hi"] * 100:+.0f}% off high')
    if r.get("swings"):
        meta.append(f'{r["swings"]} swings')
    last = f'{r["last"]:.3f}' if isinstance(r.get("last"), (int, float)) else "—"
    ev = r.get("ev")
    nl = r.get("read") or r.get("now_line")
    hc = MOOD_HEX.get(mood, "#4ade80" if up else "#f87171")
    # MINIMAL (his call, 2026-07-22): one word, one chart, one live line.
    # Everything else — chips, story, verdict, playbook, dossier — lives
    # behind a single quiet "more". No competing labels, nothing static.
    more = ('<div class="note" style="color:#5b636c;font-size:11px">'
            'engine research · not live</div>')
    if ev:
        more += (f'<div class="note" style="margin-top:4px"><b style="color:{ev[1]}">{ev[0]}</b>'
                 f'<span style="color:#8b939c"> — {html.escape(ev[2])}</span></div>')
    if r.get("playbook"):
        more += f'<div class="note" style="color:#facc15;font-size:12.5px">{html.escape(r["playbook"])}</div>'
    if r.get("reason"):
        more += f'<div class="note" style="color:#8b939c;font-style:italic">{html.escape(r["reason"])}</div>'
    if r.get("headline"):
        more += f'<div class="note" style="color:#c9ced4">📰 {html.escape(r["headline"])}</div>'
    more += _dossier_body(r)
    return f"""<div class="card sym" id="wc_{t}" style="border-left:3px solid {hc}">
<div class="row"><span class="tk" style="font-size:18px">{html.escape(t)}</span>
<span class="vhead" id="lvh_{t}" style="font-weight:800;font-size:15px;letter-spacing:.03em;color:{hc}">{html.escape(mood or "…")}</span>
<span class="px" style="margin-left:auto">{last}</span>
<span class="hb" id="hb_{t}" title="seconds since last print">●</span>
<span class="wrm" data-t="{t}">×</span></div>
<canvas class="spk" id="sp_{t}" width="600" height="76"></canvas>
<div class="note" id="lv_{t}" style="font-size:13px;color:#5b636c"></div>
<div class="note" id="nw_{t}" style="font-size:12.5px;color:#c9ced4;display:none"></div>
<details class="mre"><summary>more</summary>{more}</details></div>"""


def _no_board_notice(sdir, now=None):
    """The board is the product. If it is missing, say so loudly with the
    reason — silence here once cost days of confusion. But a missing board
    outside shift hours is *expected*, and framing it as a failure at 2am
    made a healthy deploy look broken."""
    import glob
    now = now or dt.datetime.now(NY)
    off_hours = now.weekday() >= 5 or now.hour < 7 or now.hour >= 19
    if off_hours:
        when = ("Monday" if now.weekday() >= 5 or (now.weekday() == 4 and now.hour >= 19)
                else "today")
        return ('<div class="stale" style="background:#12233a;border-color:#1e3a5f;'
                'color:#93c5fd"><b>BOARD OFFLINE — market closed</b><br>'
                f'The live board starts with the 6:55a ET shift {when}. '
                'Nothing is broken. Below is the nightly forecast scan.</div>')
    ran = bool(glob.glob(os.path.join(sdir, "alpaca_base_*.json")))
    why = ("The live shift has run, but no board was produced — likely an "
           "Alpaca data error." if ran else
           "The live shift has not run yet today, or it failed before "
           "reaching Alpaca.")
    return ('<div class="stale"><b>NO BOARD DATA</b><br>' + why +
            '<br>Check the repo&rsquo;s <b>Actions</b> tab → latest '
            '<b>live shift</b> run. Everything below is the nightly '
            'forecast scan, which is a different thing.</div>')


def _heat_meter(heat):
    if heat is None:
        return ""
    n = max(0, min(5, int(round(heat / 20))))
    col = "#f87171" if heat >= 70 else ("#facc15" if heat >= 40 else "#5b636c")
    return (f'<span style="color:{col};letter-spacing:1px;font-size:13px">'
            f'{"▰" * n}{"▱" * (5 - n)}</span>'
            f'<span class="px" style="margin-left:4px">{heat:.0f}</span>')


def _board_section(bd, closed_now, stale_day=None):
    if not bd or not bd.get("rows"):
        return ""
    ts = bd["ts"][11:16]
    label = SESSION_LABEL.get(bd.get("session"), "Board")
    if closed_now and bd.get("session") == "rth":
        label = "Today&rsquo;s board (closed)"
    if stale_day:
        label = f"Last board · {stale_day} {SESSION_LABEL.get(bd.get('session'), '')}"
    cards = []
    intel = _INTEL.get("cur") or {}
    for r in bd["rows"]:
        up = r["move"] > 0
        _dil = (intel.get("dil") or {}).get(r["ticker"])
        _hl = (intel.get("halts_by") or {}).get(r["ticker"])
        _rot = rot_of((intel.get("flo") or {}).get(r["ticker"]) or {})
        ev_w, ev_c, ev_y = edge_verdict(r, _dil, _hl, _rot)
        if r.get("ssr"):
            ev_y = (ev_y + " · SSR on").strip(" ·")
        chips = ""
        if r.get("new"):
            chips += _chip("NEW", "#ff5a1f")
        if r.get("hot"):
            chips += _chip("HOT NOW", "#facc15")
        if r.get("pin"):
            chips += _chip("PIN · DEAD", "#e879f9")
        if r.get("catalyst"):
            chips += _chip(f'PR {r.get("pr_ts") or ""}', "#ff5a1f")
        if r.get("state"):
            chips += _chip(r["state"], STATE_HEX.get(r["state"], "#9ca3af"))
        meta = [f'${fmt_big(r["dollars"])}']
        if r.get("vs_adv"):
            meta.append(f'{r["vs_adv"]:.1f}x ADV')
        if r.get("off_hi") is not None:
            meta.append(f'{r["off_hi"] * 100:+.0f}% off high')
        if r.get("swings"):
            meta.append(f'{r["swings"]} swings')
        if r.get("path") is not None:
            meta.append(f'{r["path"] * 100:.0f}% traveled')
        if r.get("tp"):
            meta.append(f'{r["tp"]:.1f}x tape now')
        meta.append(f'since {r["first_seen"]}')
        cards.append(f"""<div class="card" style="border-left:3px solid {'#4ade80' if up else '#f87171'}">
<div class="row"><span class="tk" style="font-size:18px">{html.escape(r["ticker"])}</span>
<span class="score" style="font-size:19px;color:{'#4ade80' if up else '#f87171'}">{r["move"] * 100:+.0f}%</span>
{_heat_meter(r.get("heat"))}
<span class="px">{r["last"]:.2f}</span>{chips}</div>
<div class="meta">{"".join(f"<span>{m}</span>" for m in meta)}</div>
<div class="note" style="margin-top:4px"><b style="color:{ev_c}">{ev_w}</b>
<span style="color:#8b939c"> — {html.escape(ev_y)}</span></div>
{f'<div class="note" style="margin-top:5px;color:#c9ced4">📰 {html.escape(r["headline"])}'
 + ("".join(" " + _chip(fl, "#f87171" if fl.startswith("⚠") else "#8b939c")
            for fl in (r.get("flags") or [])[:3])) + "</div>"
 if r.get("headline") else ""}</div>""")
    return (f'<h2 style="color:#ff5a1f;font-size:13px">{label} · {ts} ET</h2>'
            '<div class="note" style="margin:-4px 2px 8px">every US listing, ETFs out, '
            'volume-verified, ranked by the HEAT METER: intraday travel a human could '
            'actually trade (one-print gaps score ~0), swing count, and whether '
            'it&rsquo;s moving RIGHT NOW</div>'
            + "".join(cards))


_INTEL = {"cur": None}


def _halt_section(intel):
    if not intel:
        return ""
    today = intel.get("halts_today") or []
    if not today:
        return ""
    code_hex = {"LUDP": "#fb923c", "LUDS": "#fb923c", "T1": "#60a5fa",
                "T2": "#60a5fa", "T3": "#60a5fa", "T12": "#f87171",
                "H10": "#f87171", "H11": "#f87171"}
    by = intel.get("halts_by") or {}
    rows = []
    for i in reversed(today[-14:]):          # newest first
        n = (by.get(i["sym"]) or {}).get("n", 1)
        bits = [f'<span class="tk">{html.escape(i["sym"])}</span>',
                _chip(i["code"], code_hex.get(i["code"], "#9ca3af")),
                f'<span class="px">{i.get("hm") or ""}</span>']
        if i.get("thr"):
            bits.append(f'<span class="px">thr {html.escape(i["thr"])}</span>')
        bits.append(f'<span class="px">{"resumes " + i["res_t"] if i.get("res_t") else "HALTED"}</span>')
        if n >= 3:
            bits.append(_chip(f"×{n} EXHAUSTION?", "#f87171"))
        elif n > 1:
            bits.append(_chip(f"×{n}", "#facc15"))
        rows.append('<div class="row" style="margin:5px 0">' + "".join(bits)
                    + '</div>')
    return ('<h2>HALT RADAR · every LULD pause is a ±10%-in-5-min move</h2>'
            '<div class="card">' + "".join(rows) + '</div>')


def _ext_section(ext):
    if not ext or not ext.get("rows"):
        return ""
    pre = ext.get("session") == "pre"
    ts = ext["ts"][11:16]
    cards = []
    for r in ext["rows"]:
        va = f'{r["vs_adv"]:.1f}x ADV' if r.get("vs_adv") else ""
        cards.append(f"""<div class="card" style="border-left:3px solid #ff5a1f">
<div class="row"><span class="tk">{html.escape(r["ticker"])}</span>
<span class="score" style="color:{'#4ade80' if r["gap"] > 0 else '#f87171'}">{r["gap"] * 100:+.0f}%</span>
<span class="px">{r["last"]:.3f}</span>
<span class="px">${fmt_big(r["dollars"])} ext</span><span class="px">{va}</span>
{_chip("NEW", "#ff5a1f") if r.get("new") else ""}</div></div>""")
    title = "Pre-market ignitions" if pre else "After-hours ignitions"
    return (f'<h2 style="color:#ff5a1f">{title} · full market · {ts} ET</h2>'
            '<div class="note" style="margin:-4px 2px 8px">every US listing, '
            'price floor $0.10 — gaps confirmed by real extended-hours dollar '
            'volume, auto-injected into the next scan</div>' + "".join(cards))


def _movers_section(mv):
    if not mv or not mv.get("rows"):
        return ""
    ts = mv["ts"][11:16]
    cards = []
    for r in mv["rows"]:
        cards.append(f"""<div class="card" style="border-left:3px solid {'#4ade80' if r['day_pct'] > 0 else '#f87171'}">
<div class="row"><span class="tk">{html.escape(r["ticker"])}</span>
<span class="score" style="font-size:17px;color:{'#4ade80' if r['day_pct'] > 0 else '#f87171'}">{r["day_pct"] * 100:+.0f}%</span>
<span class="px">{r["last"]:.2f}</span><span class="px">{r["pace"]:.1f}x vol</span>
<span class="px">${fmt_big(r["dollar_day"])} iex</span>
{_chip("WATCHING", "#e879f9") if r.get("promoted") else ""}</div></div>""")
    return (f'<h2 style="color:#4ade80">Today&rsquo;s tape · real movers · {ts} ET</h2>'
            '<div class="note" style="margin:-4px 2px 8px">every US listing, ETFs '
            'excluded — only names that actually moved ≥15% on real, elevated '
            'volume, biggest move first</div>' + "".join(cards))


def _audit_section(hist):
    if hist is None or len(hist) == 0:
        return ""
    cards, seen, any_graded = [], set(), False
    for _, r in hist.iterrows():           # newest first; one card per day
        if r["trade_date"] in seen:
            continue                       # re-runs of the same scan are noise
        seen.add(r["trade_date"])
        ic = r.get("ic")
        edge = r.get("edge_rvol")
        graded = ic is not None and ic == ic
        if graded:
            any_graded = True
            cards.append(f'''<div class="card"><div class="px">{r["trade_date"]}</div>
<div class="big" style="color:{'#4ade80' if ic > 0 else '#f87171'}">{ic:+.2f}</div>
<div class="px">IC · edge {f"{edge:.1f}x" if edge and edge == edge else "--"}</div></div>''')
        else:
            cards.append(f'''<div class="card"><div class="px">{r["trade_date"]}</div>
<div class="big" style="color:#5b636c">·&nbsp;·&nbsp;·</div>
<div class="px">grades after close</div></div>''')
        if len(cards) >= 8:
            break
    if not any_graded:
        return ('<h2>Self-audit · rank IC per scan</h2>'
                '<div class="quiet">no graded scans yet — each forecast is '
                'scored against the next session&rsquo;s realized tape, so the '
                'first grades land after tomorrow&rsquo;s close</div>')
    return ('<h2>Self-audit · rank IC per scan</h2>'
            f'<div class="audit">{"".join(cards)}</div>')


def _ign_precision_line(jr):
    try:
        h_, n = jr.ignition_stats()
    except Exception:
        return ""
    if not n:
        return ""
    col = "#4ade80" if h_ / n >= 0.5 else "#f87171"
    return (f'<div class="note" style="margin-top:6px">ignition receipts, 30d: '
            f'<b style="color:{col}">{h_}/{n} hit</b> — an ignition "hits" if it '
            f'then trades ≥2x its ADV or ≥$1M in the regular session</div>')


def build(cfg: dict, out_dir: str, demo: bool = False) -> str:
    from .journal import Journal
    sdir = os.path.join(cfg["_paths"]["data"], "state")
    scan = _load(os.path.join(sdir, "latest_scan.json"))
    flow = _load(os.path.join(sdir, "latest_flow.json"))
    STATE_V = 4
    bd = _load(os.path.join(sdir, "latest_board.json"))
    if bd and bd.get("v") != STATE_V:
        bd = None
    mv = _load(os.path.join(sdir, "latest_movers.json"))
    if mv and mv.get("v") != STATE_V:
        mv = None                        # written by old code — never render
    intel = _load(os.path.join(sdir, "latest_intel.json"))
    if intel and intel.get("v") != STATE_V:
        intel = None
    _INTEL["cur"] = intel
    pulse = _load(os.path.join(sdir, "latest_pulse.json"))
    if pulse and pulse.get("v") != STATE_V:
        pulse = None                     # written by old code — never serve
    ws = _load(os.path.join(sdir, "latest_watch.json"))
    if ws and ws.get("v") != STATE_V:
        ws = None
    from .watch import load_watchlist
    try:
        wl = load_watchlist(cfg)
    except Exception:
        wl = []
    if demo:                     # demo is self-contained: use its planted list
        wl = (ws or {}).get("tickers") or wl
    ext = _load(os.path.join(sdir, "latest_ext.json"))
    if ext and ext.get("v") != STATE_V:
        ext = None
    if ext:
        newest = max(dt.datetime.now(NY).date().isoformat(),
                     (flow or {}).get("ts", "")[:10])
        if ext.get("ts", "")[:10] < newest:
            ext = None                   # an old sweep is history, not news
    if mv:
        newest = max(dt.datetime.now(NY).date().isoformat(),
                     (flow or {}).get("ts", "")[:10])
        if mv.get("ts", "")[:10] < newest:
            mv = None
    bd_stale = None
    if bd and bd.get("ts", "")[:10] < dt.datetime.now(NY).date().isoformat():
        bd_stale = bd["ts"][:10]      # overnight: show it, but say when it's from
    jr = Journal(cfg["_paths"]["journal"])
    hist = None
    events = []
    try:
        hist = jr.history(demo, limit=8)
    except Exception:
        pass
    if flow:
        day = flow["ts"][:10]
        try:
            import json as _json
            p = os.path.join(sdir, f"flow_events_{day}.jsonl")
            if os.path.exists(p):
                with open(p) as fh:
                    events = [_json.loads(x) for x in fh if x.strip()]
            else:
                events = jr.events_for_day(demo, day)
        except Exception:
            events = []
    now = dt.datetime.now(NY)
    ts_iso = (flow or scan or {}).get("ts", now.isoformat())
    closed = ""
    if flow:
        wd = now.weekday() < 5
        rth = wd and (dt.time(9, 30) <= now.time() < dt.time(16, 0))
        if not rth:
            closed = ('<div class="stale" style="background:#14181d;border-color:'
                      '#242a31;color:#8b939c">market closed — flow and radar below '
                      f'are the session&rsquo;s last readings ({flow["ts"][11:16]} ET)</div>')
    is_closed = bool(closed)
    my_names = _watch_section(wl, ws, intel, bd, now)
    if bd:
        body = (my_names
                + _board_section(bd, is_closed, bd_stale) + _halt_section(intel)
                + closed
                + _transitions_only(events)
                + _scan_section(scan, confluence_only=True)
                + _audit_section(hist) + _ign_precision_line(jr))
    elif not (ext or mv):
        body = (my_names + _no_board_notice(sdir) + closed
                + _flow_section(flow, events)
                + _scan_section(scan, confluence_only=True)
                + _audit_section(hist) + _ign_precision_line(jr))
    else:
        body = (my_names
                + _ext_section(ext) + _movers_section(mv) + _halt_section(intel)
                + closed
                + _flow_section(flow, events)
                + _scan_section(scan, confluence_only=True)
                + _audit_section(hist) + _ign_precision_line(jr))
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta http-equiv="refresh" content="150">
<meta name="theme-color" content="#0b0d10">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icon.svg"><link rel="apple-touch-icon" href="icon.svg">
<title>IGNITION</title><style>{CSS}</style></head><body>
<h1><s>IGNITION</s> HUB{' · DEMO' if demo else ''}</h1>
<div class="sub">updated <span id="ago" data-ts="{ts_iso}">…</span> ·
auto-refreshes · live shift 7am–7pm (~45s) · scan 9:15pm + 7:45am ET
{f" · <b style='color:#4ade80'>{html.escape(flow['provider'])}</b>" if flow else ""}</div>
<a class="tapelink" href="tape.html">&#9889; LIVE TAPE &mdash; second-by-second
drain meter for names you&rsquo;re holding</a>
<div class="pulse"><input id="pq" placeholder="type a ticker \u2014 hot or not?"
autocomplete="off" autocorrect="off" autocapitalize="characters"
spellcheck="false" maxlength="6" enterkeyhint="search"></div>
<div id="stale" class="stale" hidden>This page hasn&rsquo;t updated in a while —
market closed, or check the Actions tab of your repo.</div>
{body}
<div class="foot"><b>MY NAMES</b> is your watchlist — full telemetry, no gates, your
order. The <b>discovery board</b> below it is live tape ranked by tradable travel.
<b>Tonight&rsquo;s setups</b> is a next-day forecast — a different thing, and never a list of
today&rsquo;s movers. IGNITION ranks expected <b>activity</b>, not direction. Not investment
advice.</div>
<script>{JS}</script></body></html>"""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "index.html"), "w") as f:
        f.write(doc)
    if pulse:
        with open(os.path.join(out_dir, "pulse.json"), "w") as f:
            json.dump(pulse, f, separators=(",", ":"))
    # docs/watch.json — the phone side of the single source of truth. Tape
    # merges `tickers`; the hub's own JS re-renders MY NAMES cards from
    # `rows` every 45s without a page reload, so the section is never
    # staler than the last Pages deploy.
    worder = wl or (ws or {}).get("tickers") or []
    wjs = {"v": STATE_V,
           "ts": (ws or {}).get("ts")
           or dt.datetime.now(NY).isoformat(timespec="seconds"),
           "tickers": worder,
           "rows": _watch_enrich(worder, ws, intel, bd)}
    with open(os.path.join(out_dir, "watch.json"), "w") as f:
        json.dump(wjs, f, separators=(",", ":"))
    with open(os.path.join(out_dir, "tape.html"), "w") as f:
        f.write(TAPE_HTML)
    with open(os.path.join(out_dir, "manifest.webmanifest"), "w") as f:
        json.dump({"name": "IGNITION", "short_name": "IGNITION",
                   "start_url": "./", "display": "standalone",
                   "background_color": "#0b0d10", "theme_color": "#0b0d10",
                   "icons": [{"src": "icon.svg", "sizes": "any",
                              "type": "image/svg+xml"}]}, f)
    with open(os.path.join(out_dir, "icon.svg"), "w") as f:
        f.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                '<rect width="100" height="100" rx="22" fill="#0b0d10"/>'
                '<path d="M50 14c4 14-8 20-8 32a8 8 0 0016 0c0-6-3-9-3-14 '
                '10 6 17 16 17 27a22 22 0 11-44 0c0-19 18-27 22-45z" fill="#ff5a1f"/></svg>')
    open(os.path.join(out_dir, ".nojekyll"), "w").close()
    return os.path.join(out_dir, "index.html")
