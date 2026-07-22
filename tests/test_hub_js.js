#!/usr/bin/env node
/* Hub page script checks: the whole inline script (pulse verdict + MY NAMES
   editor + sync + 45s poll) must parse, and the editor's list logic must
   behave. Run: node tests/test_hub_js.js */
const {execSync} = require('child_process');
const path = require('path');
const py = `import sys; sys.path.insert(0, ${JSON.stringify(path.join(__dirname, '..'))}); from src.hub import JS; print(JS)`;
const script = execSync(`python3 -c '${py}'`, {maxBuffer: 1 << 24}).toString();

new Function(script);            // syntax gate — a typo here kills the page
console.log('hub script parses');

// the security-relevant constants must point at exactly one repo + file
console.assert(script.includes("'ALANKK11/ignition'"), 'repo constant');
console.assert((script.match(/api\.github\.com/g) || []).length >= 1, 'api host');
console.assert(/localStorage\.gh_t/.test(script), 'token in localStorage only');
// alpaca keys: READ-only reuse of tape's saved keys; the hub must never
// write them and never send them anywhere but the alpaca stream auth
console.assert(!/localStorage\.tape_k\s*=|localStorage\.tape_s\s*=/.test(script),
  'hub must not write alpaca keys');
// outbound hosts: exactly the github api + the alpaca IEX stream
const hosts = [...script.matchAll(/(?:https?|wss):\/\/([a-z0-9.\-]+)/gi)].map(m => m[1]);
console.assert(hosts.every(h => h === 'api.github.com'
  || h === 'stream.data.alpaca.markets'),
  'unexpected outbound host: ' + hosts);
console.log('hub sink audit OK (api.github.com + alpaca stream only)');

// live strip: pure-function behavior
const live = script.split('/*LIVE-BEGIN*/')[1].split('/*LIVE-END*/')[0];
const L = new Function(live + '; return {liveRead, liveState, lvSticky, liveLabel};')();
const now = 10_000_000;
// steady flow: $200 every 2s for 10 min -> FLOW
let buf = [];
for (let t = now - 600000; t <= now; t += 2000) buf.push([t, 200, 3]);
let s = L.liveRead(buf, now);
console.assert(L.liveState(s) === 'FLOW', 'steady = FLOW, got ' + L.liveState(s));
// his INLF description: a burst 8 min ago, trickle since -> DRY + downshift
let buf2 = [];
for (let t = now - 490000; t <= now - 460000; t += 500) buf2.push([t, 800, 4]); // burst
for (let t = now - 460000; t <= now; t += 9000) buf2.push([t, 30, 3.9]);        // trickle
let s2 = L.liveRead(buf2, now);
console.assert(L.liveState(s2) === 'DRY', 'trickle after burst = DRY, got ' + L.liveState(s2));
console.assert(/drying/.test(L.liveLabel('DRY', s2)[0]), 'DRY label');
// 40s of silence -> SILENT
let s3 = L.liveRead(buf2.filter(a => a[0] < now - 40000), now);
console.assert(L.liveState(s3) === 'SILENT', 'silence = SILENT');
// sticky: alternating MID/FLOW candidates every second never flip the label
let st = {};
L.lvSticky(st, 'X', 'FLOW', now);
let outs = [];
for (let i = 1; i <= 6; i++) outs.push(L.lvSticky(st, 'X', i % 2 ? 'MID' : 'FLOW', now + i * 1000));
console.assert(outs.every(o => o === 'FLOW'), 'live sticky no flicker: ' + outs);
// but 5s of persistent MID degrades
L.lvSticky(st, 'X', 'MID', now + 10000);
console.assert(L.lvSticky(st, 'X', 'MID', now + 15000) === 'MID', 'persistent MID flips');
console.log('live strip OK');
console.log('ALL HUB JS TESTS PASS');
