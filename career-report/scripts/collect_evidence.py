#!/usr/bin/env python3
"""收集客觀證據：git 貢獻統計、技術棧、程式碼規模、專案結構。

prompt 只反映「要求 AI 做什麼」，不代表成果；本腳本提供可稽核的交叉驗證資料。

用法：
    python3 collect_evidence.py --out <輸出目錄> [--repo PATH ...]

不指定 --repo 時，自動從 ~/.claude/history.jsonl 的 project 路徑推導候選 repo。

注意：git 一律透過 subprocess 直接執行，不經過 shell，
      以避免 Bash 工具層的輸出過濾／截斷造成統計失真。
"""
import argparse
import collections
import glob
import json
import os
import subprocess
import sys

MANIFESTS = ('package.json', 'composer.json', 'go.mod', 'requirements.txt', 'pyproject.toml')
CODE_EXT = ('.ts', '.tsx', '.js', '.jsx', '.py', '.go', '.php', '.vue', '.svelte')
SKIP_DIRS = {'node_modules', '.next', 'dist', 'build', 'vendor', '.git', 'venv', '.venv',
             '__pycache__', 'coverage', '.turbo'}


def git(repo, args):
    try:
        r = subprocess.run(['git', '-C', repo] + args, capture_output=True, text=True, timeout=120)
        return r.stdout
    except Exception:
        return ''


def find_repos(explicit, claude_home):
    """探索候選 repo：明確指定優先，否則從對話紀錄的工作目錄推導。"""
    if explicit:
        return [os.path.realpath(os.path.expanduser(p)) for p in explicit]

    paths = set()
    hist = os.path.join(claude_home, 'history.jsonl')
    if os.path.exists(hist):
        with open(hist, errors='ignore') as f:
            for line in f:
                try:
                    p = json.loads(line).get('project')
                except Exception:
                    continue
                if p:
                    paths.add(p)

    repos = set()
    for p in paths:
        cur = os.path.realpath(os.path.expanduser(p))
        # 往上找 .git；同時把子目錄的 repo 也納入（monorepo / 前後端分離常見）
        probe = cur
        for _ in range(6):
            if os.path.isdir(os.path.join(probe, '.git')):
                repos.add(probe)
                break
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
        if os.path.isdir(cur):
            try:
                for child in os.listdir(cur):
                    cp = os.path.join(cur, child)
                    if child.startswith('.') or child in SKIP_DIRS:
                        continue
                    if os.path.isdir(os.path.join(cp, '.git')):
                        repos.add(os.path.realpath(cp))
            except PermissionError:
                pass

    # 排除隱藏目錄下的 repo（~/.nvm、~/.oh-my-zsh、~/.Trash 等工具目錄）
    home = os.path.realpath(os.path.expanduser('~'))
    clean = []
    for r in repos:
        rel = os.path.relpath(r, home)
        if any(part.startswith('.') for part in rel.split(os.sep)):
            continue
        clean.append(r)
    return sorted(clean)


def identity_candidates(repos):
    """蒐集可能屬於本人的 git 身分（全域設定 + 各 repo 區域設定）。"""
    emails, names = set(), set()
    for scope in (['config', '--global', 'user.email'], ['config', '--global', 'user.name']):
        out = subprocess.run(['git'] + scope, capture_output=True, text=True).stdout.strip()
        if out:
            (emails if 'email' in scope[-1] else names).add(out.lower())
    for r in repos:
        e = git(r, ['config', 'user.email']).strip().lower()
        n = git(r, ['config', 'user.name']).strip().lower()
        if e:
            emails.add(e)
        if n:
            names.add(n)
    return emails, names


def is_mine(email, name, emails, names):
    email, name = email.lower(), name.lower()
    if email in emails or name in names:
        return True
    local = email.split('@')[0]
    tokens = {e.split('@')[0] for e in emails} | names
    for t in tokens:
        if len(t) >= 3 and len(local) >= 3 and (local.startswith(t) or t.startswith(local)):
            return True
        if len(t) >= 3 and len(name) >= 3 and (name.startswith(t) or t.startswith(name)):
            return True
    return False


def repo_stats(repo, emails, names):
    log = git(repo, ['log', '--all', '--format=%ae\t%an\t%ad', '--date=short'])
    lines = [l for l in log.strip().split('\n') if l and '\t' in l]
    if not lines:
        return None

    authors = collections.Counter()
    mine_dates = []
    for l in lines:
        ae, an, ad = (l.split('\t') + ['', ''])[:3]
        authors[f'{an} <{ae}>'] += 1
        if is_mine(ae, an, emails, names):
            mine_dates.append(ad)

    mine_ids = sorted({a for a in authors
                       if is_mine(a.split('<')[-1].rstrip('>'), a.split('<')[0].strip(), emails, names)})

    ins = dele = 0
    files = set()
    for aid in mine_ids:
        email = aid.split('<')[-1].rstrip('>')
        out = git(repo, ['log', '--all', f'--author={email}', '--numstat', '--pretty=tformat:'])
        for l in out.split('\n'):
            p = l.split('\t')
            if len(p) == 3 and p[0].isdigit() and p[1].isdigit():
                ins += int(p[0])
                dele += int(p[1])
                files.add(p[2])

    branches = len([b for b in git(repo, ['branch', '-a', '--format=%(refname:short)']).split('\n') if b.strip()])
    mine_dates.sort()
    return {
        'total': len(lines),
        'authors': authors.most_common(10),
        'mine_ids': mine_ids,
        'mine': len(mine_dates),
        'mine_first': mine_dates[0] if mine_dates else None,
        'mine_last': mine_dates[-1] if mine_dates else None,
        'ins': ins, 'dele': dele, 'files': len(files),
        'branches': branches,
    }


