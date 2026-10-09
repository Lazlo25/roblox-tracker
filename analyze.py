#!/usr/bin/env python3
"""Deep Analysis pipeline for the Roblox Monetization Tracker.

Run after tracker.py has recorded the day (see ANALYSIS_GUIDE.md for the full daily routine):
  python3 analyze.py collect  --date D [--budget 420] [--queue 60]
      Refresh every charted game's public page data (cached; slow-changing parts weekly or after an update),
      then build today's profiling queue: work/D/dossier_NN.md and work/D/sheet_NN.jpg (artwork contact sheets).
  python3 analyze.py ingest   --date D      Merge Claude's work/D/labels_*.json into the game profiles.
  python3 analyze.py stats    --date D      Pillar metrics, pattern scan, hypothesis re-test -> work/D/stats_report.md
  python3 analyze.py finalize --date D      Register work/D/proposals.json, merge work/D/narrative.json,
                                            write work/D/analysis.json (the artifact's analysis document).
Standard library only.
"""
import argparse, datetime as dt, glob, hashlib, json, math, os, re, subprocess, sys, threading, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tracker import game_details, load_days, chart_lists  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PROF, ANA, WORK = (os.path.join(HERE, d) for d in ('profiles', 'analysis', 'work'))
CHARTS = ['mp', 'te', 'tt', 'uc']
NAMES = {'te': 'Top Earning', 'mp': 'Most Popular', 'tt': 'Top Trending', 'uc': 'Up-and-Coming'}
PILLARS = {
    'mp': 'Sustained Dominance: higher positions, longer presence and higher peak positions on Most Popular.',
    'te': 'Monetization Efficiency: earning a high position with modest CCU (Monetization Score ranks position × CCU, lower is better).',
    'tt': 'Early Momentum: rapid upward movement on Top Trending, among games 0–4 months old only.',
    'uc': 'Breakout Popularity: higher positions within Up-and-Coming.',
}
PRIMARY = {'mp': 'mp_pos', 'te': 'te_eff', 'tt': 'tt_mom', 'uc': 'uc_pos'}
METRIC_NAMES = {
    'mp_pos': 'Most Popular position', 'mp_days': 'days on Most Popular', 'mp_peak': 'best Most Popular position',
    'te_eff': 'monetization efficiency (low position × CCU)', 'te_pos': 'Top Earning position',
    'tt_mom': 'daily position gain on Top Trending', 'tt_pos': 'Top Trending position', 'uc_pos': 'Up-and-Coming position',
}
TT_MAX_AGE = 122
UA = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}


# ---------------------------------------------------------------- shared data
def all_days():
    found = {}
    for d in load_days(os.path.join(HERE, 'db-copy')) + load_days(os.path.join(HERE, 'runs')):
        found.setdefault(d['date'], d)
    return [found[k] for k in sorted(found)]


def day_ctx(d):
    uids = d['u'] if 'u' in d else d['ids']
    partial = d.get('partial')
    if partial is None:
        partial = ['te'] if d.get('source') == 'public' else []
    return {'date': d['date'], 'lists': chart_lists(d), 'ccu': dict(zip(uids, d['ccu'])), 'partial': partial}


def replay_meta(days):
    meta = {}
    for d in days:
        for u, m in (d.get('meta') or {}).items():
            meta[u] = {**meta.get(u, {}), **m}
    return meta


def ppath(u):
    return os.path.join(PROF, u + '.json')


def load_profile(u):
    try:
        return json.load(open(ppath(u), encoding='utf-8'))
    except Exception:
        return {'u': u}


def save_profile(p):
    os.makedirs(PROF, exist_ok=True)
    tmp = ppath(p['u']) + '.tmp'
    json.dump(p, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False)
    os.replace(tmp, ppath(p['u']))


def dnum(s):
    return dt.date.fromisoformat(s[:10]).toordinal()


def members_of(ctx):
    out = []
    for k in ('te', 'mp', 'tt', 'uc'):
        for u in ctx['lists'].get(k, []):
            if u not in out:
                out.append(u)
    return out


def age_days(p, date):
    c = p.get('created')
    return dnum(date) - dnum(c) if c else None


# ---------------------------------------------------------------- collect
def api(url, tries=5):
    """GET JSON; back off on rate limits, give up at once on 'not found' style errors."""
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < tries - 1:
                wait = e.headers.get('Retry-After')
                time.sleep(int(wait) if wait and wait.isdigit() else 4 * 2 ** i)
            elif e.code >= 500 and i < tries - 1:
                time.sleep(2)
            else:
                raise
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2)


def paged(url_fn, key, cursor_key, pages):
    items, cur = [], None
    for _ in range(pages):
        r = api(url_fn(cur))
        items += r.get(key) or []
        cur = r.get(cursor_key)
        if not cur:
            return items, False
        time.sleep(0.25)
    return items, True


def refresh_page(p, date, groups):
    u = p['u']
    passes, _ = paged(lambda c: f'https://apis.roblox.com/game-passes/v1/universes/{u}/game-passes?passView=Full&pageSize=100'
                      + (f'&pageToken={urllib.parse.quote(c)}' if c else ''), 'gamePasses', 'nextPageToken', 3)
    p['passes'] = [{'name': x.get('name', ''), 'price': x.get('price')} for x in passes if x.get('isForSale')]
    prods, more = paged(lambda c: f'https://apis.roblox.com/developer-products/v2/universes/{u}/developerproducts?limit=100'
                        + (f'&cursor={urllib.parse.quote(c)}' if c else ''), 'developerProducts', 'nextPageCursor', 3)
    sale = [x for x in prods if x.get('IsForSale', True)]
    p['products'] = [{'name': x.get('displayName') or x.get('Name', ''), 'price': x.get('PriceInRobux')} for x in sale][:120]
    p['products_n'], p['products_more'] = len(sale), more
    badges, bmore = paged(lambda c: f'https://badges.roblox.com/v1/universes/{u}/badges?limit=100&sortOrder=Asc'
                          + (f'&cursor={urllib.parse.quote(c)}' if c else ''), 'data', 'nextPageCursor', 2)
    p['badges_n'], p['badges_more'] = len(badges), bmore
    media = api(f'https://games.roblox.com/v2/games/{u}/media').get('data') or []
    p['media'] = {'images': sum(1 for m in media if m.get('assetType') == 'Image'),
                  'videos': sum(1 for m in media if m.get('assetType') != 'Image'),
                  'video_titles': [m.get('videoTitle') for m in media if m.get('videoTitle')][:5]}
    p['page_at'], p['page_updated'] = date, p.get('updated')


def refresh_groups(profiles, groups, date, deadline):
    """Creator group sizes, slowly: the groups API rate-limits hard, so stop at the first refusal and retry next run."""
    ids = []
    for p in profiles.values():
        cr = p.get('creator') or {}
        gid = str(cr.get('id'))
        if cr.get('type') == 'Group' and cr.get('id') and gid not in ids:
            g = groups.get(gid)
            if not g or g.get('members') is None or dnum(date) - dnum(g['at']) > 14:
                ids.append(gid)
    done = 0
    for gid in ids:
        if time.time() > deadline:
            break
        try:
            r = api(f'https://groups.roblox.com/v1/groups/{gid}', tries=1)
            groups[gid] = {'members': r.get('memberCount'), 'verified': bool(r.get('hasVerifiedBadge')), 'at': date}
            done += 1
        except urllib.error.HTTPError as e:
            if e.code == 429:
                break
        except Exception:
            pass
        time.sleep(1.2)
    return done, len(ids) - done


def needs_page(p, date):
    return 'page_at' not in p or dnum(date) - dnum(p['page_at']) >= 7 or p.get('page_updated') != p.get('updated')


