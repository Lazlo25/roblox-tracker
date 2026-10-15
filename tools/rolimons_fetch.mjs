// Fetches Rolimons daily history (run on the Mac: Rolimons blocks cloud servers). A separate Chrome window (own profile) loads each game's public page,
// one every 5 s (robots.txt asks for 2; faster draws 429s), and its embedded history is reduced to Eastern-day values:
// c = average players, v = new visits that day. Usage: node tools/rolimons_fetch.mjs todo.json outDir [chromeProfileDir]
// todo.json: [[universeId, placeId], ...]. A Cloudflare challenge stops the run; it is never worked around.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const [todoPath, outDir, profileArg] = process.argv.slice(2);
const todo = JSON.parse(fs.readFileSync(todoPath, 'utf8'));
fs.mkdirSync(outDir, { recursive: true });
const PORT = 9333, here = path.dirname(new URL(import.meta.url).pathname);
const sleep = ms => new Promise(r => setTimeout(r, ms));

const chrome = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${profileArg || path.join(process.env.HOME, '.cache', 'rolimons-chrome-profile')}`,
  '--no-first-run', '--no-default-browser-check', '--window-size=900,700',
  'https://www.rolimons.com/game/9872472334'], { stdio: 'ignore' });

let page;
for (let i = 0; i < 30 && !page; i++) {
  await sleep(1000);
  try { page = (await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()).find(t => t.type === 'page'); } catch {}
}
if (!page) { console.log('NO CHROME'); process.exit(1); }
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener('open', r));
let id = 0; const waiting = new Map();
ws.addEventListener('message', e => { const m = JSON.parse(e.data); if (waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); } });
const send = (method, params) => new Promise(r => { const n = ++id; waiting.set(n, r); ws.send(JSON.stringify({ id: n, method, params })); });
const evaluate = async expr => {
  const m = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  if (m.result.exceptionDetails) throw new Error(m.result.exceptionDetails.exception?.description || 'eval error');
  return m.result.result.value;
};

// Wait for the first page to load normally; a Cloudflare challenge page means stop, not work around it.
let ready = false;
for (let i = 0; i < 40 && !ready; i++) {
  await sleep(1500);
  ready = await evaluate(`!!document.getElementById('game_history_json')`).catch(() => false);
}
if (!ready) { console.log('BLOCKED: the first page did not load normally (challenge?); stopping'); chrome.kill(); process.exit(2); }

const fn = `async (u, pl) => {
  const etDay = new Intl.DateTimeFormat('en-CA', {timeZone: 'America/New_York'});
  const r = await fetch('/game/' + pl, {credentials: 'include'});
  const html = await r.text();
  if (r.status === 403 || /challenge-platform|cf-chl/.test(html)) return {u, pl, blocked: r.status};
  if (!r.ok) return {u, pl, err: 'http ' + r.status};
  const m = html.match(/<script[^>]*id="game_history_json"[^>]*>([\\s\\S]*?)<\\/script>/);
  if (!m) return {u, pl, err: 'no history'};
  const h = JSON.parse(m[1]), T = h.timestamps, P = h.players, V = h.visits;
  if (!T || !T.length) return {u, pl, err: 'empty history'};
  const days = new Map();
  for (let i = 0; i < T.length; i++) {
    const d = etDay.format(new Date(T[i] * 1000));
    let e = days.get(d); if (!e) days.set(d, e = {s: 0, n: 0, lastV: null});
    if (P[i] != null) { e.s += P[i]; e.n++; }
    if (V[i] != null) e.lastV = V[i];
  }
  const keys = [...days.keys()].sort(), d0 = keys[0], dl = keys[keys.length - 1];
  const c = [], v = []; let prevV = null;
  for (let t = Date.parse(d0 + 'T12:00:00Z'); ; t += 864e5) {
    const d = new Date(t).toISOString().slice(0, 10), e = days.get(d);
    c.push(e && e.n ? Math.round(e.s / e.n) : null);
    const lv = e ? e.lastV : null;
    v.push(lv != null && prevV != null ? Math.max(0, lv - prevV) : null);
    if (lv != null) prevV = lv;
    if (d === dl) break;
  }
  return {u, pl, d0, through: dl, c, v, src: 'rolimons'};
}`;

let ok = 0, failed = 0, streak429 = 0;
for (const [u, pl] of todo) {
  const file = path.join(outDir, `${u}.json`);
  if (fs.existsSync(file)) continue;
  const t0 = Date.now();
  let res;
  try { res = await evaluate(`(${fn})(${JSON.stringify(u)}, ${JSON.stringify(pl)})`); } catch (e) { res = { u, pl, err: String(e).slice(0, 100) }; }
  if (res.blocked) { console.log(`BLOCKED at ${u} (status ${res.blocked}); stopping`); break; }
  if (res.err === 'http 429') {  // rate limited: wait a minute (longer each time in a row), then retry this game
    streak429 = (streak429 || 0) + 1;
    if (streak429 > 6) { console.log('STOP: still rate limited after 6 waits'); break; }
    console.log(`429 at ${u}; waiting ${60 * streak429}s`);
    await sleep(60000 * streak429); todo.push([u, pl]); continue;
  }
  streak429 = 0;
  if (res.err) { failed++; console.log(`skip ${u} ${pl}: ${res.err}`); }
  else { fs.writeFileSync(file, JSON.stringify(res)); ok++; }
  if ((ok + failed) % 25 === 0) console.log(`progress ok=${ok} failed=${failed}`);
  await sleep(Math.max(0, 5000 - (Date.now() - t0)));
}
console.log(`DONE ok=${ok} failed=${failed}`);
ws.close(); chrome.kill();
