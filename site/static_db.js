/* Hosted copy only (site/build.py inlines this). Stands in for the artifact database: each document is read from
   data/<path>.json and each collection from data/<name>.json (an array), re-read every 10 minutes so an open page
   keeps updating. Also warns when the last build is old, which means the update job has stalled. */
if (!window.claude) (function () {
  const POLL_MS = 10 * 60 * 1000, STALE_H = 3;
  const get = p => fetch('data/' + p + '.json', { cache: 'no-cache' }).then(r => {
    if (r.status === 404) return null;
    if (!r.ok) throw { code: 'http-' + r.status };
    return r.json();
  });
  const snap = v => ({ exists: v != null, data: () => v });
  const watch = (load, cb, err) => {
    const run = () => load().then(cb, e => { if (err) err(e); });
    run();
    setInterval(run, POLL_MS);
    return () => {};
  };
  const doc = p => ({ get: () => get(p).then(snap), onSnapshot: (cb, err) => watch(() => get(p).then(snap), cb, err) });
  const collection = name => {
    const q = { by: null, desc: false, n: 0 };
    const api = {
      orderBy(field, dir) { q.by = field; q.desc = dir === 'desc'; return api; },
      limit(n) { q.n = n; return api; },
      onSnapshot: (cb, err) => watch(() => get(name).then(list => {
        let docs = (list || []).slice();
        if (q.by) docs.sort((a, b) => (a[q.by] < b[q.by] ? -1 : a[q.by] > b[q.by] ? 1 : 0) * (q.desc ? -1 : 1));
        if (q.n) docs = docs.slice(0, q.n);
        return { docs: docs.map(snap) };
      }), cb, err)
    };
    return api;
  };
  window.claude = { use: () => Promise.resolve({ doc, collection }) };

  const checkStale = () => get('meta').then(m => {
    const hours = m && m.built ? (Date.now() - Date.parse(m.built)) / 3600000 : Infinity;
    let el = document.getElementById('stale');
    if (hours < STALE_H) { if (el) el.remove(); return; }
    if (!el) {
      el = document.createElement('div');
      el.id = 'stale'; el.className = 'notice'; el.setAttribute('role', 'status');
      const main = document.querySelector('.main');
      if (main) main.insertBefore(el, main.children[1] || null);
    }
    el.innerHTML = '<strong>This page hasn’t updated for ' + (isFinite(hours) ? Math.floor(hours) + ' hours' : 'a while') +
      '.</strong> The update job may be paused: open <a href="https://github.com/Lazlo25/roblox-tracker/actions/workflows/publish.yml">Publish site</a>, then Enable workflow or Run workflow.';
  }, () => {});
  document.addEventListener('DOMContentLoaded', () => { checkStale(); setInterval(checkStale, POLL_MS); });
})();