def priority(ctx, profiles, date):
    lists, rank = ctx['lists'], {}
    for u in members_of(ctx):
        p = profiles[u]
        age = age_days(p, date)
        tiers = []
        if u in lists.get('tt', []) and age is not None and age <= TT_MAX_AGE:
            tiers.append((0, lists['tt'].index(u)))
        if u in lists.get('uc', []):
            tiers.append((1, lists['uc'].index(u)))
        if u in lists.get('mp', [])[:50]:
            tiers.append((2, lists['mp'].index(u)))
        if u in lists.get('te', [])[:60]:
            tiers.append((3, lists['te'].index(u)))
        best = min((lists[k].index(u) for k in lists if u in lists[k]), default=999)
        tiers.append((4, best))
        rank[u] = min(tiers)
    return sorted(rank, key=lambda u: rank[u])


def fetch_image(url, path):
    if os.path.exists(path):
        return path
    for i in range(3):
        try:
            data = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=30).read()
            open(path, 'wb').write(data)
            return path
        except Exception:
            time.sleep(1 + i)
    return None


def build_queue(date, ctx, profiles, groups, n):
    order = priority(ctx, profiles, date)
    fresh = [u for u in order if 'llm' not in profiles[u]]
    changed = [u for u in order if 'llm' in profiles[u] and profiles[u]['llm'].get('desc_hash') != profiles[u].get('desc_hash')]
    queue = (fresh + changed)[:n]
    wd = os.path.join(WORK, date)
    img = os.path.join(wd, 'img')
    os.makedirs(img, exist_ok=True)
    for old in glob.glob(os.path.join(wd, 'dossier_*.md')) + glob.glob(os.path.join(wd, 'sheet_*.jpg')):
        os.remove(old)
    if not queue:
        json.dump([], open(os.path.join(wd, 'queue.json'), 'w'))
        return []
    icons, thumbs = {}, {}
    for i in range(0, len(queue), 50):
        r = api('https://thumbnails.roblox.com/v1/games/icons?returnPolicy=PlaceHolder&size=150x150&format=Png&isCircular=false&universeIds=' + ','.join(queue[i:i + 50]))
        for x in r.get('data') or []:
            if x.get('imageUrl'):
                icons[str(x['targetId'])] = x['imageUrl']
        time.sleep(0.4)
    for i in range(0, len(queue), 10):
        r = api('https://thumbnails.roblox.com/v1/games/multiget/thumbnails?countPerUniverse=3&defaults=true&size=384x216&format=Png&isCircular=false&universeIds=' + ','.join(queue[i:i + 10]))
        for x in r.get('data') or []:
            thumbs[str(x['universeId'])] = [t['imageUrl'] for t in x.get('thumbnails') or [] if t.get('imageUrl')][:3]
        time.sleep(0.4)
    items = []
    for i, u in enumerate(queue):
        label = 'Q%02d' % (i + 1)
        ic = fetch_image(icons[u], os.path.join(img, u + '_icon.png')) if u in icons else None
        th = [fetch_image(t, os.path.join(img, f'{u}_{j}.png')) for j, t in enumerate(thumbs.get(u, []))]
        items.append({'label': label, 'u': u, 'name': profiles[u].get('name', u), 'icon': ic, 'thumbs': [t for t in th if t]})
    for s in range(0, len(items), 5):
        spec = [{'label': f"{it['label']} · {it['name'][:60]}", 'icon': it['icon'], 'thumbs': it['thumbs']} for it in items[s:s + 5]]
        sp = os.path.join(wd, 'sheet_spec.json')
        json.dump(spec, open(sp, 'w', encoding='utf-8'), ensure_ascii=False)
        out = os.path.join(wd, 'sheet_%02d.jpg' % (s // 5 + 1))
        res = subprocess.run(['osascript', '-l', 'JavaScript', os.path.join(HERE, 'sheet.js'), sp, out], capture_output=True, text=True)
        if res.returncode != 0:
            print('warning: contact sheet failed:', res.stderr.strip()[:200])
    for s in range(0, len(items), 15):
        lines = []
        for it in items[s:s + 15]:
            lines.append(dossier(it['label'], profiles[it['u']], ctx, groups, date))
        open(os.path.join(wd, 'dossier_%02d.md' % (s // 15 + 1)), 'w', encoding='utf-8').write('\n\n'.join(lines))
    json.dump([{'label': it['label'], 'u': it['u'], 'name': it['name']} for it in items],
              open(os.path.join(wd, 'queue.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    return items


def fmt_n(n):
    if n is None:
        return '?'
    return f'{n / 1e9:.1f}B' if n >= 1e9 else f'{n / 1e6:.1f}M' if n >= 1e6 else f'{n / 1e3:.1f}K' if n >= 1e3 else str(n)


def dossier(label, p, ctx, groups, date):
    pos = ', '.join(f'{NAMES[k]} #{ctx["lists"][k].index(p["u"]) + 1}' for k in ('te', 'mp', 'tt', 'uc') if p['u'] in ctx['lists'].get(k, []))
    cr = p.get('creator') or {}
    g = groups.get(str(cr.get('id'))) if cr.get('type') == 'Group' else None
    day = (p.get('daily') or {}).get(date, {})
    up, down = day.get('up') or 0, day.get('down') or 0
    like = f'{100 * up / (up + down):.0f}% of {fmt_n(up + down)} votes' if up + down else '?'
    passes = sorted(p.get('passes') or [], key=lambda x: -(x.get('price') or 0))
    prods = p.get('products') or []
    media = p.get('media') or {}
    desc = re.sub(r'\s+', ' ', p.get('desc') or '').strip()
    return '\n'.join([
        f'## {label} · {p.get("name")} (universe {p["u"]})',
        f'Charts today: {pos} | Age: {age_days(p, date)} days | Genre: {p.get("genre") or "?"} / {p.get("subgenre") or "?"} | Max players: {p.get("max_players")}',
        f'Creator: {cr.get("type")} "{cr.get("name")}"' + (f' ({fmt_n(g.get("members"))} members{", verified" if g.get("verified") else ""})' if g else '')
        + f' | Visits {fmt_n(p.get("visits"))} | Favorites {fmt_n(p.get("favorites"))} | Likes {like} | Updated {p.get("updated", "")[:10]}',
        f'Game passes ({len(passes)}): ' + ('; '.join(f'{x["name"]} {x.get("price")}' for x in passes[:12]) or 'none'),
        f'Developer products ({p.get("products_n", 0)}{"+" if p.get("products_more") else ""}): ' + ('; '.join(f'{x["name"]} {x.get("price")}' for x in prods[:10]) or 'none'),
        f'Badges: {p.get("badges_n", "?")}{"+" if p.get("badges_more") else ""} | Page media: {media.get("images", "?")} images, {media.get("videos", 0)} videos'
        + (f' ({"; ".join(media.get("video_titles") or [])})' if media.get('video_titles') else ''),
        'Description: ' + (desc[:1400] + ('…' if len(desc) > 1400 else '') if desc else '(none)'),
    ])


def cmd_collect(a):
    date, t0 = a.date, time.time()
    days = [d for d in all_days() if d['date'] <= date]
    if not days:
        sys.exit('NO DAYS: run tracker.py first')
    ctx = day_ctx(days[-1])
    if ctx['date'] != date:
        print(f'note: latest recorded day is {ctx["date"]}; collecting for it')
        date = ctx['date']
    members = members_of(ctx)
    gpath = os.path.join(PROF, '_groups.json')
    os.makedirs(PROF, exist_ok=True)
    groups = json.load(open(gpath)) if os.path.exists(gpath) else {}
    det = game_details(members)
    votes = {}
    for i in range(0, len(members), 50):
        try:
            r = api('https://games.roblox.com/v1/games/votes?universeIds=' + ','.join(members[i:i + 50]))
            for v in r.get('data') or []:
                votes[str(v['id'])] = v
        except Exception as e:
            print('warning: votes', e)
        time.sleep(0.5)
    profiles = {}
    for u in members:
        p, g = load_profile(u), det.get(u)
        if g:
            desc = g.get('description') or ''
            cr = g.get('creator') or {}
            p.update({
                'name': (g.get('name') or '').strip(), 'place': str(g.get('rootPlaceId') or ''), 'created': (g.get('created') or '')[:10],
                'updated': g.get('updated') or '', 'genre': g.get('genre_l1') or '', 'subgenre': g.get('genre_l2') or '',
                'desc': desc, 'desc_hash': hashlib.sha1(desc.encode()).hexdigest()[:12], 'max_players': g.get('maxPlayers'),
                'vip': bool(g.get('createVipServersAllowed')), 'price': g.get('price'), 'avatar': g.get('universeAvatarType'),
                'visits': g.get('visits'), 'favorites': g.get('favoritedCount'),
                'creator': {'id': cr.get('id'), 'name': cr.get('name'), 'type': cr.get('type'), 'verified': bool(cr.get('hasVerifiedBadge'))},
            })
            v = votes.get(u) or {}
            p.setdefault('daily', {})[date] = {'updated': p['updated'], 'visits': p['visits'], 'fav': p['favorites'],
                                               'up': v.get('upVotes'), 'down': v.get('downVotes')}
        profiles[u] = p
        save_profile(p)
    todo = [u for u in priority(ctx, profiles, date) if 'name' in profiles[u] and needs_page(profiles[u], date)]

    def work(u):
        if time.time() - t0 > a.budget:
            return 'deferred'
        try:
            refresh_page(profiles[u], date, groups)
            save_profile(profiles[u])
            return 'done'
        except Exception as e:
            print('warning: page data for', u, e, flush=True)
            return 'error'
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        results = list(ex.map(work, todo))
    done, skipped = results.count('done'), results.count('deferred')
    gdone, gleft = refresh_groups(profiles, groups, date, t0 + a.budget + 120)
    json.dump(groups, open(gpath, 'w'))
    items = build_queue(date, ctx, profiles, groups, a.queue)
    wd = os.path.join(WORK, date)
    print(f'OK collect date={date} games={len(members)} page_refreshed={done} page_deferred={skipped} groups={gdone} groups_left={gleft} queue={len(items)} '
          f'dossiers={len(glob.glob(os.path.join(wd, "dossier_*.md")))} sheets={len(glob.glob(os.path.join(wd, "sheet_*.jpg")))} '
          f'seconds={time.time() - t0:.0f}')


# ---------------------------------------------------------------- ingest
LABEL_KEYS = ['loop', 'loop_archetype', 'objectives', 'progression', 'mechanics', 'social', 'competitive', 'themes',
              'monetization_signals', 'thumbnails', 'thumbnail_style', 'complexity', 'confidence']


def cmd_ingest(a):
    wd = os.path.join(WORK, a.date)
    queue = {q['label']: q['u'] for q in json.load(open(os.path.join(wd, 'queue.json'), encoding='utf-8'))}
    ok = bad = 0
    for f in sorted(glob.glob(os.path.join(wd, 'labels_*.json'))):
        try:
            rows = json.load(open(f, encoding='utf-8'))
        except Exception as e:
            print('BAD LABEL FILE', f, e)
            bad += 1
            continue
        for row in rows:
            u = row.get('u') or queue.get(row.get('label'))
            missing = [k for k in LABEL_KEYS if k not in row]
            if not u or missing:
                print('skipped label', row.get('label'), 'missing', missing)
                bad += 1
                continue
            p = load_profile(u)
            row = {k: v for k, v in row.items() if k not in ('label', 'u')}
            row.update({'at': a.date, 'desc_hash': p.get('desc_hash')})
            p['llm'] = row
            save_profile(p)
            ok += 1
    print(f'OK ingest labels={ok} skipped={bad}')


# ---------------------------------------------------------------- features
EMOJI = re.compile('[\U0001F000-\U0001FAFF☀-➿⬀-⯿]')
MECH = {
    'pets_eggs': r'\bpets?\b|\beggs?\b|\bhatch', 'rebirth': r'\brebirth|\bprestige\b', 'steal': r'\bsteal|\brob\b|\brobbing\b',
    'trade': r'\btrad(e|es|ing)\b', 'merge': r'\bmerg', 'tycoon_base': r'\btycoon\b|\bbase\b|\bplot\b',
    'build': r'\bbuild', 'obby': r'\bobby\b|\bparkour\b|\bobstacle', 'tower_defense': r'\btower defen[sc]e\b|\btd\b',
    'pvp_combat': r'\bpvp\b|\bduels?\b|\bfight|\bbattle|\bcombat\b|\barena\b|\bkill', 'anime': r'\banime\b',
    'brainrot': r'\bbrainrot', 'rng_luck': r'\brng\b|\broll|\bluck', 'fishing': r'\bfish', 'mining_digging': r'\bmin(e|es|ing)\b|\bdig|\bdrill|\bores?\b',
    'food_cooking': r'\bcook|\brestaurant|\bcafe\b|\bfood\b|\bsushi\b|\bbake|\bkitchen', 'horror': r'\bhorror\b|\bscary\b|\bnightmare|\bjumpscare',
    'survival': r'\bsurviv|\bnights?\b|\bzombie', 'vehicles': r'\bcars?\b|\bdriv|\brac(e|es|ing)\b|\bvehicle|\bdrift|\bplanes?\b',
    'sports': r'\bsoccer\b|\bfootball\b|\bbasketball\b|\bvolleyball\b|\bgolf\b|\bbaseball\b|\bsports?\b|\bball\b',
    'roleplay_life': r'\broleplay\b|\brp\b|\bhouse\b|\bfamily\b|\bcity\b|\blife\b|\bneighborhood', 'idle_afk': r'\bidle\b|\bafk\b|\boffline',
    'incremental': r'\+1\b|\bper (click|second|step)\b|\bclick|\bincremental|\b/s\b|\bper sec', 'quests': r'\bquests?\b|\bmissions?\b',
    'bosses_raids': r'\bboss|\braids?\b|\bdungeon', 'clans_teams': r'\bclans?\b|\bguilds?\b|\bteams?\b|\bsquads?\b|\bcrews?\b',
    'leaderboard': r'\bleaderboard|\branked\b|\bseason pass', 'daily_rewards': r'\bdaily\b|\bplaytime reward|\bfree reward|\bfree gift',
    'codes': r'\bcodes?\b', 'admin_abuse': r'\badmin abuse\b|\badmin (event|war)', 'event': r'\bevent\b|\blimited\b|\bhalloween\b|\bchristmas\b|\bsummer\b|\bweekend\b',
    'update_schedule': r'\bevery (mon|tues|wednes|thurs|fri|satur|sun)day\b|\bupdates? every\b|\bweekly updates?\b|\bevery week\b',
    'coop_friends': r'\bwith (your )?friends\b|\bco-?op\b|\b2 ?players?\b|\btwo players?\b|\bteam up\b|\bduo\b|\bsquad up\b',
    'collect_index': r'\bcollect|\bindex\b|\bcollection\b', 'upgrade': r'\bupgrad|\blevel up\b|\bevolve|\bevolution',
    'weapons': r'\bguns?\b|\bswords?\b|\bweapons?\b|\bshoot|\bsniper\b|\bknife\b', 'creatures': r'\banimals?\b|\bdino|\bcreatures?\b|\bdragons?\b|\bhorses?\b|\bcats?\b|\bdogs?\b|\bmonsters?\b',
    'fashion_avatar': r'\bdress\b|\bfashion\b|\boutfits?\b|\bmakeover\b|\bavatar\b', 'voice_social': r'\bvoice chat\b|\bvc\b|\bhangout\b|\bmeet people\b',
    'escape_flee': r'\bescape\b|\btsunami\b|\bflee\b|\brun from\b', 'sandbox_physics': r'\bsandbox\b|\bphysics\b|\bdestroy|\bsmash|\bbreak\b|\bnuke',
    'grow_garden': r'\bgrow\b|\bgarden|\bfarm|\bplant', 'tycoon_money': r'\bmoney\b|\bcash\b|\bcoins?\b|\bincome\b|\bearn\b',
}
CTA = {
    'cta_like': r'\blike the game\b|\blike (and|&) favou?rite|\bthumbs? up\b|\blikes? for\b|\blike for\b|\bleave a like\b|\bmore likes\b',
    'cta_favorite': r'\bfavou?rite', 'cta_group': r'\bjoin (the|our|my) (group|community)\b|\bgroup (rewards?|perks?|bonus|chest)',
    'cta_discord': r'\bdiscord\b', 'cta_social': r'\byoutube\b|\btiktok\b|\btwitter\b|\binstagram\b',
    'desc_update_notes': r'\bupdate\s*(log|notes)\b|\bnew update\b|\bv\d+\.\d+|\bpatch\b|\bchangelog',
}
PASS_CATS = {'pass_double': r'x2|2x|double|x3|3x|triple', 'pass_vip': r'\bvip\b|premium', 'pass_auto': r'\bauto', 'pass_luck': r'luck',
             'pass_speed': r'speed|fast', 'pass_storage': r'slot|storage|inventory|space|capacity|backpack', 'pass_admin': r'admin|command',
             'pass_cosmetic': r'radio|boombox|music|tag|title|trail|emote|skin|aura'}
PROD_CATS = {'prod_currency': r'money|cash|coins?|gems?|gold|bucks|tokens?|diamonds?', 'prod_luck': r'luck', 'prod_skip': r'skip|instant|teleport',
             'prod_server_wide': r'server|everyone|nuke|global|all players', 'prod_revive': r'revive|respawn|extra life',
             'prod_spin_crate': r'spin|crate|chest|box|pack|roll'}
ARCHETYPES = ['collect_upgrade_rebirth', 'idle_income_tycoon', 'steal_defend_base', 'incremental_plus1', 'hatch_collect_pets', 'rng_roll_collect',
              'merge_upgrade', 'obby_platformer', 'tower_defense', 'combat_pvp', 'wave_survival_coop', 'horror_escape', 'roleplay_social',
              'sports_competitive', 'racing_driving', 'simulation_job', 'sandbox_physics', 'party_minigames', 'story_adventure', 'other']
STYLES = ['gameplay', 'character_art', 'reward_flex', 'text_ad', 'event_banner', 'mixed']
SOCIALS = ['solo', 'coop', 'pvp', 'social_hub', 'mixed']
GENRES = ['Action', 'Adventure', 'Education', 'Entertainment', 'Obby & Platformer', 'Party & Casual', 'Puzzle', 'RPG',
          'Roleplay & Avatar Sim', 'Shooter', 'Shopping', 'Simulation', 'Social', 'Sports & Racing', 'Strategy', 'Survival',
          'Utility & Other']
FEATURE_LABELS = {}
# Traits that partly result from success itself (popular games pile up visits and favorites): report them, but as outcome-linked.
OUTCOME_LINKED = {'visits_log', 'favorites_log', 'votes_per_kvisit', 'group_members_log'}


def lg(x):
    return math.log10(x) if x and x > 0 else None


def med(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def features(p, date, ctx, creator_counts, groups):
    f = {}
    title, desc = p.get('name', ''), p.get('desc', '')
    text = (title + ' \n ' + desc).lower()
    age = age_days(p, date)
    f['age_days'] = age
    if p.get('updated'):
        f['days_since_update'] = max(0, dnum(date) - dnum(p['updated']))
    days = p.get('daily') or {}
    upd_values = {v.get('updated') for v in days.values() if v.get('updated')}
    if len(days) >= 3:
        f['updates_per_week'] = 7 * (len(upd_values) - 1) / max(1, dnum(max(days)) - dnum(min(days)))
    day = days.get(date) or (days[max(days)] if days else {})
    up, down = day.get('up') or 0, day.get('down') or 0
    f['like_ratio'] = up / (up + down) if up + down >= 50 else None
    f['visits_log'] = lg(p.get('visits'))
    f['favorites_log'] = lg(p.get('favorites'))
    f['fav_per_kvisit'] = 1000 * p['favorites'] / p['visits'] if p.get('visits') and p.get('favorites') is not None else None
    f['votes_per_kvisit'] = 1000 * (up + down) / p['visits'] if p.get('visits') and up + down else None
    f['max_players'] = p.get('max_players')
    f['vip_servers'] = int(bool(p.get('vip')))
    if 'page_at' in p:
        prices = [x['price'] for x in p.get('passes') or [] if x.get('price')]
        f['passes_n'] = len(p.get('passes') or [])
        f['passes_median_price'] = med(prices)
        f['passes_max_price'] = max(prices) if prices else None
        pp = [x['price'] for x in p.get('products') or [] if x.get('price')]
        f['products_n'] = p.get('products_n', 0)
        f['products_median_price'] = med(pp)
        f['badges_n'] = p.get('badges_n')
        f['media_images'] = (p.get('media') or {}).get('images')
        f['has_video'] = int(bool((p.get('media') or {}).get('videos')))
        names_p = ' '.join(x['name'] for x in p.get('passes') or []).lower()
        names_d = ' '.join(x['name'] for x in p.get('products') or []).lower()
        for k, rx in PASS_CATS.items():
            f[k] = int(bool(re.search(rx, names_p)))
        for k, rx in PROD_CATS.items():
            f[k] = int(bool(re.search(rx, names_d)))
    cr = p.get('creator') or {}
    f['creator_group'] = int(cr.get('type') == 'Group')
    f['creator_verified'] = int(bool(cr.get('verified')))
    g = groups.get(str(cr.get('id'))) if cr.get('type') == 'Group' else None
    f['group_members_log'] = lg(g.get('members')) if g else None
    f['creator_chart_games'] = creator_counts.get(str(cr.get('id')), 1)
    f['title_len'] = len(title)
    f['title_emoji'] = len(EMOJI.findall(title))
    f['title_bracket_tag'] = int(bool(re.search(r'[\[\(【]', title)))
    tags = ' '.join(re.findall(r'[\[\(【]([^\]\)】]*)[\]\)】]', title)).lower()
    f['title_update_tag'] = int(bool(re.search(r'upd|update|new|w\d|season|part', tags)))
    f['title_event_tag'] = int(bool(re.search(r'event|halloween|christmas|summer|winter|easter|🎃|👻|admin', tags + title.lower())))
    f['title_release_tag'] = int(bool(re.search(r'beta|alpha|early|release|test', tags)))
    f['title_plus1'] = int('+1' in title)
    letters = [c for c in title if c.isalpha()]
    f['title_caps_heavy'] = int(len(letters) >= 6 and sum(c.isupper() for c in letters) / len(letters) > 0.6)
    f['desc_len_log'] = lg(len(desc) + 1)
    f['desc_emoji'] = len(EMOJI.findall(desc))
    for k, rx in {**MECH, **CTA}.items():
        f[('mech_' if k in MECH else '') + k] = int(bool(re.search(rx, text)))
    if p.get('genre'):
        for g in set(GENRES) | {p['genre']}:
            f['genre=' + g] = int(p['genre'] == g)
    llm = p.get('llm')
    if llm:
        arch = llm.get('loop_archetype')
        for a in ARCHETYPES:
            f['loop=' + a] = int(arch == a)
        sty = llm.get('thumbnail_style')
        for s in STYLES:
            f['thumb=' + s] = int(sty == s)
        f['thumb_text_numbers'] = int(bool(llm.get('thumb_text_numbers')))
        f['complexity'] = llm.get('complexity')
        f['novelty'] = llm.get('novelty')
        for s in SOCIALS:
            f['social=' + s] = int(llm.get('social') == s)
        f['competitive_core'] = int(llm.get('competitive') == 'core')
    return f


# ---------------------------------------------------------------- statistics (no numpy)
def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def spearman(x, y):
    """rho and two-sided p (normal approximation)."""
    n = len(x)
    if n < 8:
        return None, None
    rx, ry = ranks(x), ranks(y)
    mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if not sxx or not syy:
        return None, None
    rho = sxy / math.sqrt(sxx * syy)
    z = rho * math.sqrt(n - 1)
    return rho, 2 * (1 - phi(abs(z)))


def rank_biserial(flag, y, min_group=8):
    """Effect of a binary trait on y: r in [-1, 1] (positive = trait holders score higher) and two-sided p."""
    g1 = [v for f, v in zip(flag, y) if f]
    g0 = [v for f, v in zip(flag, y) if not f]
    n1, n0 = len(g1), len(g0)
    if n1 < min_group or n0 < min_group:
        return None, None, n1
    r = ranks(g1 + g0)
    r1 = sum(r[:n1])
    u1 = r1 - n1 * (n1 + 1) / 2
    rb = 2 * u1 / (n1 * n0) - 1
    n = n1 + n0
    counts = {}
    for v in g1 + g0:
        counts[v] = counts.get(v, 0) + 1
    tie = sum(t ** 3 - t for t in counts.values())
    sd = math.sqrt(n1 * n0 / 12 * ((n + 1) - tie / (n * (n - 1))))
    z = (u1 - n1 * n0 / 2) / sd if sd else 0
    return rb, 2 * (1 - phi(abs(z))), n1


def bh(ps):
    """Benjamini-Hochberg q-values."""
    idx = sorted(range(len(ps)), key=lambda i: ps[i])
    q, prev = [0.0] * len(ps), 1.0
    for rank_, i in reversed(list(enumerate(idx, 1))):
        prev = min(prev, ps[i] * len(ps) / rank_)
        q[i] = prev
    return q


def is_binary(vals):
    return all(v in (0, 1) for v in vals)


def assoc(xs, ys):
    """Effect, p, kind and minority-group size for feature values xs against metric ys (pairs with None dropped)."""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 12:
        return None
    x, y = [a for a, _ in pairs], [b for _, b in pairs]
    if is_binary(x):
        e, p, n1 = rank_biserial(x, y)
        return None if e is None else {'effect': e, 'p': p, 'kind': 'binary', 'n': len(x), 'n_with': n1}
    if len(set(x)) < 3:
        return None
    e, p = spearman(x, y)
    return None if e is None else {'effect': e, 'p': p, 'kind': 'corr', 'n': len(x)}


def best_threshold(xs, ys, min_side=12):
    pairs = sorted((x, y) for x, y in zip(xs, ys) if x is not None and y is not None)
    if len(pairs) < 2 * min_side or len({x for x, _ in pairs}) < 6:
        return None
    best = None
    cuts = sorted({pairs[int(len(pairs) * q)][0] for q in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)})
    for c in cuts:
        flag = [x > c for x, _ in pairs]
        if sum(flag) < min_side or len(flag) - sum(flag) < min_side:
            continue
        e, p, _ = rank_biserial(flag, [y for _, y in pairs], min_side)
        if e is not None and (best is None or abs(e) > abs(best['effect'])):
            best = {'cut': c, 'effect': e, 'p': p}
    return best


# ---------------------------------------------------------------- the dataset
def build(date):
    days = [d for d in all_days() if d['date'] <= date]
    ctxs = [day_ctx(d) for d in days]
    latest = ctxs[-1]
    meta = replay_meta(days)
    groups = json.load(open(os.path.join(PROF, '_groups.json'))) if os.path.exists(os.path.join(PROF, '_groups.json')) else {}
    profiles = {}
    for f in glob.glob(os.path.join(PROF, '*.json')):
        if os.path.basename(f).startswith('_'):
            continue
        p = json.load(open(f, encoding='utf-8'))
        profiles[p['u']] = p
    members = members_of(latest)
    for u in members:
        if u not in profiles:
            m = meta.get(u, {})
            profiles[u] = {'u': u, 'name': m.get('n', u), 'created': m.get('c', ''), 'genre': m.get('g', '')}
    creator_counts = {}
    for u in members:
        cid = str((profiles[u].get('creator') or {}).get('id'))
        creator_counts[cid] = creator_counts.get(cid, 0) + 1
    feats = {u: features(profiles[u], latest['date'], latest, creator_counts, groups) for u in set(members) | {
        u for c in ctxs for k in c['lists'] for u in c['lists'][k] if u in profiles}}
    # positions by chart and date
    pos = {k: {} for k in CHARTS}
    for c in ctxs:
        for k in CHARTS:
            if k in c['lists'] and k not in c['partial']:
                for i, u in enumerate(c['lists'][k]):
                    pos[k].setdefault(u, {})[c['date']] = i + 1
    ccu = {c['date']: c['ccu'] for c in ctxs}
    # Each day's combined CCU of the 100 biggest games, so CCU can be compared across days as market share.
    ccu_total = {d: sum(sorted(v.values(), reverse=True)[:100]) or 1 for d, v in ccu.items()}
    return {'days': ctxs, 'latest': latest, 'profiles': profiles, 'feats': feats, 'pos': pos, 'ccu': ccu, 'ccu_total': ccu_total, 'groups': groups,
            'meta': meta, 'members': members, 'creator_counts': creator_counts}


def metrics(D):
    """Game-level pillar metrics (higher is always better) for the latest day's population of each chart."""
    L, date = D['latest'], D['latest']['date']
    out = {m: {} for m in METRIC_NAMES}
    for u in L['lists'].get('mp', []):
        out['mp_pos'][u] = -math.log(L['lists']['mp'].index(u) + 1)
    for u, by in D['pos']['mp'].items():
        out['mp_days'][u] = len(by)
        out['mp_peak'][u] = -math.log(min(by.values()))
    for u in L['lists'].get('te', []):
        vals = [-(math.log(p) + math.log(max(1, D['ccu'][d].get(u, 0)) / D['ccu_total'][d])) for d, p in D['pos']['te'].get(u, {}).items()]
        if vals:
            out['te_eff'][u] = sum(vals) / len(vals)
        out['te_pos'][u] = -math.log(L['lists']['te'].index(u) + 1)
    for u in L['lists'].get('tt', []):
        age = D['feats'].get(u, {}).get('age_days')
        if age is None or age > TT_MAX_AGE:
            continue
        out['tt_pos'][u] = -math.log(L['lists']['tt'].index(u) + 1)
        seq = sorted(D['pos']['tt'].get(u, {}).items())
        gains = [(a[1] - b[1]) / max(1, dnum(b[0]) - dnum(a[0])) for a, b in zip(seq, seq[1:])]
        if gains:
            out['tt_mom'][u] = sum(gains) / len(gains)
    for u in L['lists'].get('uc', []):
        out['uc_pos'][u] = -math.log(L['lists']['uc'].index(u) + 1)
    return out


def day_metric(D, metric, date):
    """Single-day cross-section of a metric, for checking whether a pattern holds day after day."""
    c = next(c for c in D['days'] if c['date'] == date)
    lst = lambda k: c['lists'].get(k, []) if k not in c['partial'] else []
    if metric in ('mp_pos', 'te_pos', 'uc_pos', 'tt_pos'):
        k = metric[:2]
        res = {u: -math.log(i + 1) for i, u in enumerate(lst(k))}
        if k == 'tt':
            res = {u: v for u, v in res.items() if (D['feats'].get(u, {}).get('age_days') or 999) <= TT_MAX_AGE}
        return res
    if metric == 'te_eff':
        return {u: -(math.log(i + 1) + math.log(max(1, c['ccu'].get(u, 0)))) for i, u in enumerate(lst('te'))}
    if metric == 'tt_mom':
        prev = [x for x in D['days'] if x['date'] < date and 'tt' in x['lists'] and 'tt' not in x['partial']]
        if not prev:
            return {}
        pp = {u: i + 1 for i, u in enumerate(prev[-1]['lists']['tt'])}
        gap = max(1, dnum(date) - dnum(prev[-1]['date']))
        return {u: (pp[u] - (i + 1)) / gap for i, u in enumerate(lst('tt'))
                if u in pp and (D['feats'].get(u, {}).get('age_days') or 999) <= TT_MAX_AGE}
    return {}


def creator_robust(D, feat, metric_vals, base, cut=None):
    """Recompute after keeping only each creator's best game, so one prolific creator can't drive the pattern."""
    best = {}
    for u, y in metric_vals.items():
        cid = str((D['profiles'].get(u, {}).get('creator') or {}).get('id', u))
        if cid not in best or y > metric_vals[best[cid]]:
            best[cid] = u
    us = list(best.values())
    xs = [D['feats'].get(u, {}).get(feat) for u in us]
    if cut is not None:
        xs = [None if x is None else int(x > cut) for x in xs]
    r = assoc(xs, [metric_vals[u] for u in us])
    if not r or base is None:
        return None
    return r['effect'] * base > 0 and abs(r['effect']) >= 0.5 * abs(base)


def age_stratified(D, feat, metric_vals):
    buckets = [(0, 14), (15, 42), (43, 84), (85, TT_MAX_AGE)]
    tot = wsum = 0
    for lo, hi in buckets:
        us = [u for u in metric_vals if lo <= (D['feats'].get(u, {}).get('age_days') or -1) <= hi]
        r = assoc([D['feats'].get(u, {}).get(feat) for u in us], [metric_vals[u] for u in us])
        if r:
            tot += r['effect'] * r['n']
            wsum += r['n']
    return tot / wsum if wsum else None


def all_feature_names(D):
    names = set()
    for f in D['feats'].values():
        names |= set(f)
    return sorted(names)


def scan(D, M, chart):
    rows = []
    for metric in ([PRIMARY[chart]] + (['mp_days', 'mp_peak'] if chart == 'mp' else ['tt_pos'] if chart == 'tt' else [])):
        vals = M[metric]
        if len(vals) < 12 or len(set(vals.values())) < 4:
            continue
        us = list(vals)
        for feat in all_feature_names(D):
            xs = [D['feats'].get(u, {}).get(feat) for u in us]
            r = assoc(xs, [vals[u] for u in us])
            if not r:
                continue
            r.update({'feature': feat, 'metric': metric})
            if r['kind'] == 'corr':
                r['threshold'] = best_threshold(xs, [vals[u] for u in us])
            rows.append(r)
    if not rows:
        return []
    for r, q in zip(rows, bh([r['p'] for r in rows])):
        r['q'] = q
    rows.sort(key=lambda r: (r['q'], -abs(r['effect'])))
    top = rows[:14]
    for r in top:
        r['creator_robust'] = creator_robust(D, r['feature'], M[r['metric']], r['effect'])
        if chart in ('tt', 'uc'):
            r['age_stratified'] = age_stratified(D, r['feature'], M[r['metric']])
    return top


# ---------------------------------------------------------------- hypotheses
HPATH = os.path.join(ANA, 'hypotheses.json')


def load_h():
    return json.load(open(HPATH, encoding='utf-8')) if os.path.exists(HPATH) else []


def save_h(hs):
    os.makedirs(ANA, exist_ok=True)
    json.dump(hs, open(HPATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def test_h(D, M, h):
    t = h['test']
    metric, feat, sign = t['metric'], t['feature'], 1 if t.get('direction', '+') == '+' else -1
    vals = M.get(metric) or {}
    us = list(vals)
    xs = [D['feats'].get(u, {}).get(feat) for u in us]
    if t.get('type') == 'threshold':
        xs = [None if x is None else int(x > t['cut']) for x in xs]
    r = assoc(xs, [vals[u] for u in us])
    if not r:
        return {'effect': None, 'n': len([x for x in xs if x is not None]), 'days': 0, 'note': 'not enough comparable games yet'}
    eff = r['effect']
    if t.get('strata') == 'age':
        s = age_stratified(D, feat, vals)
        eff = s if s is not None else eff
    p_one = r['p'] / 2 if eff * sign > 0 else 1 - r['p'] / 2
    agree = evals = 0
    for c in D['days']:
        dv = day_metric(D, metric, c['date'])
        if len(dv) < 12:
            continue
        dx = [D['feats'].get(u, {}).get(feat) for u in dv]
        if t.get('type') == 'threshold':
            dx = [None if x is None else int(x > t['cut']) for x in dx]
        dr = assoc(dx, list(dv.values()))
        if dr:
            evals += 1
            agree += dr['effect'] * sign > 0
    robust = creator_robust(D, feat, vals, eff, t['cut'] if t.get('type') == 'threshold' else None)
    s = 1 - p_one
    w_n = min(1.0, r['n'] / 120)
    w_t = 0.3 + 0.7 * min(1.0, evals / 45)  # time matters: ~45 days of consistent support to approach full confidence
    cons = agree / evals if evals >= 2 else 1.0
    conf = 50 + (100 * s - 50) * w_n * w_t * (0.6 + 0.4 * cons) * (1.0 if robust is not False else 0.75)
    counter = []
    if r['kind'] == 'binary':
        # Games that have the trait but sit at the wrong end: the bottom fifth for '+', the top fifth for '-'.
        ranked = sorted(us, key=lambda u: vals[u], reverse=sign < 0)
        has = dict(zip(us, xs))
        counter = [D['profiles'].get(u, {}).get('name', u) for u in ranked[:max(5, len(ranked) // 5)] if has.get(u) == 1][:3]
    return {'effect': round(eff, 3), 'p': round(r['p'], 4), 'n': r['n'], 'days': evals, 'consistency': round(cons, 2),
            'creator_robust': robust, 'confidence': round(max(1, min(95, conf))), 'counterexamples': counter}


def record(h, date, conf, extra):
    hist = [e for e in h.get('history', []) if e['date'] != date]
    prev = hist[-1]['conf'] if hist else None
    if h.get('status') == 'Retired':
        status = 'Retired'
    elif prev is None:
        status = 'New'
    else:
        status = 'Strengthened' if conf - prev >= 2 else 'Weakened' if conf - prev <= -2 else 'Unchanged'
        low = [e for e in hist[-2:] if e['conf'] < 30]
        if conf < 30 and len(low) == 2:
            status = 'Retired'
    hist.append({'date': date, 'conf': conf})
    h.update({'history': hist[-60:], 'confidence': conf, 'status': status, 'evidence': extra})


def retest_all(D, M, date):
    hs = load_h()
    for h in hs:
        if h.get('kind') == 'qualitative':
            last = h.get('history', [])
            conf = last[-1]['conf'] if last else h.get('confidence', 30)
            if not last or last[-1]['date'] != date:
                record(h, date, conf, h.get('evidence', {}))
            continue
        if h.get('status') == 'Retired':
            continue
        res = test_h(D, M, h)
        conf = res.pop('confidence', None)
        if conf is None:
            conf = (h.get('history') or [{'conf': 50}])[-1]['conf']
        record(h, date, conf, res)
    save_h(hs)
    return hs


# ---------------------------------------------------------------- report
def name(D, u):
    return D['profiles'].get(u, {}).get('name') or D['meta'].get(u, {}).get('n') or u


def traits(D, u):
    f, p = D['feats'].get(u, {}), D['profiles'].get(u, {})
    bits = [f'age {f.get("age_days")}d', p.get('genre') or '?']
    if f.get('days_since_update') is not None:
        bits.append(f'upd {f["days_since_update"]}d ago')
    if f.get('like_ratio'):
        bits.append(f'likes {100 * f["like_ratio"]:.0f}%')
    if 'passes_n' in f:
        bits.append(f'passes {f["passes_n"]}, products {f.get("products_n")}')
    llm = p.get('llm') or {}
    if llm.get('loop_archetype'):
        bits.append('loop ' + llm['loop_archetype'])
    return ', '.join(bits)


def fmt_assoc(r):
    s = f'{r["feature"]} vs {r["metric"]}: {"r" if r["kind"] == "binary" else "rho"}={r["effect"]:+.2f} n={r["n"]}'
    if r['kind'] == 'binary':
        s += f' (with={r["n_with"]})'
    s += f' p={r["p"]:.3f} q={r["q"]:.2f}'
    if r.get('threshold'):
        s += f' | best split at {r["threshold"]["cut"]:.3g}: r={r["threshold"]["effect"]:+.2f}'
    if r.get('creator_robust') is False:
        s += ' | NOT creator-robust'
    if r['feature'] in OUTCOME_LINKED:
        s += ' | outcome-linked (partly a result of success)'
    if r.get('age_stratified') is not None:
        s += f' | within-age effect {r["age_stratified"]:+.2f}'
    return s


def keywords(names):
    stop = set('the and for with your you from into are this that its not but can how who why what get got our out off all new update updated upd beta alpha event season part early access free game games roblox edition remastered test testing official version'.split())
    docs = []
    for n in names:
        plain = re.sub(r'\[[^\]]*\]|\([^)]*\)|【[^】]*】', ' ', n).lower()
        toks = [t.replace("'", '') for t in re.findall(r"\+1|[a-z][a-z0-9']*", plain)]
        docs.append([t for t in toks if t in ('rp', 'vs', 'td', '+1') or (len(t) >= 3 and t not in stop)])
    allw = {t for d in docs for t in d}
    fold = lambda t: t[:-1] if len(t) >= 4 and t.endswith('s') and t[:-1] in allw else t
    return [{fold(t) for t in d} for d in docs]


def keyword_stats(D, M, chart):
    vals = M[PRIMARY[chart]] if len(M[PRIMARY[chart]]) >= 12 else M.get({'tt': 'tt_pos'}.get(chart, ''), {})
    us = D['latest']['lists'].get(chart, [])
    sets = dict(zip(us, keywords([name(D, u) for u in us])))
    counts = {}
    for s in sets.values():
        for t in s:
            counts[t] = counts.get(t, 0) + 1
    out = []
    for t, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:15]:
        r = None
        scored = [u for u in us if u in vals]
        if c >= 5 and len(scored) >= 12:
            e, p, _ = rank_biserial([int(t in sets[u]) for u in scored], [vals[u] for u in scored], 4)
            r = None if e is None else {'effect': round(e, 2), 'p': round(p, 3)}
        out.append({'label': t.upper() if t in ('rp', 'vs', 'td', '+1') else t.capitalize(), 'share': round(c / max(1, len(us)), 3), 'assoc': r})
    return out


def cmd_stats(a):
    D = build(a.date)
    date = D['latest']['date']
    M = metrics(D)
    hs = retest_all(D, M, date)
    L = D['latest']
    wd = os.path.join(WORK, date)
    os.makedirs(wd, exist_ok=True)
    lines = [f'# Deep Analysis stats · {date}', '']
    nd = {k: sum(1 for c in D['days'] if k in c['lists'] and k not in c['partial']) for k in CHARTS}
    prof = sum(1 for u in D['members'] if 'page_at' in D['profiles'].get(u, {}))
    llm = sum(1 for u in D['members'] if 'llm' in D['profiles'].get(u, {}))
    lines.append(f'Coverage: {len(D["members"])} games today; page data for {prof}, gameplay profiles for {llm}. '
                 f'Days recorded per chart: ' + ', '.join(f'{NAMES[k]} {nd[k]}' for k in CHARTS))
    stats = {'date': date, 'coverage': {'games': len(D['members']), 'page': prof, 'llm': llm, 'days': nd}, 'charts': {}}
    for k in CHARTS:
        lst = L['lists'].get(k, [])
        lines += ['', f'## {NAMES[k]} — pillar: {PILLARS[k]}']
        prim = PRIMARY[k]
        vals = M[prim] if len(M[prim]) >= 12 else (M['tt_pos'] if k == 'tt' else M[prim])
        used = prim if vals is M[prim] else 'tt_pos'
        lines.append(f'Population: {len(lst)} on chart; {len(vals)} scored on {METRIC_NAMES[used]}' + (' (age ≤ 4 months only)' if k == 'tt' else ''))
        ranked = sorted(vals, key=lambda u: -vals[u])
        lines.append('Best on the pillar: ' + '; '.join(f'{name(D, u)} [{traits(D, u)}]' for u in ranked[:6]))
        lines.append('Weakest on the pillar: ' + '; '.join(f'{name(D, u)} [{traits(D, u)}]' for u in ranked[-4:]))
        prev = [c for c in D['days'][:-1] if k in c['lists'] and k not in c['partial']]
        if prev and k not in L['partial']:
            pp = {u: i + 1 for i, u in enumerate(prev[-1]['lists'][k])}
            moves = sorted(((pp[u] - (i + 1), u) for i, u in enumerate(lst) if u in pp), reverse=True)
            newc = [u for u in lst if u not in pp]
            gone = [u for u in pp if u not in set(lst)]
            lines.append('Biggest risers: ' + '; '.join(f'{name(D, u)} +{m} [{traits(D, u)}]' for m, u in moves[:5] if m > 0))
            lines.append('Biggest fallers: ' + '; '.join(f'{name(D, u)} {m} [{traits(D, u)}]' for m, u in moves[-5:] if m < 0))
            lines.append(f'New entries ({len(newc)}): ' + '; '.join(f'{name(D, u)} [{traits(D, u)}]' for u in newc[:8]))
            lines.append(f'Fell off ({len(gone)}): ' + '; '.join(name(D, u) for u in gone[:10]))
            upd = [(pp[u] - (i + 1)) for i, u in enumerate(lst) if u in pp and (D['feats'].get(u, {}).get('days_since_update') or 99) <= 1]
            rest = [(pp[u] - (i + 1)) for i, u in enumerate(lst) if u in pp and (D['feats'].get(u, {}).get('days_since_update') or 99) > 1]
            if upd and rest:
                lines.append(f'Updated in the last day: {len(upd)} games, median move {med(upd):+} vs {med(rest):+} for the rest')
        buckets = [(0, 14), (15, 42), (43, 84), (85, 122), (123, 365), (366, 99999)]
        lb = []
        for lo, hi in buckets:
            us = [u for u in vals if lo <= (D['feats'].get(u, {}).get('age_days') or -1) <= hi]
            if us:
                ranks_ = sorted(vals.values())
                pct = med([100 * sum(1 for v in ranks_ if v <= vals[u]) / len(ranks_) for u in us])
                lb.append(f'{lo}-{hi if hi < 99999 else "+"}d: n={len(us)} median pillar percentile {pct:.0f}')
        lines.append('By age: ' + ' | '.join(lb))
        top = scan(D, M, k)
        lines.append('Strongest associations (feature vs metric; positive = better on the pillar):')
        lines += ['- ' + fmt_assoc(r) for r in top]
        kw = keyword_stats(D, M, k)
        lines.append('Title keywords (share; association with pillar): ' + '; '.join(
            f'{w["label"]} {100 * w["share"]:.0f}%' + (f' r={w["assoc"]["effect"]:+.2f}' if w['assoc'] else '') for w in kw[:12]))
        arch = {}
        for u in vals:
            l = (D['profiles'].get(u, {}).get('llm') or {})
            if l.get('loop_archetype'):
                arch.setdefault(l['loop_archetype'], []).append(u)
        if arch:
            allv = sorted(vals.values())
            pc = lambda u: 100 * sum(1 for v in allv if v <= vals[u]) / len(allv)
            lines.append('Gameplay loops among profiled games (n; median pillar percentile): ' + '; '.join(
                f'{a} {len(us)} ({med([pc(u) for u in us]):.0f})' for a, us in sorted(arch.items(), key=lambda kv: -len(kv[1]))))
        stats['charts'][k] = {'metric': used, 'n': len(vals), 'top': top, 'keywords': kw,
                              'best': [{'u': u, 'name': name(D, u)} for u in ranked[:10]]}
    # cross-chart
    lines += ['', '## Cross-chart']
    first, start = {}, {}
    for c in D['days']:
        for k in CHARTS:
            if k in c['lists'] and k not in c['partial']:
                start.setdefault(k, c['date'])
                for u in c['lists'][k]:
                    first.setdefault(u, {}).setdefault(k, c['date'])
    # Only real entries count: a game already on a chart when tracking began has an unknown entry date.
    entries, moves = {k: 0 for k in CHARTS}, {}
    for u, fk in first.items():
        for b, db in fk.items():
            if db == start.get(b):
                continue
            entries[b] += 1
            for a, da in fk.items():
                if a != b and da < db:
                    moves.setdefault(f'{a}→{b}', []).append(dnum(db) - dnum(da))
    lines.append('Real chart entries since tracking began: ' + '; '.join(f'{NAMES[k]} {entries[k]}' for k in CHARTS))
    lines.append('Entries by games already on another chart (count, median days later): ' +
                 ('; '.join(f'{m} {len(v)} ({sorted(v)[len(v) // 2]}d)' for m, v in sorted(moves.items(), key=lambda kv: -len(kv[1])))
                  or 'none yet'))
    feats_x = {}
    for k in CHARTS:
        for r in stats['charts'][k]['top']:
            feats_x.setdefault(r['feature'], {})[k] = r['effect']
    shared = {f: v for f, v in feats_x.items() if len(v) >= 2}
    lines.append('Features strong on several charts: ' + '; '.join(
        f'{f} ' + ', '.join(f'{NAMES[k]} {e:+.2f}' for k, e in v.items()) for f, v in list(shared.items())[:10]))
    lines += ['', '## Hypothesis log']
    for h in hs:
        ev = h.get('evidence') or {}
        prevc = h['history'][-2]['conf'] if len(h.get('history', [])) >= 2 else None
        lines.append(f'- {h["id"]} [{NAMES.get(h["chart"], h["chart"])}] {h["statement"]} — {h["confidence"]}% ({h["status"]}'
                     + (f', was {prevc}%' if prevc is not None else '') + f') effect={ev.get("effect")} n={ev.get("n")} days={ev.get("days")}'
                     + (f' counter: {", ".join(ev.get("counterexamples") or [])}' if ev.get('counterexamples') else '')
                     + (f' | action shown: "{h["action"]}"' if h.get('action') else ' | no action (not shown)'))
    open(os.path.join(wd, 'stats_report.md'), 'w', encoding='utf-8').write('\n'.join(lines))
    json.dump(stats, open(os.path.join(wd, 'stats.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'OK stats date={date} hypotheses={len(hs)} report={os.path.join(wd, "stats_report.md")} chars={sum(len(x) for x in lines)}')


# ---------------------------------------------------------------- finalize
def cmd_finalize(a):
    D = build(a.date)
    date = D['latest']['date']
    wd = os.path.join(WORK, date)
    M = metrics(D)
    hs = load_h()
    prop_path = os.path.join(wd, 'proposals.json')
    if os.path.exists(prop_path):
        props = json.load(open(prop_path, encoding='utf-8'))
        nums = {k: max([int(h['id'].split('-')[-1]) for h in hs if h['chart'] == k] or [0]) for k in CHARTS + ['x']}
        known_feats = set(all_feature_names(D)) | set(FEATURE_LABELS)
        for n in props.get('new', []):
            k = n.get('chart', 'x')
            if any(h['statement'].strip().lower() == n['statement'].strip().lower() for h in hs):
                continue
            nums[k] = nums.get(k, 0) + 1
            h = {'id': f'H-{k}-{nums[k]:03d}', 'chart': k, 'statement': n['statement'], 'kind': n.get('kind', 'tested'),
                 'created': date, 'history': []}
            if (n.get('action') or '').strip():
                h['action'] = n['action'].strip()
            if h['kind'] == 'tested':
                t = n.get('test') or {}
                if t.get('feature') not in known_feats or t.get('metric') not in METRIC_NAMES:
                    print('REJECTED proposal (unknown feature or metric):', n['statement'][:80], t)
                    continue
                h['test'] = t
                res = test_h(D, M, h)
                conf = res.pop('confidence', 50)
                record(h, date, conf, res)
            else:
                record(h, date, int(n.get('confidence', 25)), {'rationale': n.get('rationale', '')})
            hs.append(h)
        for upd in props.get('qualitative_updates', []):
            for h in hs:
                if h['id'] == upd.get('id') and h.get('kind') == 'qualitative':
                    h['history'] = [e for e in h.get('history', []) if e['date'] != date]
                    record(h, date, int(upd['confidence']), {'rationale': upd.get('note', '')})
        for upd in props.get('actions', []):
            for h in hs:
                if h['id'] == upd.get('id'):
                    if (upd.get('action') or '').strip():
                        h['action'] = upd['action'].strip()
                    else:
                        h.pop('action', None)
        for ret in props.get('retire', []):
            for h in hs:
                if h['id'] == ret.get('id'):
                    h['status'] = 'Retired'
                    h.setdefault('evidence', {})['retired_because'] = ret.get('reason', '')
        save_h(hs)
    narrative = json.load(open(os.path.join(wd, 'narrative.json'), encoding='utf-8')) if os.path.exists(os.path.join(wd, 'narrative.json')) else {}
    stats = json.load(open(os.path.join(wd, 'stats.json'), encoding='utf-8')) if os.path.exists(os.path.join(wd, 'stats.json')) else {'charts': {}}
    prev_docs = sorted(glob.glob(os.path.join(ANA, 'archive', '*.json')))
    day_index = len([p for p in prev_docs if os.path.basename(p)[:10] < date]) + 1
    charts = {}
    for k in CHARTS:
        n = (narrative.get('charts') or {}).get(k, {})
        charts[k] = {'pillar': PILLARS[k], 'metric': METRIC_NAMES.get((stats['charts'].get(k) or {}).get('metric', PRIMARY[k])),
                     'observations': n.get('observations', []), 'patterns': n.get('patterns', []),
                     'keywords': (stats['charts'].get(k) or {}).get('keywords', [])[:12],
                     'outliers': n.get('outliers', []), 'takeaways': n.get('takeaways', [])}
    doc = {'date': date, 'generatedAt': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'dayIndex': day_index,
           'coverage': stats.get('coverage', {}), 'charts': charts,
           'cross': {'summary': (narrative.get('cross') or {}).get('summary', []), 'progressions': (narrative.get('cross') or {}).get('progressions', [])},
           'hypotheses': [{'id': h['id'], 'chart': h['chart'], 'statement': h['statement'], 'action': h.get('action', ''), 'kind': h.get('kind', 'tested'),
                           'confidence': h['confidence'], 'status': h['status'], 'created': h.get('created'),
                           'evidence': {k: v for k, v in (h.get('evidence') or {}).items() if k in ('effect', 'n', 'days', 'consistency', 'creator_robust', 'counterexamples', 'rationale', 'retired_because', 'note')},
                           'history': [[e['date'], e['conf']] for e in h.get('history', [])[-30:]]} for h in hs]}
    body = json.dumps(doc, ensure_ascii=False, separators=(',', ':'))
    if len(body.encode()) > 240_000:
        sys.exit('ANALYSIS DOC TOO LARGE: %d bytes; shorten the narrative' % len(body.encode()))
    open(os.path.join(wd, 'analysis.json'), 'w', encoding='utf-8').write(body)
    os.makedirs(os.path.join(ANA, 'archive'), exist_ok=True)
    open(os.path.join(ANA, 'archive', date + '.json'), 'w', encoding='utf-8').write(body)
    print(f'OK finalize date={date} hypotheses={len(hs)} bytes={len(body.encode())} out={os.path.join(wd, "analysis.json")}')


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    for c in ('collect', 'ingest', 'stats', 'finalize'):
        s = sub.add_parser(c)
        s.add_argument('--date', default=dt.datetime.now().strftime('%Y-%m-%d'))
        if c == 'collect':
            s.add_argument('--budget', type=int, default=420, help='seconds to spend refreshing per-game page data')
            s.add_argument('--queue', type=int, default=60, help='games to queue for gameplay profiling')
            s.add_argument('--workers', type=int, default=4, help='parallel page-data requests')
    a = ap.parse_args()
    {'collect': cmd_collect, 'ingest': cmd_ingest, 'stats': cmd_stats, 'finalize': cmd_finalize}[a.cmd](a)


if __name__ == '__main__':
    main()
