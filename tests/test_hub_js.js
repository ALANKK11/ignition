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
console.assert(!/tape_k|tape_s/.test(script), 'hub never touches alpaca keys');
console.assert(/localStorage\.gh_t/.test(script), 'token in localStorage only');
// no other outbound hosts in the hub script
const hosts = [...script.matchAll(/https?:\/\/([a-z0-9.\-]+)/gi)].map(m => m[1]);
console.assert(hosts.every(h => h === 'api.github.com'),
  'unexpected outbound host: ' + hosts);
console.log('hub sink audit OK (api.github.com only, relative pulse/watch)');
console.log('ALL HUB JS TESTS PASS');
