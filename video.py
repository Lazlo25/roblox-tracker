#!/usr/bin/env python3
"""Gameplay-video acquisition for long-running hits (standard library; Pillow for contact sheets; ffmpeg for frames).

A game qualifies once, when all of these hold on the latest day:
  - it is in Most Popular's top MP_TOP or Top Earning's top TE_TOP;
  - its CCU baseline has been stable for a year (videos/hist_index.json, built from Rolimons daily history);
  - it is a long-time resident: either it was already in that top group on the first recorded day of the chart and is at
    least MIN_AGE days old, or it has stayed in that group for MIN_AGE days in a row.

  python3 video.py index --hist DIR              build videos/hist_index.json from Rolimons daily history files (DIR/<universe>.json)
  python3 video.py candidates [--date D]          list qualifying games without a finished video -> videos/queue.json
  python3 video.py roblox [--date D]              download each queued game's Roblox preview video (if it has one) and cut contact
                                                  sheets into work/videos/<universe>/; games without one are marked youtube_needed
  python3 video.py youtube [--limit N]            for youtube_needed games: pick the most-watched 8+ minute YouTube video with yt-dlp,
                                                  cut contact sheets from its first 20 minutes, then delete the file
  python3 video.py hist-todo --out F              top-100 games still without Rolimons history -> F ([[universe, place], ...])
  python3 video.py ingest                         read work/videos/<universe>/video_labels.json into profiles/<universe>.json
"""
import argparse, datetime as dt, glob, gzip, json, os, re, shutil, statistics, subprocess, sys, tempfile, urllib.request, urllib.parse
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ET = ZoneInfo('America/New_York')
DAYS = os.path.join(HERE, 'db-copy', 'days')
VID = os.path.join(HERE, 'videos')
STATE = os.path.join(VID, 'state.json')
INDEX = os.path.join(VID, 'hist_index.json')
QUEUE = os.path.join(VID, 'queue.json')
WORK = os.path.join(HERE, 'work', 'videos')
PROFILES = os.path.join(HERE, 'profiles')
MP_TOP, TE_TOP, MIN_AGE = 50, 100, 547   # 547 days = 1.5 years
BAND = (0.5, 2.0)                        # every month's median CCU within half to double the year's median
YT_MIN_SECONDS = 480
LABEL_KEYS = ['loop_observed', 'pacing', 'hud_density', 'visual_polish', 'camera', 'monetization_visible', 'retention_hooks',
              'social_visible', 'session_depth', 'notable', 'matches_page', 'confidence']


def load(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)


def get(url, binary=False, tries=4, ua='Mozilla/5.0'):
    import time
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': ua})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            return data if binary else json.loads(data)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def days_docs():
    out = []
    for f in sorted(glob.glob(os.path.join(DAYS, '*.json'))):
        if re.search(r'\d{4}-\d{2}-\d{2}\.json$', f):
            d = load(f, None)
            if d and isinstance(d.get('u'), list) and d.get('charts'):
                out.append(d)
    return out


# ---------------------------------------------------------------- stability index
def stable_baseline(c):
    """(stable, lo, hi) from a daily average-CCU list. The final (partial) day is dropped; twelve 30-day windows are
    compared with the median of their medians. Needs 300 readings in the 360 days and 20 in every window."""
    c = c[:-1][-360:]
    if len(c) < 360 or sum(x is not None for x in c) < 300:
        return False, None, None
    meds = []
    for k in range(12):
        w = [x for x in c[len(c) - 30 * (k + 1): len(c) - 30 * k] if x is not None]
        if len(w) < 20:
            return False, None, None
        meds.append(statistics.median(w))
    m = statistics.median(meds)
    if m <= 0:
        return False, None, None
    lo, hi = min(meds) / m, max(meds) / m
    return BAND[0] <= lo and hi <= BAND[1], round(lo, 2), round(hi, 2)


def cmd_index(a):
    idx = load(INDEX, {})
    for f in sorted(glob.glob(os.path.join(a.hist, '*.json'))):
        h = load(f, None)
        if not h or not h.get('c'):
            continue
        ok, lo, hi = stable_baseline(h['c'])
        idx[str(h['u'])] = {'stable': ok, 'lo': lo, 'hi': hi, 'days': len(h['c']), 'through': h.get('through')}
    save(INDEX, idx)
    print('OK index games=%d stable=%d' % (len(idx), sum(1 for v in idx.values() if v['stable'])))