def tech_stack(repo):
    """從 manifest 抽技術棧（含 monorepo 的子套件）。"""
    found = []
    for depth_glob in ('', '*/', '*/*/'):
        for m in MANIFESTS:
            for path in glob.glob(os.path.join(repo, depth_glob, m)):
                if any(s in path.split(os.sep) for s in SKIP_DIRS):
                    continue
                rel = os.path.relpath(path, repo)
                try:
                    if m == 'package.json':
                        d = json.load(open(path))
                        deps = sorted({**d.get('dependencies', {}), **d.get('devDependencies', {})})
                        found.append((rel, f'name={d.get("name")} deps=' + ', '.join(deps)))
                    elif m == 'composer.json':
                        d = json.load(open(path))
                        deps = sorted({**d.get('require', {}), **d.get('require-dev', {})})
                        found.append((rel, f'name={d.get("name")} deps=' + ', '.join(deps)))
                    else:
                        txt = ' '.join(open(path, errors='ignore').read().split())
                        found.append((rel, txt[:1200]))
                except Exception:
                    continue
    return found


def code_size(repo):
    per_ext = collections.Counter()
    per_ext_files = collections.Counter()
    for root, dirs, fs in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
        for f in fs:
            ext = os.path.splitext(f)[1]
            if ext not in CODE_EXT:
                continue
            try:
                n = sum(1 for _ in open(os.path.join(root, f), errors='ignore'))
            except Exception:
                continue
            per_ext[ext] += n
            per_ext_files[ext] += 1
    return per_ext, per_ext_files


def structure(repo):
    out = []
    for sub in ('src', 'app', 'packages', 'apps', 'scripts', 'docs', 'prisma/migrations'):
        p = os.path.join(repo, sub)
        if not os.path.isdir(p):
            continue
        try:
            entries = sorted(os.listdir(p))
        except PermissionError:
            continue
        if sub == 'prisma/migrations':
            out.append(f'  {sub}/: {len(entries)} 個 migration')
        else:
            out.append(f'  {sub}/: ' + ' '.join(entries[:40]) + (' ...' if len(entries) > 40 else ''))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--repo', action='append', default=[])
    ap.add_argument('--claude-home', default=os.path.expanduser('~/.claude'))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    repos = find_repos(args.repo, args.claude_home)
    if not repos:
        print('WARNING: 找不到任何 git repo，只能依賴 prompt 語料撰寫文件')
    emails, names = identity_candidates(repos)

    dest = os.path.join(args.out, 'evidence.txt')
    with open(dest, 'w') as f:
        f.write('=== 身分候選（用於判定哪些 commit 屬於本人）===\n')
        f.write(f'emails: {sorted(emails)}\nnames: {sorted(names)}\n')
        f.write('注意：以下 mine_ids 為自動比對結果，撰寫文件前必須人工確認是否全屬同一人。\n\n')

        for r in repos:
            f.write('=' * 70 + f'\nREPO: {r}\n')
            st = repo_stats(r, emails, names)
            if not st:
                f.write('  (無 commit 紀錄)\n\n')
                continue
            pct = 100 * st['mine'] / st['total'] if st['total'] else 0
            f.write(f"  commits: 本人 {st['mine']} / 全部 {st['total']}  ({pct:.0f}%)\n")
            f.write(f"  本人 commit 期間: {st['mine_first']} ~ {st['mine_last']}\n")
            f.write(f"  本人變更行數: +{st['ins']:,} / -{st['dele']:,}，觸及 {st['files']:,} 個檔案\n")
            f.write(f"  （行數含 lock／產生檔，僅供規模參考，勿當作手寫程式碼量）\n")
            f.write(f"  分支數: {st['branches']}\n")
            f.write(f"  判定為本人的身分: {st['mine_ids']}\n")
            f.write('  作者排行:\n')
            for a, n in st['authors']:
                f.write(f'    {n:>6}  {a}\n')

            size, nfiles = code_size(r)
            if size:
                f.write('  程式碼規模: ' + '，'.join(
                    f'{e} {size[e]:,} 行/{nfiles[e]} 檔' for e, _ in size.most_common(5)) + '\n')

            struct = structure(r)
            if struct:
                f.write('  結構:\n' + '\n'.join(struct) + '\n')

            for rel, info in tech_stack(r):
                f.write(f'  [{rel}] {info}\n')
            f.write('\n')

    print(f'證據已輸出：{dest}')
    print(f'掃描 {len(repos)} 個 repo：')
    for r in repos:
        print(f'  {r}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
