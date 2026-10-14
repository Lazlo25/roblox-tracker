#!/usr/bin/env python3
"""Daily snapshot builder for the Roblox Monetization Tracker artifact.

  python3 tracker.py prepare --out day.json [--dump DIR] [--date YYYY-MM-DD]
      --dump   folder holding the artifact's existing `days` documents (ArtifactData list out_dir),
               used to send only new or renamed games' details.
      Reads the four public charts and writes the day document, printing a one-line summary starting with OK.
      Each game's CCU is its average since midnight Eastern (from the hourly collector) when it has enough hours of
      readings, else this run's snapshot; session length, like %, total players on Roblox and genre shares come from
      the same collector.

  python3 tracker.py refresh --date YYYY-MM-DD --dump DIR --out day.json [--day-file F] [--rt-file F]
      Re-applies the latest day figures to that date's stored document: the updates through the day, and the next
      day's final full-day figures.

  python3 tracker.py live --ccu DIR --out live.json
      The total-players document the page's Players on Roblox card reads (latest reading, today so far, every day
      RoTerminal has, the last 72 hours), from a local roblox-ccu checkout.
"""
import argparse, csv, datetime as dt, glob, html, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request, uuid
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ET = ZoneInfo('America/New_York')
CHARTS = [('te', 'top-earning'), ('mp', 'most-popular'), ('tt', 'top-trending'), ('uc', 'up-and-coming')]
CHART_URL = ('https://apis.roblox.com/explore-api/v1/get-sort-content?urlLocale=en_us&device=all&country=all'
             '&cpuCores=8&maxResolution=1920x1080&maxMemory=16384&networkType=4g&sortId=%s&sessionId=%s')
GAMES_URL = 'https://games.roblox.com/v1/games?universeIds='
DAY_URL = 'https://raw.githubusercontent.com/Lazlo25/roblox-ccu/main/data/daily/{date}.json'
RT_URL = 'https://raw.githubusercontent.com/Lazlo25/roblox-ccu/main/data/roterminal_daily.json'
AVG_MAX_AGE_H = 3      # a day-so-far file older than this is ignored (a finished day's file never goes stale)
LIVE_HOURS = 72        # hours of total-players readings in the live document


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


def load_rt(path=None):
    """RoTerminal's daily platform averages as saved by the hourly collector: {'days': [[day start, avg, peak, partial]]}."""
    try:
        return json.load(open(path, encoding='utf-8')) if path else get_json(RT_URL, tries=3)
    except Exception as e:
        print('warning: RoTerminal daily averages unavailable:', e)
        return None


def platform_for(date, rt):
    """Total players on Roblox for one Eastern date: RoTerminal's day average and peak (partial while the day runs)."""
    start = int(dt.datetime.combine(dt.date.fromisoformat(date), dt.time(0), ET).timestamp())
    for t, avg, peak, partial in (rt or {}).get('days') or []:
        if abs(t - start) < 3600:
            return {'avg': int(avg), 'peak': int(peak), 'partial': bool(partial)}
    return None


def apply_extras(doc, day, rt):
    """Session length (minutes per visit) and like % per game, total players on Roblox, and RoTerminal's genre shares."""
    games = (day or {}).get('games') or {}
    doc['sess'] = [(games.get(u) or {}).get('sess') for u in doc['u']]
    doc['like'] = [(games.get(u) or {}).get('like') for u in doc['u']]
    plat = platform_for(doc['date'], rt)
    if plat:
        doc['plat'] = plat
    if day and day.get('genres'):
        doc['rtg'] = {'shares': day['genres'], 'hours': day.get('genre_hours', 0), 'through_et': day.get('through_et', '')}
    return 'EXTRAS: session for %d games, like %% for %d, total players %s, genre shares %s' % (
        sum(x is not None for x in doc['sess']), sum(x is not None for x in doc['like']),
        '{:,} avg'.format(plat['avg']) if plat else 'missing', 'from %d hours' % doc['rtg']['hours'] if 'rtg' in doc else 'missing')


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


