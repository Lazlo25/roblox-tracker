#!/usr/bin/env python3
"""Daily snapshot builder for the Roblox Monetization Tracker artifact.

  python3 tracker.py check
      Prints today's local date, the run folder, and whether now is inside
      the snapshot window (12:00-20:00 local), so day-to-day CCU stays comparable.

  python3 tracker.py prepare --out day.json [--ids browser.txt] [--dump DIR] [--date YYYY-MM-DD] [--allow-partial]
      --ids    the one-line output of fetch_charts.js (signed-in charts, includes 18+ titles).
               Omit it to fall back to the public charts, which miss restricted titles.
      --dump   folder holding the artifact's existing `days` documents (ArtifactData list out_dir),
               used to send only new or renamed games' details.
      --allow-partial  use the public list for any chart whose browser data doesn't check out.
      Writes the day document and prints a one-line summary starting with OK. Each game's CCU is its average
      since midnight Eastern (from the hourly collector) when it has enough hours of readings, else this run's snapshot.

  python3 tracker.py refresh --date YYYY-MM-DD --dump DIR --out day.json
      Re-applies the latest day averages to that date's stored document: the evening updates, and the next day's
      final full-day figures.
"""
import argparse, datetime as dt, glob, html, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
WINDOW = (12, 20)  # local hours [start, end)
CHARTS = [('te', 'top-earning'), ('mp', 'most-popular'), ('tt', 'top-trending'), ('uc', 'up-and-coming')]
CHART_URL = ('https://apis.roblox.com/explore-api/v1/get-sort-content?urlLocale=en_us&device=all&country=all'
             '&cpuCores=8&maxResolution=1920x1080&maxMemory=16384&networkType=4g&sortId=%s&sessionId=%s')
GAMES_URL = 'https://games.roblox.com/v1/games?universeIds='
DAY_URL = 'https://raw.githubusercontent.com/Lazlo25/roblox-ccu/main/data/daily/{date}.json'
AVG_MAX_AGE_H = 3      # a day-so-far file older than this is ignored (a finished day's file never goes stale)


def fnv1a(s):
    h = 0x811c9dc5
    for b in s.encode():
        h ^= b
        h = (h * 0x01000193) & 0xffffffff
    return format(h, 'x')


def get_json(url, tries=6):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if i == tries - 1:
                raise
            if e.code == 429:  # rate limited: wait as long as Roblox asks, or back off 5, 10, 20, 40 s
                wait = e.headers.get('Retry-After')
                time.sleep(int(wait) if wait and wait.isdigit() else 5 * 2 ** i)
            else:
                time.sleep(2 * (i + 1))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def public_chart(sort_id):
    """A chart as an anonymous visitor sees it (no restricted titles)."""
    base = CHART_URL % (sort_id, uuid.uuid4())
    ids, tok = [], ''
    for _ in range(20):
        r = get_json(base + (('&pageToken=' + urllib.parse.quote(tok)) if tok else ''))
        games = r.get('games') or []
        ids += [str(g['universeId']) for g in games]
        tok = r.get('nextPageToken')
        if not tok or not games:
            break
    return ids


def game_details(ids):
    out = {}
    for i in range(0, len(ids), 50):
        if i:
            time.sleep(1)  # stay under the games API rate limit
        r = get_json(GAMES_URL + ','.join(ids[i:i + 50]))
        for g in r.get('data', []):
            out[str(g['id'])] = g
    missing = [u for u in ids if u not in out]
    for i in range(0, len(missing), 50):  # one retry for stragglers
        r = get_json(GAMES_URL + ','.join(missing[i:i + 50]))
        for g in r.get('data', []):
            out[str(g['id'])] = g
    return out


