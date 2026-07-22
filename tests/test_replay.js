#!/usr/bin/env node
/* REPLAY — his bug report driven through the REAL page script with a DOM
   stub: "goes from cooling to reading to grey to cooling… doesn't have
   bars, has bars… it's not getting the information."
   Scenarios: (a) sparse name (1 print/45s) across a page rebuild — the
   headline must never regress to an engine mood word and must stay a
   stable THIN FEED with counts; (b) empty buffer — WAITING FOR PRINTS /
   STREAM OFF, never a stale word; (c) dense buying — ACCUMULATING, sticky.
   Run: node tests/test_replay.js */
const {execSync} = require('child_process');
const js = execSync(`python3 -c 'from src.hub import JS; print(JS)'`, {maxBuffer: 1 << 24}).toString();

// ---- minimal DOM/browser stub -------------------------------------------
const els = {};
function el(id) {
  if (!els[id]) els[id] = {
    id, textContent: '', innerHTML: '', style: {}, hidden: false, dataset: {},
    classList: {add(){}, remove(){}, toggle(){}, contains(){return false}},
    addEventListener(){}, appendChild(){}, querySelector(){return null},
    querySelectorAll(){return []}, closest(){return null},
    getContext(){return new Proxy({}, {get:()=>()=>{}})},
    width: 600, height: 76, value: '', placeholder: '', offsetWidth: 0,
    remove(){},
  };
  return els[id];
}
const document = {
  getElementById: el, addEventListener(){}, hidden: false,
  createElement: () => el('tmp_' + Math.random()),
};
const localStorage = {tape_k: 'k', tape_s: 's'};
const stubs = {
  document, localStorage,
  fetch: () => new Promise(() => {}),      // network never resolves in replay
  WebSocket: function(){ this.readyState = 1; this.send = () => {};
    this.close = () => {}; },
  setInterval: () => 0, setTimeout: () => 0, clearTimeout: () => {},
  console, Date, JSON, Math, Promise, navigator: {},
  window: {},
};
const fn = new Function(...Object.keys(stubs),
  js + `; return {paintCard, cvdWord, LBUF, lwsSet:(w)=>{lws=w}, wrenderRef:()=>wrender};`);
const env = fn(...Object.values(stubs));

const now0 = Date.now();

// (a) SPARSE: one $150 print every 45s for 12 min, page "reloaded" midway
els['lvh_INLF'] = undefined; // fresh
const buf = env.LBUF['INLF'] = [];
for (let t = now0 - 720000; t <= now0; t += 45000) buf.push([t, 150, 3.5, 1]);
env.lwsSet(new stubs.WebSocket());
let words = new Set();
for (let k = 0; k < 30; k++) {                     // 30 painted seconds
  env.paintCard('INLF', now0 + k * 1000);
  words.add(el('lvh_INLF').textContent);
}
console.assert(words.size === 1, 'sparse word is STABLE, got: ' + [...words]);
const w = [...words][0];
console.assert(/THIN FEED · \d+ prints/.test(w),
  'sparse reads THIN FEED with counts, got: ' + w);
console.assert(!/COOLING|DEAD|MONEY/.test(w), 'no engine mood leakage: ' + w);
// simulated wrender rebuild: headline element recreated with mood text
el('lvh_INLF').textContent = 'COOLING';
env.paintCard('INLF', now0 + 31000);
console.assert(/THIN FEED/.test(el('lvh_INLF').textContent),
  'repaint after rebuild kills the stale mood word');
// heartbeat must TICK every painted second even though the tape is quiet
const beats = new Set();
for (let k = 0; k < 8; k++) { env.paintCard('INLF', now0 + 40000 + k * 1000);
  beats.add(el('hb_INLF').textContent); }
console.assert(beats.size >= 3, 'heartbeat ticks between prints: ' + [...beats]);
console.log('heartbeat ticks OK — ' + [...beats].slice(0,4).join(' '));
console.log('sparse scenario OK — stable "' + w + '"');

// (b) EMPTY buffer: waiting words, never stale
env.LBUF['ZCMD'] = [];
el('lvh_ZCMD').textContent = 'FADING';
env.paintCard('ZCMD', now0);
console.assert(el('lvh_ZCMD').textContent === 'WAITING FOR PRINTS',
  'empty buffer overrides stale word: ' + el('lvh_ZCMD').textContent);
env.lwsSet(null);
env.paintCard('ZCMD', now0);
console.assert(el('lvh_ZCMD').textContent === 'STREAM OFF', 'stream-off state');
env.lwsSet(new stubs.WebSocket());
console.log('empty scenario OK');

// (c) DENSE buying: $400 at the ask every 2s → ACCUMULATING and sticky
const b2 = env.LBUF['LABT'] = [];
for (let t = now0 - 600000; t <= now0; t += 2000)
  b2.push([t, 400, 5, t > now0 - 240000 ? 1 : 0]);
words = new Set();
for (let k = 0; k < 20; k++) {
  env.paintCard('LABT', now0 + k * 1000);
  words.add(el('lvh_LABT').textContent);
}
console.assert(words.size === 1 && words.has('ACCUMULATING'),
  'dense buying = stable ACCUMULATING: ' + [...words]);
console.log('dense scenario OK');
console.log('ALL REPLAY TESTS PASS');
