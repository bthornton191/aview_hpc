#!/usr/bin/env python3
"""Fail when a tracked file contains a denylisted (site/customer/project) string.

This repository is public: customer names, site hostnames, internal paths,
license servers, task ids and similar must never be committed.  Patterns are
case-insensitive regexes, one per entry in ``DENYLIST`` below.

Usage:
    python scripts/check_denylist.py            # scan all tracked files
    python scripts/check_denylist.py FILE...    # scan given files (pre-commit)
    python scripts/check_denylist.py --history  # also scan every commit's diff

An allowlist (``scripts/denylist_allow.txt``) may exempt specific
``path:regex`` pairs; it is empty by design.  Exit status 1 on any hit.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = Path(__file__).with_name('denylist_allow.txt')
SELF = Path(__file__).resolve().relative_to(ROOT).as_posix()

DENYLIST = [
    r'\bNNL\b', r'naval', r'fluor', r'\bCDM\b',
    r'cadence\.com', r'sjcvl', r'sjflex', r'sjlsf', r'sjfamd', r'sjfcl',
    r'/grid/sfi', r'_space\b', r'/home/thornton', r'hexagonmetrology',
    r'DEWET', r'10\.20\.0\.10', r'\bt_[0-9a-f]{8}\b', r'MSC:20[0-9]{2}',
    r'AMDEPYC',
]
COMPILED = [re.compile(p, re.IGNORECASE) for p in DENYLIST]

# The one address allowed to appear, and only in git author/committer/tagger
# identity fields (never in file content or commit messages).  Matched as the
# exact angle-bracketed string, so any other cadence.com address still fails.
ALLOWED_IDENTITY = '<thornton@cadence.com>'


def load_allow():
    out = []
    if ALLOWLIST.exists():
        for line in ALLOWLIST.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and ':' in line:
                path, rx = line.split(':', 1)
                out.append((path.strip(), re.compile(rx.strip(), re.IGNORECASE)))
    return out


def scan_text(name, text, allow, hits):
    for n, line in enumerate(text.splitlines(), 1):
        for pat in COMPILED:
            m = pat.search(line)
            if m and not any(p == name and a.search(line) for p, a in allow):
                hits.append(f'{name}:{n}: [{pat.pattern}] {line.strip()[:120]}')


def scan_identities(name, text, allow, hits):
    """Scan author/committer/tagger lines; permit only ALLOWED_IDENTITY."""
    scan_text(name, text.replace(ALLOWED_IDENTITY, '<>'), allow, hits)


def scan_history(repo, allow, hits):
    """Scan all refs of ``repo``: identities, messages, diffs, tag objects."""
    def git(*a):
        return subprocess.run(['git', '-C', str(repo), *a], capture_output=True,
                              encoding='utf-8', errors='replace').stdout
    scan_identities('<identities>', git(
        'log', '--all', '--format=%an <%ae>%n%cn <%ce>'), allow, hits)
    # messages and diffs only: no identity fields here, so no exemption applies
    scan_text('<history>', git(
        'log', '--all', '-p', '--format=commit %H%n%B',
        '--', '.', f':(exclude){SELF}'), allow, hits)
    for tag in git('tag', '-l').split():
        if git('cat-file', '-t', tag).strip() != 'tag':
            continue
        head, _, body = git('cat-file', 'tag', tag).partition('\n\n')
        scan_identities(f'<tag {tag} header>', head, allow, hits)
        scan_text(f'<tag {tag} message>', body, allow, hits)


def main(argv):
    history = '--history' in argv
    files = [a for a in argv if not a.startswith('--')]
    if not files:
        files = subprocess.check_output(
            ['git', 'ls-files'], cwd=ROOT, text=True).split()
    allow, hits = load_allow(), []
    for f in files:
        rel = Path(f).as_posix()
        if rel == SELF or rel == ALLOWLIST.name:
            continue  # this file necessarily contains the patterns
        p = ROOT / f
        if not p.is_file():
            continue
        data = p.read_bytes()
        if b'\0' in data:
            continue
        scan_text(rel, data.decode('utf-8', 'replace'), allow, hits)
    if history:
        scan_history(ROOT, allow, hits)
    for h in hits:
        print(h)
    if hits:
        print(f'\n{len(hits)} denylist hit(s). Scrub the text; see '
              'scripts/check_denylist.py.', file=sys.stderr)
        return 1
    print('denylist: clean')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