def load_day_averages(date, url_tpl=DAY_URL, path=None):
    """The hourly collector's averages for one Eastern date (midnight to `through_et`), or (None, reason).
    `path` reads a local copy instead (cloud runs clone roblox-ccu because they can't fetch from the web)."""
    try:
        d = json.load(open(path, encoding='utf-8')) if path else get_json(url_tpl.format(date=date), tries=3)
        made = dt.datetime.strptime(d['generated_utc'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc)
        if d.get('date') != date or not isinstance(d.get('games'), dict):
            raise ValueError('file is for %s' % d.get('date'))
    except Exception as e:
        return None, 'missing (%s)' % (str(e)[:80] or type(e).__name__)
    age_h = (dt.datetime.now(dt.timezone.utc) - made).total_seconds() / 3600
    if d.get('through_et') != '24:00' and age_h > AVG_MAX_AGE_H:
        return None, 'stale (generated %.1f h ago)' % age_h
    return d, 'ok'


def min_samples(day):
    """Hours of readings a game needs: 12 for a finished day, otherwise half the hours elapsed (at least 6)."""
    hh, mm = (int(x) for x in day['through_et'].split(':'))
    return 12 if hh >= 24 else max(6, int((hh + mm / 60) / 2))


def apply_day_averages(doc, day, status):
    """Set each game's CCU to its day average when it has enough hourly readings, else its snapshot reading."""
    snap = doc.get('snap') or list(doc['ccu'])
    need = min_samples(day) if day else None
    ccu, src, rng = [], [], []
    for i, u in enumerate(doc['u']):
        g = (day or {}).get('games', {}).get(u)
        if g and g.get('samples', 0) >= need:
            ccu.append(int(round(g['avg_ccu']))); src.append('a'); rng.append([g['min_ccu'], g['max_ccu'], g['samples']])
        else:
            ccu.append(snap[i]); src.append('s'); rng.append(None)
    doc.update(ccu=ccu, snap=snap, src=src, rng=rng)
    doc['avg24'] = {'status': status, 'generated_utc': (day or {}).get('generated_utc', ''), 'averaged': src.count('a'),
                    'through_et': (day or {}).get('through_et', ''), 'min_samples': need}
    if day is None:
        return 'CCU SOURCE: snapshot readings for every game; the day-average file is %s' % status
    return ('CCU SOURCE: average of today\'s hourly readings (midnight to %s ET) for %d games, snapshot for %d (fewer than %d hours of readings)'
            % (day['through_et'], src.count('a'), len(src) - src.count('a'), need))


DUBIT_URL = 'https://dubit.io/library/which-roblox-genres-get-the-most-plays'


def dubit_shares():
    """Dubit's share of Roblox plays by genre (their published table), or None if the page can't be read."""
    try:
        req = urllib.request.Request(DUBIT_URL, headers={'User-Agent': 'Mozilla/5.0'})
        page = urllib.request.urlopen(req, timeout=30).read().decode('utf-8', 'replace')
    except Exception as e:
        print('warning: Dubit page fetch failed:', e)
        return None
    shares = {}
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', page, re.S):
        cells = [html.unescape(re.sub('<[^>]+>', '', c)).strip() for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.S)]
        if len(cells) == 3 and cells[2].endswith('%'):
            try:
                shares[cells[1]] = float(cells[2].rstrip('%'))
            except ValueError:
                pass
    if len(shares) < 5:
        print('warning: Dubit table not found on the page')
        return None
    span = re.search(r'(\d{1,2} [A-Z][a-z]+ \d{4}) to (\d{1,2} [A-Z][a-z]+ \d{4})', html.unescape(re.sub('<[^>]+>', ' ', page)))
    return {'shares': shares, 'from': span.group(1) if span else '', 'to': span.group(2) if span else '', 'url': DUBIT_URL}


def parse_browser(path):
    line = open(path, encoding='utf-8').read().strip()
    parts = line.split('|')
    if parts[0] != 'v2':
        sys.exit('BAD BROWSER FILE: expected a line starting with v2| (got %r...)' % line[:60])
    entries = {}
    for seg in parts[1:]:
        key, mode, count, h, payload = seg.split(':', 4)
        entries[key] = (mode, int(count), h, payload)
    return entries


