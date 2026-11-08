"""Build the hosted copy of the Roblox Monetization Tracker into an output folder.

The page is the same roblox_monetization_tracker.html the artifact uses. Here a small shim (site/static_db.js) stands in
for the artifact database and reads JSON files published next to the page instead:

  data/days.json            every day document from db-copy/days, with today and yesterday refreshed from roblox-ccu
  data/analysis.json        the latest Deep Analysis document (analysis/archive)
  data/live/platform.json   total players on Roblox (tracker.py live)
  data/hist/_index.json     universe id -> "through" date, for the games with Rolimons history
  data/hist/<id>.json       each game's Rolimons history
  data/meta.json            when this build ran (the page warns when it is old)

Nothing is written back to the repo. Usage: python3 site/build.py --ccu ../roblox-ccu --out _site
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEAD = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<style>body{margin:0}</style></head><body>\n')


def dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))


def tracker(*args):
    r = subprocess.run([sys.executable, os.path.join(ROOT, 'tracker.py'), *args], cwd=ROOT, capture_output=True, text=True)
    print((r.stdout + r.stderr).strip())
    return r.returncode == 0


def build_days(ccu, tmp):
    days = {}
    for p in sorted(glob.glob(os.path.join(ROOT, 'db-copy', 'days', '*.json'))):
        d = json.load(open(p, encoding='utf-8'))
        days[d['date']] = d
    today = dt.datetime.now(ZoneInfo('America/New_York')).date()
    for date in (str(today - dt.timedelta(days=1)), str(today)):  # what the 4-hourly cloud run refreshes, hourly here
        day_file = os.path.join(ccu, 'data', 'daily', date + '.json')
        if date not in days or not os.path.exists(day_file):
            continue
        out = os.path.join(tmp, date + '.json')
        if tracker('refresh', '--date', date, '--dump', os.path.join(ROOT, 'db-copy'), '--day-file', day_file,
                   '--rt-file', os.path.join(ccu, 'data', 'roterminal_daily.json'), '--out', out):
            days[date] = json.load(open(out, encoding='utf-8'))
    return [days[k] for k in sorted(days)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ccu', required=True, help='a roblox-ccu checkout')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(os.path.join(out, 'data'))

    with tempfile.TemporaryDirectory() as tmp:
        days = build_days(a.ccu, tmp)
        if not days:
            sys.exit('BUILD FAILED: no day documents in db-copy/days')
        dump(os.path.join(out, 'data', 'days.json'), days)
        live = os.path.join(tmp, 'live.json')
        if tracker('live', '--ccu', a.ccu, '--out', live):
            os.makedirs(os.path.join(out, 'data', 'live'))
            shutil.copy(live, os.path.join(out, 'data', 'live', 'platform.json'))

    archive = sorted(glob.glob(os.path.join(ROOT, 'analysis', 'archive', '*.json')))
    dump(os.path.join(out, 'data', 'analysis.json'), [json.load(open(archive[-1], encoding='utf-8'))] if archive else [])

    index = {}
    for p in glob.glob(os.path.join(ROOT, 'rolimons', '*.json')):
        u = os.path.basename(p)[:-5]
        if u.isdigit():
            index[u] = json.load(open(p, encoding='utf-8')).get('through', '')
            os.makedirs(os.path.join(out, 'data', 'hist'), exist_ok=True)
            shutil.copy(p, os.path.join(out, 'data', 'hist', u + '.json'))
    dump(os.path.join(out, 'data', 'hist', '_index.json'), index)

    dump(os.path.join(out, 'data', 'meta.json'), {'built': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})

    page = open(os.path.join(ROOT, 'roblox_monetization_tracker.html'), encoding='utf-8').read()
    shim = '<script>\n' + open(os.path.join(ROOT, 'site', 'static_db.js'), encoding='utf-8').read() + '</script>\n'
    page, n = re.subn(r'^<script>$', lambda m: shim + m.group(0), page, count=1, flags=re.M)  # before the page's own script
    if n != 1:
        sys.exit("BUILD FAILED: the page's main <script> tag wasn't found")
    with open(os.path.join(out, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(HEAD + page + '\n</body></html>\n')
    open(os.path.join(out, '.nojekyll'), 'w').close()
    print('OK build days=%d latest=%s analysis=%s hist=%d out=%s' % (
        len(days), days[-1]['date'], os.path.basename(archive[-1])[:-5] if archive else 'none', len(index), out))


if __name__ == '__main__':
    main()