# ---------------------------------------------------------------- which games need Rolimons history
HIST_TE, HIST_MP = 100, 100   # a game gets stored history once it is in either top group
HIST_MIN_AGE = 365            # ...and only if the game is at least a year old, so its history is worth storing


def cmd_hist_todo(a):
    """[[universe, place], ...] for games now in Top Earning's or Most Popular's top 100 that are at least a year old, have no history yet and are on Rolimons."""
    days = days_docs()
    latest, idx = days[-1], load(INDEX, {})
    meta = {}
    for d in days:
        for u, m in (d.get('meta') or {}).items():
            meta[u] = {**meta.get(u, {}), **m}
    top = [latest['u'][i] for i in latest['charts'].get('te', [])[:HIST_TE]] + [latest['u'][i] for i in latest['charts'].get('mp', [])[:HIST_MP]]
    today = dt.date.fromisoformat(latest['date'])
    old_enough = lambda u: meta.get(u, {}).get('c') and (today - dt.date.fromisoformat(meta[u]['c'])).days >= HIST_MIN_AGE
    missing = [u for u in dict.fromkeys(top) if u not in idx and meta.get(u, {}).get('pl') and old_enough(u)]
    listed = get('https://api.rolimons.com/games/v1/gamelist', ua='roblox-tracker personal research')['games']
    todo = [[u, meta[u]['pl']] for u in missing if meta[u]['pl'] in listed]
    with open(a.out, 'w') as f:
        json.dump(todo, f)
    print('OK hist-todo need=%d (not on Rolimons: %d)' % (len(todo), len(missing) - len(todo)))


# ---------------------------------------------------------------- candidates
def tenure(days, u, chart, top):
    """(incumbent, run): in the chart's top group on its first recorded day, and days in a row in it up to the latest day."""
    first = next((d for d in days if chart in d['charts']), None)
    if not first:
        return False, 0
    incumbent = u in [first['u'][i] for i in first['charts'][chart][:top]]
    run = 0
    for d in reversed(days):
        if chart not in d['charts']:
            continue
        if u in [d['u'][i] for i in d['charts'][chart][:top]]:
            run += 1
        else:
            break
    return incumbent, run


def cmd_candidates(a):
    days = days_docs()
    if a.date:
        days = [d for d in days if d['date'] <= a.date]
    latest = days[-1]
    meta = {}
    for d in days:
        for u, m in (d.get('meta') or {}).items():
            meta[u] = {**meta.get(u, {}), **m}
    today = dt.date.fromisoformat(latest['date'])
    idx, state = load(INDEX, {}), load(STATE, {})
    pools = [('mp', MP_TOP), ('te', TE_TOP)]
    on = {}
    for chart, top in pools:
        for i in latest['charts'].get(chart, [])[:top]:
            on.setdefault(latest['u'][i], []).append(chart)
    queue, waiting, skipped = [], [], {'unstable': 0, 'too_new': 0, 'done': 0}
    for u, charts in on.items():
        m = meta.get(u, {})
        age = (today - dt.date.fromisoformat(m['c'])).days if m.get('c') else 0
        resident = False
        for chart in charts:
            incumbent, run = tenure(days, u, chart, dict(pools)[chart])
            if (incumbent and age >= MIN_AGE) or run >= MIN_AGE:
                resident = True
        if not resident:
            skipped['too_new'] += 1
            continue
        if u not in idx:
            waiting.append({'u': u, 'name': m.get('n', u), 'why': 'needs Rolimons history'})
            continue
        if not idx[u]['stable']:
            skipped['unstable'] += 1
            continue
        if (state.get(u) or {}).get('status') in ('labeled', 'no_video'):
            skipped['done'] += 1
            continue
        queue.append({'u': u, 'name': m.get('n', u), 'place': m.get('pl', ''), 'charts': charts, 'age_days': age,
                      'status': (state.get(u) or {}).get('status', 'queued')})
    save(QUEUE, {'date': latest['date'], 'queue': queue, 'waiting': waiting})
    print('OK candidates date=%s on_charts=%d queue=%d waiting_for_history=%d skipped=%s' % (latest['date'], len(on), len(queue), len(waiting), skipped))