def rebuild(entry, pub):
    """Full signed-in chart from the browser entry; True when it matches the browser's checksum."""
    mode, count, h, payload = entry
    if mode == 'f':
        ids = payload.split(',') if payload else []
    else:
        ids = list(pub)
        extra = sorted((int(p), u) for p, u in (x.split('.') for x in payload.split(';') if x))
        for pos, u in extra:
            ids.insert(pos - 1, u)
    return ids, len(ids) == count and len(set(ids)) == len(ids) and fnv1a(','.join(ids)) == h


def find_day(obj, depth=0):
    """The day document inside an exported file, whatever wrapper it was saved with."""
    if isinstance(obj, dict):
        if isinstance(obj.get('date'), str) and (isinstance(obj.get('u'), list) or isinstance(obj.get('ids'), list)):
            return obj
        if depth < 3:
            for v in obj.values():
                hit = find_day(v, depth + 1)
                if hit:
                    return hit
    return None


def load_days(dump):
    """Existing day documents from an ArtifactData out_dir dump, oldest first (one per date)."""
    found = {}
    if not dump:
        return []
    for f in glob.glob(os.path.join(dump, '**', '*.json'), recursive=True):
        try:
            d = find_day(json.load(open(f, encoding='utf-8')))
        except Exception:
            continue
        if d:
            found[d['date']] = d
    return [found[k] for k in sorted(found)]


def chart_lists(doc):
    """{chart key: [universe ids in order]} for a stored day (v1 docs hold Top Earning only)."""
    if 'u' in doc:
        return {k: [doc['u'][i] for i in idx] for k, idx in (doc.get('charts') or {}).items()}
    return {'te': list(doc['ids'])}


def cmd_check(_):
    now = dt.datetime.now()
    date = now.strftime('%Y-%m-%d')
    run = os.path.join(HERE, 'runs', date)
    os.makedirs(run, exist_ok=True)
    ok = WINDOW[0] <= now.hour < WINDOW[1]
    print('DATE=%s\nTIME=%s\nRUN_DIR=%s\nWINDOW=%s' % (date, now.strftime('%H:%M'), run, 'OK' if ok else 'SKIP'))


