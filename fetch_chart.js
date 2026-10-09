// Run in the built-in browser on roblox.com while signed in (needed for 18+ titles).
// Reads four All Devices / All Locations charts, signed in and signed out, and returns one line:
//   v2|<key>:<mode>:<count>:<fnv1a hash of the signed-in id list>:<payload>|...
// mode d: payload = the titles only signed-in viewers see, as position.universeId;...
//         (tracker.py rebuilds the full list from the public chart and checks the hash)
// mode f: payload = the full signed-in list, id,id,...  (used when the two views disagree)
// For a full-list run, change 'diff' on the last line to 'full'. (File name kept from v1 so the daily task's approvals still apply.)
await (async (MODE) => {
  const CHARTS = [['te', 'top-earning'], ['mp', 'most-popular'], ['tt', 'top-trending'], ['uc', 'up-and-coming']];
  const fnv = s => { let h = 0x811c9dc5; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; } return h.toString(16); };
  const sid = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now());
  async function list(sortId, credentials) {
    const base = 'https://apis.roblox.com/explore-api/v1/get-sort-content?urlLocale=en_us&device=all&country=all'
      + '&cpuCores=8&maxResolution=1920x1080&maxMemory=16384&networkType=4g&sortId=' + sortId + '&sessionId=' + sid;
    const ids = []; let tok = '', filters = '';
    for (let i = 0; i < 20; i++) {
      const res = await fetch(base + (tok ? '&pageToken=' + encodeURIComponent(tok) : ''), { credentials });
      if (!res.ok) throw new Error(sortId + ' HTTP ' + res.status);
      const r = await res.json();
      filters = r.appliedFilters || '';
      const games = r.games || [];
      for (const g of games) ids.push(String(g.universeId));
      tok = r.nextPageToken;
      if (!tok || !games.length) break;
    }
    return { ids, filters };
  }
  try {
    const out = ['v2'];
    for (const [key, sortId] of CHARTS) {
      const L = await list(sortId, 'include');
      if (!/device=all/.test(L.filters) || !/country=all/.test(L.filters)) return 'ERR|' + key + ' unexpected filters ' + L.filters;
      const full = L.ids.join(',');
      let mode = 'f', payload = full;
      if (MODE === 'diff') {
        const P = await list(sortId, 'omit');
        const pub = new Set(P.ids), rest = [], extra = [];
        L.ids.forEach((u, i) => { if (pub.has(u)) rest.push(u); else extra.push((i + 1) + '.' + u); });
        if (rest.join(',') === P.ids.join(',')) { mode = 'd'; payload = extra.join(';'); }
      }
      out.push([key, mode, L.ids.length, fnv(full), payload].join(':'));
    }
    return out.join('|');
  } catch (e) { return 'ERR|' + e; }
})('diff')