# ---------------------------------------------------------------- frames and sheets
def find_ffmpeg():
    p = shutil.which('ffmpeg')
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def duration(ff, path):
    r = subprocess.run([ff, '-i', path], capture_output=True, text=True)
    m = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', r.stderr)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0


def cut_sheets(path, out, n_frames, start=0.0, span=None):
    """n_frames evenly spaced frames from [start, start+span] and the contact sheets they go on (12 frames each)."""
    from PIL import Image, ImageDraw
    ff = find_ffmpeg()
    if not ff:
        sys.exit('NO FFMPEG: install ffmpeg (or pip install imageio-ffmpeg)')
    total = duration(ff, path)
    span = min(span or total, max(0.0, total - start))
    if span <= 0:
        return 0, total
    os.makedirs(out, exist_ok=True)
    frames = []
    for i in range(n_frames):
        t = start + span * (i + 0.5) / n_frames
        f = os.path.join(out, 'f_%02d.jpg' % i)
        subprocess.run([ff, '-y', '-v', 'error', '-ss', '%.2f' % t, '-i', path, '-frames:v', '1', '-vf', 'scale=320:-2', f], check=False)
        if os.path.exists(f):
            frames.append((t, f))
    for s in range(0, len(frames), 12):
        group = frames[s:s + 12]
        sheet = Image.new('RGB', (4 * 322, 3 * 200), (31, 31, 31))
        draw = ImageDraw.Draw(sheet)
        for j, (t, f) in enumerate(group):
            x, y = (j % 4) * 322, (j // 4) * 200
            sheet.paste(Image.open(f).convert('RGB').resize((320, 180)), (x, y + 18))
            draw.text((x + 4, y + 3), '%d:%02d' % (t // 60, t % 60), fill='white')
        sheet.save(os.path.join(out, 'sheet_%d.jpg' % (s // 12 + 1)), 'JPEG', quality=78)
    for _, f in frames:
        os.remove(f)
    return len(frames), total


# ---------------------------------------------------------------- Roblox preview videos
def roblox_video_ids(u):
    r = get('https://games.roblox.com/v2/games/%s/media' % u)
    return [m['videoId'] for m in r.get('data', []) if m.get('assetType') == 'GamePreviewVideo' and m.get('approved') and m.get('videoId')]


def download_asset(asset_id, dest):
    r = get('https://assetdelivery.roblox.com/v2/assetId/%s' % asset_id)
    loc = next(x['location'] for x in r['locations'])
    data = get(loc, binary=True)
    if data[:2] == b'\x1f\x8b':
        data = gzip.decompress(data)
    with open(dest, 'wb') as f:
        f.write(data)


def cmd_roblox(a):
    q, state = load(QUEUE, {}).get('queue', []), load(STATE, {})
    done = 0
    for g in q:
        u = g['u']
        if (state.get(u) or {}).get('status') in ('frames_ready', 'labeled', 'no_video'):
            continue
        try:
            ids = roblox_video_ids(u)
        except Exception as e:
            print('skip', u, 'media lookup failed:', e)
            continue
        if not ids:
            state[u] = {'status': 'youtube_needed', 'at': dt.date.today().isoformat()}
            continue
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'v.webm')
            try:
                download_asset(ids[0], path)
                n, total = cut_sheets(path, os.path.join(WORK, u), 24)
            except Exception as e:
                print('skip', u, 'video failed:', e)
                continue
        meta = {'u': u, 'name': g['name'], 'source': 'roblox', 'video_id': ids[0], 'seconds': round(total), 'frames': n}
        save(os.path.join(WORK, u, 'meta.json'), meta)
        state[u] = {'status': 'frames_ready', 'source': 'roblox', 'video_id': ids[0], 'at': dt.date.today().isoformat()}
        done += 1
        print('frames', u, g['name'], '%d frames from %ds' % (n, total))
    save(STATE, state)
    print('OK roblox sheets=%d youtube_needed=%d' % (done, sum(1 for s in state.values() if s['status'] == 'youtube_needed')))


# ---------------------------------------------------------------- YouTube (run on the Mac: cloud servers are usually blocked)
def pick_youtube(name):
    q = 'ytsearch15:%s roblox gameplay' % name
    r = subprocess.run(['yt-dlp', '--dump-single-json', '--flat-playlist', '--no-warnings', q], capture_output=True, text=True, timeout=180)
    if r.returncode:
        raise RuntimeError(r.stderr[-200:])
    words = [w for w in re.findall(r'\w+', name.lower()) if len(w) > 2][:3]
    best = None
    for e in json.loads(r.stdout).get('entries', []):
        if (e.get('duration') or 0) < YT_MIN_SECONDS or not e.get('view_count'):
            continue
        title = (e.get('title') or '').lower()
        if words and not all(w in title for w in words[:2]):
            continue
        if not best or e['view_count'] > best['view_count']:
            best = e
    return best


def cmd_youtube(a):
    state = load(STATE, {})
    names = {g['u']: g['name'] for g in load(QUEUE, {}).get('queue', [])}
    todo = [u for u, s in state.items() if s['status'] == 'youtube_needed' and u in names][:a.limit]
    for u in todo:
        try:
            v = pick_youtube(names[u])
        except Exception as e:
            print('skip', u, 'search failed:', e)
            continue
        if not v:
            state[u] = {'status': 'no_video', 'at': dt.date.today().isoformat()}
            print('no suitable video for', u, names[u])
            continue
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'v.mp4')
            # The tv/mweb clients give a plain 360p-480p file; yt-dlp's default client is refused with 403 from here.
            r = subprocess.run(['yt-dlp', '--extractor-args', 'youtube:player_client=tv,mweb', '-f', 'b[height<=480]/bv*[height<=480]+ba/b',
                                '--merge-output-format', 'mp4', '--no-playlist', '--no-warnings', '-o', path, 'https://www.youtube.com/watch?v=' + v['id']], capture_output=True, text=True, timeout=1800)
            if r.returncode or not os.path.exists(path):
                print('skip', u, 'download failed:', r.stderr[-200:])
                continue
            n, total = cut_sheets(path, os.path.join(WORK, u), 36, start=30.0, span=1200.0)
        save(os.path.join(WORK, u, 'meta.json'), {'u': u, 'name': names[u], 'source': 'youtube', 'video_id': v['id'], 'title': v.get('title'),
                                                  'views': v.get('view_count'), 'seconds': round(total), 'frames': n})
        state[u] = {'status': 'frames_ready', 'source': 'youtube', 'video_id': v['id'], 'at': dt.date.today().isoformat()}
        print('frames', u, names[u], '%d frames from %s' % (n, v.get('title')))
    save(STATE, state)
    print('OK youtube games=%d' % len(todo))


# ---------------------------------------------------------------- labels
def cmd_ingest(a):
    state, ok, bad = load(STATE, {}), 0, 0
    for f in sorted(glob.glob(os.path.join(WORK, '*', 'video_labels.json'))):
        u = os.path.basename(os.path.dirname(f))
        row, meta = load(f, None), load(os.path.join(os.path.dirname(f), 'meta.json'), {})
        missing = [k for k in LABEL_KEYS if not isinstance(row, dict) or k not in row]
        if missing:
            print('skipped', u, 'missing', missing)
            bad += 1
            continue
        p = load(os.path.join(PROFILES, u + '.json'), None)
        if p is None:
            print('skipped', u, 'no profile')
            bad += 1
            continue
        row.update({'source': meta.get('source'), 'video_id': meta.get('video_id'), 'at': dt.date.today().isoformat()})
        p['video'] = row
        with open(os.path.join(PROFILES, u + '.json'), 'w', encoding='utf-8') as pf:
            json.dump(p, pf, ensure_ascii=False)  # same compact format analyze.py writes
        state[u] = {**state.get(u, {}), 'status': 'labeled'}
        shutil.rmtree(os.path.dirname(f), ignore_errors=True)
        ok += 1
    save(STATE, state)
    print('OK ingest labeled=%d skipped=%d' % (ok, bad))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('index').add_argument('--hist', required=True)
    for name in ('candidates', 'roblox'):
        sub.add_parser(name).add_argument('--date')
    sub.add_parser('youtube').add_argument('--limit', type=int, default=3)
    sub.add_parser('ingest')
    sub.add_parser('hist-todo').add_argument('--out', required=True)
    a = ap.parse_args()
    {'index': cmd_index, 'candidates': cmd_candidates, 'roblox': cmd_roblox, 'youtube': cmd_youtube, 'ingest': cmd_ingest, 'hist-todo': cmd_hist_todo}[a.cmd](a)


if __name__ == '__main__':
    main()
