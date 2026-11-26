#!/usr/bin/env python3
"""Download the hourly collector's files that the site build needs into a local folder laid out like the collector's
data folder (OUT/data/...), so site/build.py and tracker.py can keep reading local files.

The collector runs in Google Apps Script (the roblox-ccu GitHub repo was disabled on Nov 24, 2026) and serves its data
files read-only at FEED_URL?path=data/... . Fetched: yesterday's and today's daily/<ET date>.json, roterminal_daily.json
and the last few UTC days of platform/<date>.csv. A file the feed doesn't have is skipped.

Usage: python3 site/fetch_ccu.py --out ccu
"""
import argparse, datetime as dt, os, sys, time, urllib.request
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tracker import FEED_URL, LIVE_HOURS  # noqa: E402

ET = ZoneInfo('America/New_York')


def fetch(path):
    """The file's text, or None when the feed doesn't have it or can't be reached (main() decides what is fatal)."""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(FEED_URL + '?path=' + path, timeout=60) as r:
                text = r.read().decode('utf-8')
            return None if text.startswith('{"error":"not found"}') else text
        except Exception as e:
            print('warning: %s: %s' % (path, e))
            time.sleep(5 * (attempt + 1))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(ET).date()
    paths = ['data/roterminal_daily.json']
    paths += ['data/daily/%s.json' % (today - dt.timedelta(days=d)) for d in (1, 0)]
    paths += ['data/platform/%s.csv' % (now - dt.timedelta(days=d)).date() for d in range(LIVE_HOURS // 24 + 2, -1, -1)]
    got = []
    for p in paths:
        text = fetch(p)
        if text is None:
            continue
        dest = os.path.join(a.out, p)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'w', encoding='utf-8') as f:
            f.write(text)
        got.append(p)
    print('fetched %d of %d collector files: %s' % (len(got), len(paths), ' '.join(got)))
    if 'data/roterminal_daily.json' not in got:
        sys.exit('collector feed unreachable or empty')


if __name__ == '__main__':
    main()