def cmd_prepare(a):
    date = a.date or dt.datetime.now(ET).strftime('%Y-%m-%d')
    avg, avg_status = load_day_averages(date)
    charts = {}
    for key, sort_id in CHARTS:
        try:
            charts[key] = public_chart(sort_id)
        except Exception as e:
            print('warning: public %s fetch failed: %s' % (sort_id, e))
            charts[key] = []

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
        if cur and cur != known.get(u):
            meta[u] = cur

    # Per-game history charts: each game's total visits now (new visits per day = the change between snapshots), and
    # current players and total visits for every earlier tracker game that is no longer tracked.
    vis = [int(det[u]['visits']) if u in det and det[u].get('visits') else None for u in union]
    gone = sorted({u for d in days for u in (d.get('u') or d.get('ids') or [])} - set(union), key=int)
    gone_det = game_details(gone)
    extra = {u: [int(g.get('playing') or 0), int(g['visits']) if g.get('visits') else None] for u, g in gone_det.items()}

    doc = {'v': 2, 'date': date, 'takenAt': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           'source': 'public', 'u': union, 'ccu': ccu, 'vis': vis, 'x': extra,
           'charts': {k: [index[u] for u in charts[k]] for k, _ in CHARTS if charts.get(k)},
           'partial': [], 'meta': meta}
    ccu_line = apply_day_averages(doc, avg, avg_status)
    extras_line = apply_extras(doc, avg, load_rt())
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
    print('OK date=%s games=%d fallen_tracked=%d %s meta=%d missing_details=%d untracked_history=%d/%d bytes=%d dubit=%s'
          % (date, members, len(fallen), ' '.join(parts), len(meta), missing, len(extra), len(gone), len(body.encode()), 'ok' if dubit else 'missing'))
    print(ccu_line)
    print(extras_line)


def cmd_refresh(a):
    """Re-apply the latest day figures to an already-recorded day (updates through the day, and finalizing yesterday)."""
    day_doc = next((d for d in load_days(a.dump) if d['date'] == a.date), None)
    if day_doc is None or 'u' not in day_doc:
        sys.exit('NO DAY DOCUMENT for %s in %s' % (a.date, a.dump))
    avg, status = load_day_averages(a.date, path=a.day_file)
    if avg is None:
        sys.exit('NO UPDATE: the day-average file for %s is %s' % (a.date, status))
    line = apply_day_averages(day_doc, avg, status)
    extras = apply_extras(day_doc, avg, load_rt(a.rt_file))
    with open(a.out, 'w', encoding='utf-8') as f:
        f.write(json.dumps(day_doc, ensure_ascii=False, separators=(',', ':')))
    print('OK refresh date=%s through=%s' % (a.date, avg['through_et']))
    print(line)
    print(extras)


def cmd_live(a):
    """Total players on Roblox for the page's card, from a local roblox-ccu checkout."""
    now = dt.datetime.now(dt.timezone.utc)
    rt = load_rt(os.path.join(a.ccu, 'data', 'roterminal_daily.json'))
    if not rt:
        sys.exit('NO LIVE DATA: data/roterminal_daily.json is missing in %s' % a.ccu)
    hourly = []
    for i in range(LIVE_HOURS // 24 + 2, -1, -1):
        path = os.path.join(a.ccu, 'data', 'platform', '%s.csv' % (now - dt.timedelta(days=i)).date())
        if os.path.exists(path):
            hourly += [[r['utc'], int(r['players'])] for r in csv.DictReader(open(path, encoding='utf-8'))]
    cut = (now - dt.timedelta(hours=LIVE_HOURS)).strftime('%Y-%m-%dT%H:%M:%SZ')
    hourly = [h for h in hourly if h[0] >= cut]
    daily = [[dt.datetime.fromtimestamp(t, ET).strftime('%Y-%m-%d'), int(avg), int(peak), int(partial)] for t, avg, peak, partial in rt['days']]
    doc = {'at': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'source': 'RoTerminal (roterminal.co)', 'rt_fetched': rt.get('fetched_utc', ''),
           'now': hourly[-1] if hourly else None, 'daily': daily, 'hourly': hourly}
    with open(a.out, 'w', encoding='utf-8') as f:
        f.write(json.dumps(doc, separators=(',', ':')))
    print('OK live now=%s days=%d hours=%d' % (doc['now'], len(daily), len(hourly)))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--out', required=True)
    p.add_argument('--dump')
    p.add_argument('--date')
    r = sub.add_parser('refresh')
    r.add_argument('--date', required=True)
    r.add_argument('--dump', required=True)
    r.add_argument('--out', required=True)
    r.add_argument('--day-file', help="local copy of roblox-ccu's data/daily/<date>.json")
    r.add_argument('--rt-file', help="local copy of roblox-ccu's data/roterminal_daily.json")
    q = sub.add_parser('live')
    q.add_argument('--ccu', required=True, help='a roblox-ccu checkout')
    q.add_argument('--out', required=True)
    a = ap.parse_args()
    {'prepare': cmd_prepare, 'refresh': cmd_refresh, 'live': cmd_live}[a.cmd](a)


if __name__ == '__main__':
    main()