def cmd_prepare(a):
    date = a.date or dt.datetime.now().strftime('%Y-%m-%d')
    avg, avg_status = load_day_averages(date)
    pub = {}
    for key, sort_id in CHARTS:
        try:
            pub[key] = public_chart(sort_id)
        except Exception as e:
            print('warning: public %s fetch failed: %s' % (sort_id, e))
            pub[key] = []

    charts, partial, restricted, verified = {}, [], set(), set()
    if a.ids:
        entries, bad = parse_browser(a.ids), []
        for key, _ in CHARTS:
            ids, ok = rebuild(entries[key], pub[key]) if key in entries else ([], False)
            if ok:
                charts[key] = ids
                if pub[key]:
                    restricted |= set(ids) - set(pub[key])
                    verified |= set(ids) & set(pub[key])
            else:
                bad.append(key)
        if bad and not a.allow_partial:
            print('CHART MISMATCH: %s. Rerun fetch_charts.js with \'full\' on its last line, save it, and run prepare again.' % ','.join(bad))
            sys.exit(3)
        for key in bad:
            charts[key] = pub[key]
            partial.append(key)
        if not restricted and any(pub.values()):
            print('warning: the browser lists have no titles beyond the public charts; the Roblox sign-in has probably lapsed. Recording as partial.')
            partial, verified = [k for k, _ in CHARTS], set()
    else:
        charts, partial = dict(pub), [k for k, _ in CHARTS]

    if len(charts.get('te') or []) < 100:
        sys.exit('CHART TOO SHORT: Top Earning has %d entries' % len(charts.get('te') or []))

    days = load_days(a.dump)
    union = []
    for key, _ in CHARTS:
        for u in charts.get(key) or []:
            if u not in union:
                union.append(u)
    # Fallen Heroes: games that left a chart within the last 3 days keep their CCU tracked; after that they stop.
    today = dt.date.fromisoformat(date)
    last_on = {}
    for d in days:
        if d['date'] < date:
            for key, ids in chart_lists(d).items():
                for u in ids:
                    last_on.setdefault(key, {})[u] = d['date']
    fallen = []
    for key, _ in CHARTS:
        now = set(charts.get(key) or [])
        for u, last in last_on.get(key, {}).items():
            if u not in now and u not in union and u not in fallen and (today - dt.date.fromisoformat(last)).days <= 3:
                fallen.append(u)
    members = len(union)
    union += fallen
    index = {u: i for i, u in enumerate(union)}
    det = game_details(union)

    known = {}
    for d in days:
        for u, m in (d.get('meta') or {}).items():
            known[u] = {**known.get(u, {}), **m}

    ccu, meta = [], {}
    for u in union:
        g = det.get(u)
        ccu.append(int(g.get('playing') or 0) if g else 0)
        cur = dict(known.get(u, {}))
        if g:
            cur['n'] = (g.get('name') or '').strip()
            cur['c'] = (g.get('created') or '')[:10]
            cur['pl'] = str(g.get('rootPlaceId') or '')
            cur['g'] = (g.get('genre_l1') or '').strip()
        if u in restricted:
            cur['r'] = 1
        elif u in verified:
            cur['r'] = 0
        if cur and cur != known.get(u):
            meta[u] = cur

    source = 'browser' if not partial else ('public' if len(partial) == len(CHARTS) else 'mixed')
    doc = {'v': 2, 'date': date, 'takenAt': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           'source': source, 'u': union, 'ccu': ccu,
           'charts': {k: [index[u] for u in charts[k]] for k, _ in CHARTS if charts.get(k)},
           'partial': partial, 'meta': meta}
    ccu_line = apply_day_averages(doc, avg, avg_status)
    dubit = dubit_shares()
    if dubit:
        doc['dubit'] = dubit
    body = json.dumps(doc, ensure_ascii=False, separators=(',', ':'))
    if len(body.encode()) > 250_000:
        sys.exit('DOC TOO LARGE: %d bytes' % len(body.encode()))
    with open(a.out, 'w', encoding='utf-8') as f:
        f.write(body)

    prev_days = [d for d in days if d['date'] < date]
    parts = []
    for key, _ in CHARTS:
        now = charts.get(key) or []
        prev = next((chart_lists(d)[key] for d in reversed(prev_days) if key in chart_lists(d)), None)
        if prev is None:
            parts.append('%s=%d(first day)' % (key, len(now)))
        else:
            parts.append('%s=%d(+%d/-%d)' % (key, len(now), len(set(now) - set(prev)), len(set(prev) - set(now))))
    missing = sum(1 for u in union if u not in det)
    print('OK date=%s source=%s games=%d fallen_tracked=%d %s meta=%d missing_details=%d bytes=%d partial=%s dubit=%s'
          % (date, source, members, len(fallen), ' '.join(parts), len(meta), missing, len(body.encode()), ','.join(partial) or '-', 'ok' if dubit else 'missing'))
    print(ccu_line)


def cmd_refresh(a):
    """Re-apply the latest day averages to an already-recorded day (evening updates, and finalizing yesterday)."""
    day_doc = next((d for d in load_days(a.dump) if d['date'] == a.date), None)
    if day_doc is None or 'u' not in day_doc:
        sys.exit('NO DAY DOCUMENT for %s in %s' % (a.date, a.dump))
    avg, status = load_day_averages(a.date, path=a.day_file)
    if avg is None:
        sys.exit('NO UPDATE: the day-average file for %s is %s' % (a.date, status))
    line = apply_day_averages(day_doc, avg, status)
    with open(a.out, 'w', encoding='utf-8') as f:
        f.write(json.dumps(day_doc, ensure_ascii=False, separators=(',', ':')))
    print('OK refresh date=%s through=%s' % (a.date, avg['through_et']))
    print(line)


