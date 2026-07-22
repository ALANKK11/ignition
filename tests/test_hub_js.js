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
// alpaca keys: the ⚙ panel is the ONLY writer (user-typed input → the same
// localStorage slots TAPE uses), and keys go nowhere but the alpaca auth
const keyWrites = [...script.matchAll(/localStorage\.tape_[ks]\s*=/g)];
console.assert(keyWrites.length === 2, 'exactly the two panel writes: ' + keyWrites.length);
// outbound hosts: exactly the github api + the alpaca IEX stream
const hosts = [...script.matchAll(/(?:https?|wss):\/\/([a-z0-9.\-]+)/gi)].map(m => m[1]);
console.assert(hosts.every(h => h === 'api.github.com'
  || h === 'stream.data.alpaca.markets' || h === 'ws.finnhub.io'
  || h === 'finnhub.io'),
  'unexpected outbound host: ' + hosts);
// the finnhub URL must carry only the finnhub key, never the alpaca pair
console.assert(/ws\.finnhub\.io\/\?token='\+encodeURIComponent\(k\)/.test(script)
  && !/finnhub[^]*tape_k/.test(script.split('function fconnect')[1].split('fws.onopen')[0]),
  'finnhub gets only its own key');
console.log('hub sink audit OK (github + alpaca + finnhub, keys segregated)');

// live strip: pure-function behavior
const live = script.split('/*LIVE-BEGIN*/')[1].split('/*LIVE-END*/')[0];
const L = new Function(live + '; return {liveRead, liveState, lvSticky, liveLabel, liveHead, liveScore, bookLine};')();
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
// side classification: same flow, all prints at the ask → BUYERS IN;
// heavy prints at the bid → SELLERS HITTING (his exact ask: "money coming
// in on my side buying, or more selling")
let bbuf = [], sbuf = [];
for (let t = now - 600000; t <= now; t += 2000) { bbuf.push([t, 200, 3, 1]); sbuf.push([t, 200, 3, -1]); }
const sb = L.liveRead(bbuf, now), ss = L.liveRead(sbuf, now);
console.assert(sb.bshare > 0.99 && L.liveHead('FLOW', sb)[0] === 'BUYERS IN', 'buyers head');
console.assert(ss.bshare < 0.01 && L.liveHead('FLOW', ss)[0] === 'SELLERS HITTING', 'sellers head');
console.assert(/buy \/ ▼/.test(L.liveLabel('FLOW', sb, null, now)[0]), 'side split in label');
// book pressure: stacked ask reads as sellers queuing; stale quote ignored
console.assert(/sellers queuing/.test(L.bookLine({bs: 10, as: 40, t: now - 2000}, now)), 'ask stack');
console.assert(/buyers queuing/.test(L.bookLine({bs: 50, as: 10, t: now - 2000}, now)), 'bid stack');
console.assert(L.bookLine({bs: 10, as: 40, t: now - 60000}, now) === '', 'stale quote ignored');
// unclassified prints (side 0 / legacy 3-tuples) never fake a side
console.assert(s.bshare === null, 'no-side buf → neutral');
// headline is trade language; DRY with downshift shows the arrow
console.assert(L.liveHead('FLOW', s)[0] === 'MONEY IN', 'FLOW head');
console.assert(L.liveHead('SILENT', s3)[0] === 'STALLED', 'SILENT head');
console.assert(/DRAINING/.test(L.liveHead('DRY', s2)[0]), 'DRY head');
// ranking: a MONEY-IN name outranks a DRAINING one regardless of size
console.assert(L.liveScore('FLOW', {d30: 1}) > L.liveScore('DRY', {d30: 1e9}),
  'state ranks above raw dollars');
console.log('live strip + ranking OK');

// feed auto-select: SIP probed first, IEX fallback wired, entitlement cached
console.assert(script.includes("'wss://stream.data.alpaca.markets/v2/'+LFEED"),
  'feed-parameterized stream URL');
console.assert(/feed_sip/.test(script) && /feed_pref/.test(script),
  'feed pref + entitlement cache');
console.assert(/insufficient\|subscription/.test(script),
  'entitlement rejection fallback');
// unified keys panel: one save path writes the SAME storage tape uses
console.assert(/localStorage\.tape_k=k/.test(script)
  && /localStorage\.tape_s=s2/.test(script), 'panel writes shared key slots');
console.log('feed auto-select + keys panel OK');
console.log('ALL HUB JS TESTS PASS');
