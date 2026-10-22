#!/usr/bin/env python3
"""Privacy scan for the tracker repos (standard library only).

  python3 tools/privacy_scan.py REPO_DIR [REPO_DIR ...]   scan every branch's current files; exit 1 on any finding
  python3 tools/privacy_scan.py --add "term"              add a personal term (stored only as a SHA-256 hash)

Looks for two things in every text file of every branch (refs/remotes/origin/* and HEAD):
  - credentials by pattern: GitHub/Anthropic/OpenAI/Google/Firecrawl/Slack/AWS keys, private keys, JWTs, the Roblox
    .ROBLOSECURITY cookie;
  - personal terms (email, username, name) from tools/privacy_terms.sha256. Only hashes are stored and only the location
    of a match is printed, because these repos and their Actions logs are public.
Commit history is not scanned, only each branch's current files.
"""
import hashlib, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TERMS = os.path.join(HERE, 'privacy_terms.sha256')
PATTERNS = {
    'GitHub token': r'\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}',
    'Anthropic key': r'\bsk-ant-[A-Za-z0-9_\-]{20,}',
    'OpenAI-style key': r'\bsk-(proj-)?[A-Za-z0-9]{32,}',
    'Google API key': r'\bAIza[0-9A-Za-z_\-]{35}\b',
    'Firecrawl key': r'\bfc-[0-9a-f]{32}\b',
    'Slack token': r'\bxox[abposr]-[A-Za-z0-9\-]{10,}',
    'AWS key': r'\bAKIA[0-9A-Z]{16}\b',
    'Private key': r'-----BEGIN [A-Z ]*PRIVATE KEY-----',
    'JWT': r'\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}',
    'Roblox cookie': r'_\|WARNING:-DO-NOT-SHARE-THIS',
}
WORD = re.compile(r'[a-z0-9._%+\-@]+')
PLAIN = re.compile(r'[a-z0-9]+')


def h(s):
    return hashlib.sha256(s.strip().lower().encode()).hexdigest()


def load_terms():
    try:
        return {l.split()[0] for l in open(TERMS) if l.strip() and not l.startswith('#')}
    except FileNotFoundError:
        return set()


def git(repo, *args):
    return subprocess.run(['git', '-C', repo] + list(args), capture_output=True, check=True).stdout


def scan_text(text, terms):
    """[(line number, what)] for one file's text."""
    hits = []
    for name, rx in PATTERNS.items():
        for m in re.finditer(rx, text):
            hits.append((text.count('\n', 0, m.start()) + 1, name))
    if terms:
        for n, line in enumerate(text.lower().split('\n'), 1):
            words = PLAIN.findall(line)
            cands = set(WORD.findall(line)) | set(words) | {a + ' ' + b for a, b in zip(words, words[1:])}
            for c in cands:
                if h(c) in terms:
                    hits.append((n, 'personal term #%d' % sorted(terms).index(h(c))))
    return hits


def scan_repo(repo, terms):
    refs = [r for r in git(repo, 'for-each-ref', '--format=%(refname)', 'refs/remotes/origin').decode().split() if not r.endswith('/HEAD')] or ['HEAD']
    seen, found = set(), 0
    cat = subprocess.Popen(['git', '-C', repo, 'cat-file', '--batch'], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    for ref in refs:
        for entry in git(repo, 'ls-tree', '-r', '-z', ref).decode().split('\0'):
            if not entry:
                continue
            info, path = entry.split('\t', 1)
            sha = info.split()[2]
            if sha in seen:
                continue  # same file content already scanned on another branch
            seen.add(sha)
            cat.stdin.write((sha + '\n').encode())
            cat.stdin.flush()
            size = int(cat.stdout.readline().split()[2])
            blob = cat.stdout.read(size)
            cat.stdout.read(1)
            if b'\0' in blob[:8000] or size > 20_000_000:
                continue  # binary (images) or huge
            for line, what in scan_text(blob.decode('utf-8', 'replace'), terms):
                print('FOUND %s  %s:%s line %d' % (what, ref.replace('refs/remotes/origin/', ''), path, line))
                found += 1
    cat.stdin.close()
    print('%s: %d branch(es) scanned, %d finding(s)' % (os.path.basename(os.path.abspath(repo)), len(refs), found))
    return found


def main():
    if sys.argv[1:2] == ['--add']:
        with open(TERMS, 'a') as f:
            f.write(h(sys.argv[2]) + '\n')
        print('added (hash only)')
        return
    terms = load_terms()
    total = sum(scan_repo(r, terms) for r in sys.argv[1:])
    sys.exit(1 if total else 0)


if __name__ == '__main__':
    main()