def cmd_patch18(a):
    """Add the signed-in-only (18+) titles to a day recorded from the public 1 PM charts, at the positions the browser reports."""
    doc = next((d for d in load_days(a.dump) if d['date'] == a.date), None)
    if doc is None or 'u' not in doc:
        sys.exit('NO DAY DOCUMENT for %s in %s' % (a.date, a.dump))
    entries, pub = parse_browser(a.ids), chart_lists(doc)
    fixed, restricted = [], set()
    new_lists = dict(pub)
    for key, _ in CHARTS:
        if key not in entries or key not in pub:
            continue
        mode, count, h, payload = entries[key]
        if mode == 'f':  # a full signed-in list: titles absent from the public list are the signed-in-only ones
            ids = payload.split(',') if payload else []
            restricted |= set(ids) - set(pub[key])
        else:  # only the signed-in-only titles, with their positions now; slot them into the 1 PM public list
            extra = sorted((int(p), u) for p, u in (x.split('.') for x in payload.split(';') if x))
            moved = {u for _, u in extra}
            ids = [u for u in pub[key] if u not in moved]
            for pos, u in extra:
                ids.insert(min(pos - 1, len(ids)), u)
            restricted |= moved
        new_lists[key] = ids
        fixed.append(key)
    if not fixed:
        sys.exit('NOTHING PATCHED: the browser line has none of this day\'s charts')
    add = sorted({u for ids in new_lists.values() for u in ids if u not in doc['u']})
    det = game_details(add) if add else {}
    snap = doc.get('snap') or list(doc['ccu'])
    for u in add:
        g = det.get(u) or {}
        doc['u'].append(u)
        doc['ccu'].append(int(g.get('playing') or 0))
        snap.append(int(g.get('playing') or 0))
        doc['meta'][u] = {'n': (g.get('name') or '').strip(), 'c': (g.get('created') or '')[:10],
                          'pl': str(g.get('rootPlaceId') or ''), 'g': (g.get('genre_l1') or '').strip(), 'r': int(u in restricted)}
    for u in restricted - set(add):
        doc['meta'][u] = {**doc['meta'].get(u, {}), 'r': 1}
    doc['snap'] = snap
    index = {u: i for i, u in enumerate(doc['u'])}
    doc['charts'] = {k: [index[u] for u in new_lists[k]] for k, _ in CHARTS if k in new_lists}
    doc['partial'] = [k for k in doc.get('partial', []) if k not in fixed]
    doc['source'] = 'browser' if not doc['partial'] else 'mixed'
    doc['patched18'] = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    avg, status = load_day_averages(a.date)
    ccu_line = apply_day_averages(doc, avg, status)
    with open(a.out, 'w', encoding='utf-8') as f:
        f.write(json.dumps(doc, ensure_ascii=False, separators=(',', ':')))
    print('OK patch18 date=%s charts=%s restricted=%d added=%d still_partial=%s' % (a.date, ','.join(fixed), len(restricted), len(add), ','.join(doc['partial']) or '-'))
    print(ccu_line)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('check')
    p = sub.add_parser('prepare')
    p.add_argument('--out', required=True)
    p.add_argument('--ids')
    p.add_argument('--dump')
    p.add_argument('--date')
    p.add_argument('--allow-partial', action='store_true')
    r = sub.add_parser('refresh')
    r.add_argument('--date', required=True)
    r.add_argument('--dump', required=True)
    r.add_argument('--out', required=True)
    r.add_argument('--day-file', help="local copy of roblox-ccu's data/daily/<date>.json")
    q = sub.add_parser('patch18')
    q.add_argument('--date', required=True)
    q.add_argument('--ids', required=True)
    q.add_argument('--dump', required=True)
    q.add_argument('--out', required=True)
    a = ap.parse_args()
    {'check': cmd_check, 'prepare': cmd_prepare, 'refresh': cmd_refresh, 'patch18': cmd_patch18}[a.cmd](a)


if __name__ == '__main__':
    main()
