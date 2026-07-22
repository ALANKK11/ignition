#!/usr/bin/env node
/* REPLAY — NFLV, the case that broke it: hyper-volatile, the free feed
   prints ~every 6s, but PRICE rips then rolls. The word MUST read the MOVE
   (RIPPING / ROLLING OVER / DUMPING), never "THIN", on the REAL page code. */
const {execSync} = require('child_process');
function A(c, m){ if(!c) throw new Error('FAIL: ' + m); }
const js = execSync(`python3 -c 'from src.hub import JS; print(JS)'`, {maxBuffer: 1 << 24}).toString();

const els = {};
function el(id){ if(!els[id]) els[id]={id,textContent:'',innerHTML:'',style:{},dataset:{},
  classList:{add(){},remove(){},toggle(){},contains(){return false}},
  addEventListener(){},appendChild(){},querySelector(){return null},
  querySelectorAll(){return[]},closest(){return null},
  getContext(){return new Proxy({},{get:()=>()=>{}})},width:600,height:76,
  value:'',placeholder:'',offsetWidth:0,remove(){}}; return els[id]; }
const stubs={document:{getElementById:el,addEventListener(){},hidden:false,createElement:()=>el('t'+Math.random())},
  localStorage:{tape_k:'k',tape_s:'s'},fetch:()=>new Promise(()=>{}),
  WebSocket:function(){this.readyState=1;this.send=()=>{};this.close=()=>{}},
  setInterval:()=>0,setTimeout:()=>0,clearTimeout:()=>{},console,Date,JSON,Math,Promise,navigator:{},window:{}};
const env=(new Function(...Object.keys(stubs),
  js+`;return {paintCard,moveRead,LBUF,lwsSet:(w)=>{lws=w}};`))(...Object.values(stubs));
env.lwsSet(new stubs.WebSocket());
const now=Date.now();

// vertical: +20% inside the last 5s (his literal words) -> RIPPING, top rank
let vb=[[now-4500,400,1.00,1],[now-2000,600,1.10,1],[now-300,900,1.20,1]];
let W=env.moveRead(vb,null,now);
A(W[0]==='RIPPING ↑','vertical 5s move = RIPPING, got '+W[0]+' | '+W[3]);
A(W[2]>2,'vertical rip ranks top: '+W[2]);
A(!/THIN|NO PRINTS|QUIET/.test(W[0]),'never thin while vertical');
console.log('vertical OK — "'+W[0]+'" · '+W[3]);

// steady rip: +18% over 30s, 1 print/6s -> RIPPING or RUNNING, never thin
let sb=[];let p=1.0;for(let t=now-30000;t<=now;t+=6000){p*=1.032;sb.push([t,500,+p.toFixed(4),1])}
W=env.moveRead(sb,null,now);
A(/RIPPING|RUNNING/.test(W[0]),'steady rip reads up, got '+W[0]);
A(!/THIN|QUIET/.test(W[0]),'sparse-but-moving is never thin: '+W[0]);
console.log('steady-rip OK — "'+W[0]+'"');

// rolled over: up +16% over 15s but last 5s -5% off the high -> ROLLING OVER
let rb=[[now-15000,300,1.00,1],[now-10000,400,1.15,1],[now-6000,500,1.22,1],
        [now-2000,600,1.18,-1],[now-200,700,1.16,-1]];
W=env.moveRead(rb,null,now);
A(W[0]==='ROLLING OVER','up-15s down-5s = ROLLING OVER, got '+W[0]+' | '+W[3]);
console.log('rollover OK — "'+W[0]+'" · '+W[3]);

// dumping: -18% over 15s
let db=[];p=2.0;for(let t=now-15000;t<=now;t+=5000){p*=0.94;db.push([t,800,+p.toFixed(4),-1])}
A(env.moveRead(db,null,now)[0]==='DUMPING ↓','dump = DUMPING');
console.log('dump OK');

// flat but ask stacked (quotes only) -> SELLERS STACKED
A(env.moveRead([[now-3000,300,1.0,0],[now-800,300,1.001,0]],{bs:10,as:40,t:now-800},now)[0]==='SELLERS STACKED','quote sellers');
console.log('quote-pressure OK');

// STABLE headline across 20 paints; heartbeat ticks; empty never stale
env.LBUF['NFLV']=sb; let words=new Set();
for(let k=0;k<20;k++){env.paintCard('NFLV',now+k*300);words.add(el('lvh_NFLV').textContent)}
A([...words].every(w=>/RIPPING|RUNNING/.test(w)),'stable while ripping: '+[...words]);
let beats=new Set();for(let k=0;k<6;k++){env.paintCard('NFLV',now+40000+k*1000);beats.add(el('hb_NFLV').textContent)}
A(beats.size>=3,'heartbeat ticks: '+[...beats]);
env.LBUF['ZCMD']=[];el('lvh_ZCMD').textContent='RIPPING ↑';env.paintCard('ZCMD',now);
A(el('lvh_ZCMD').textContent==='WAITING FOR PRINTS','empty overrides stale');
console.log('stability + heartbeat + empty OK');
console.log('ALL REPLAY TESTS PASS');
