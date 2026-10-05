"""Is there a newer version of this repo, and have Strike's API specs changed? Read-only: nothing is installed or overwritten.

  python3 scripts/update_check.py

Repo: fetches from GitHub and lists new commits. To update, run `git pull` (Claude asks you first).
Specs: downloads Strike's public OpenAPI specs (linked from docs.strikefinance.org) into data/specs_latest/ and compares
them with the copies in specs/. A change means Strike changed its API: the repo may need an update before it relies on it.
"""
import subprocess, hashlib, json, re
from common import ROOT, DATA, _curl

SPEC_BASE = 'https://openapi.gitbook.com/o/lWuA4gM3pousMkJKKjSh/spec/'
DOC_PAGES = ['api/trade/orders', 'api/user', 'api/market', 'api/platform-stats']  # pages whose embedded specs name every file


def git(*a): return subprocess.run(['git', '-C', str(ROOT), *a], capture_output=True, text=True)


def repo():
    if git('rev-parse', '--git-dir').returncode: return 'Not a git checkout (downloaded as a ZIP?): download the latest ZIP from GitHub to update.'
    if not git('remote').stdout.strip(): return 'No GitHub remote set for this folder, so updates can\'t be checked.'
    f = git('fetch', '--quiet')
    if f.returncode: return f'Could not reach GitHub: {f.stderr.strip()[:200]}'
    up = git('rev-parse', '--abbrev-ref', '@{u}').stdout.strip() or 'origin/main'
    new = git('log', '--oneline', f'HEAD..{up}').stdout.strip()
    dirty = git('status', '--porcelain', '--untracked-files=no').stdout.strip()
    if not new: return 'Up to date.'
    return (f'{len(new.splitlines())} update(s) available:\n' + '\n'.join('  ' + l for l in new.splitlines())
            + ('\n  Note: you have local changes to tracked files; `git pull` may need them stashed first.' if dirty else '')
            + '\n  To update: git pull')


def spec_names():
    names = {p.stem for p in (ROOT / 'specs').glob('*.yaml')}
    for page in DOC_PAGES:
        names |= set(re.findall(r'openapi\.gitbook\.com(?:%2F|/)o(?:%2F|/)\w+(?:%2F|/)spec(?:%2F|/)([\w.-]+)\.yaml', _curl(['-A', 'Mozilla', f'https://docs.strikefinance.org/{page}'])))
    return sorted(names)


def specs():
    out = DATA / 'specs_latest'; out.mkdir(parents=True, exist_ok=True); lines = []
    for n in spec_names():
        body = _curl(['-A', 'Mozilla', SPEC_BASE + n + '.yaml'])
        if not body.startswith('openapi'): lines.append(f'  {n}: could not download'); continue
        (out / f'{n}.yaml').write_text(body)
        local = ROOT / 'specs' / f'{n}.yaml'
        if not local.exists(): lines.append(f'  {n}: NEW spec (not in this repo yet)')
        elif hashlib.sha256(local.read_bytes()).hexdigest() != hashlib.sha256(body.encode()).hexdigest():
            d = subprocess.run(['diff', '-u', str(local), str(out / f'{n}.yaml')], capture_output=True, text=True).stdout
            lines.append(f"  {n}: CHANGED ({sum(1 for l in d.splitlines() if l[:1] in '+-' and l[:3] not in ('+++', '---'))} lines). Diff: diff -u specs/{n}.yaml data/specs_latest/{n}.yaml")
    return '\n'.join(lines) or '  No changes: Strike\'s API specs match this repo.'


if __name__ == '__main__':
    print('== Repo ==\n' + repo())
    print('\n== Strike API specs ==\n' + specs())
