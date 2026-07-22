#!/usr/bin/env node
/* Tape page checks for the watchlist-first pivot:
   1. the full inline <script> parses (a syntax error would kill the page);
   2. mergeWatch() — the watch.json -> chip merge — respects order, the
      8-name stream cap, the removed-set, and dedupes;
   3. the TREND/BURST core still behaves (smoke: the node-tested invariants
      from HANDOFF item 28 on three canonical shapes).
   Run: node tests/test_tape.js
*/
const {execSync} = require('child_process');
const path = require('path');
const py = `import sys; sys.path.insert(0, ${JSON.stringify(path.join(__dirname, '..'))}); from src.tape import TAPE_HTML; print(TAPE_HTML)`;
const html = execSync(`python3 -c '${py}'`, {maxBuffer: 1 << 24}).toString();
const script = html.split('<script>')[1].split('</script>')[0];

// 1 — parse the whole page script
new Function(script);
console.log('page script parses');

// 2 — mergeWatch behavior
const mergeSrc = script.split('/*MERGE-BEGIN*/')[1].split('/*MERGE-END*/')[0];
const mergeWatch = new Function(mergeSrc + '; return mergeWatch;')();
let m = mergeWatch(['AAA', 'BBB'], {}, ['ccc', 'AAA', 'DDD'], 8);
console.assert(JSON.stringify(m) === '["AAA","BBB","CCC","DDD"]', 'merge order/dedupe: ' + m);
m = mergeWatch(['AAA'], {BBB: 1}, ['BBB', 'CCC'], 8);
console.assert(JSON.stringify(m) === '["AAA","CCC"]', 'removed-set respected: ' + m);
m = mergeWatch(['A1','A2','A3','A4','A5','A6','A7'], {}, ['B1','B2','B3'], 8);
console.assert(m.length === 8 && m[7] === 'B1', 'stream cap 8: ' + m);
m = mergeWatch([], {}, null, 8);
console.assert(m.length === 0, 'null tickers tolerated');
console.log('mergeWatch OK');

// 3 — core smoke (item 28 invariants)
const core = script.split('/*CORE-BEGIN*/')[1].split('/*CORE-END*/')[0];
const env = new Function(core + `
  ; return {tapeStats, trendRaw, sticky, peakCand, burstScan, burstRead};`)();
const now = 10_000_000;
// healthy flow: steady $100/print every 500ms for 5 min
let buf = [];
for (let t = now - 300000; t <= now; t += 500) buf.push([t, 100, 5.0]);
let st = env.tapeStats(buf, now, 5.1);
let peak = env.peakCand(st);
console.assert(env.trendRaw(st, peak) === 'ALIVE', 'healthy = ALIVE');
// tape stops: 30s of silence -> DRY
let st2 = env.tapeStats(buf.filter(a => a[0] < now - 30000), now, 5.1);
console.assert(env.trendRaw(st2, peak) === 'DRY', 'silence = DRY');
// bleed to ~5% of peak -> DEAD band
let buf3 = buf.filter(a => a[0] < now - 240000);
for (let t = now - 240000; t <= now; t += 10000) buf3.push([t, 20, 4.9]);
let st3 = env.tapeStats(buf3, now, 5.1);
console.assert(['DEAD', 'FADING'].includes(env.trendRaw(st3, peak)),
  'bleed degrades: ' + env.trendRaw(st3, peak));
// sparse: 1 print/50s -> THIN, never a false DRAINED
let buf4 = [];
for (let t = now - 300000; t <= now; t += 50000) buf4.push([t, 40, 2.0]);
let st4 = env.tapeStats(buf4, now, 2.1);
console.assert(env.trendRaw(st4, env.peakCand(st4)) === 'THIN', 'sparse = THIN');
console.log('core smoke OK');
console.log('ALL TAPE TESTS PASS');
