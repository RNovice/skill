#!/usr/bin/env python3
"""彙整 Claude Code 全部歷史對話紀錄，輸出專案總覽與去重後的 prompt 語料。

用法：
    python3 collect_prompts.py --out <輸出目錄>

輸出：
    <out>/overview.txt   每個專案的期間、活躍天數、prompt 數
    <out>/prompts.txt    依專案分組、依日期標註、去重過濾後的 prompt 語料
"""
import argparse
import collections
import datetime
import glob
import json
import os
import re
import sys

# 單字回覆、指令、確認詞等不具工作內容資訊的 prompt
TRIVIAL = re.compile(
    r'^(好|ok|yes|y|n|no|繼續|continue|嗯|對|是|不是|1|2|3|a|b|c|謝謝|thanks'
    r'|可以|不用|等等|停|再來|然後|試試|來|做|改|看|跑|/\w[\w-]*)$',
    re.I,
)

# 疑似憑證的內容一律不進語料（避免寫入交付文件）
SECRET = re.compile(
    r'(eyJ[A-Za-z0-9_-]{20,}'          # JWT
    r'|sk-[A-Za-z0-9]{16,}'            # API key
    r'|figd_[A-Za-z0-9_-]{10,}'
    r'|gh[pousr]_[A-Za-z0-9]{20,}'
    r'|AKIA[0-9A-Z]{16}'
    r'|(?i:password|passwd|密碼|帳密)\s*[:=：]\s*\S+)'
)

MIN_LEN = 8
MAX_LEN = 220
DEDUP_PREFIX = 90


def load_history(home):
    """讀 ~/.claude/history.jsonl（每筆為使用者送出的 prompt）。"""
    path = os.path.join(home, 'history.jsonl')
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = d.get('timestamp', 0)
            text = (d.get('display') or '').strip()
            if not ts or not text:
                continue
            rows.append((
                datetime.datetime.fromtimestamp(ts / 1000),
                d.get('project') or '(unknown)',
                text,
            ))
    return rows


def load_projects(home):
    """讀 ~/.claude/projects/*/*.jsonl 的 user 訊息，補足 history 缺漏的上下文。"""
    rows = []
    for jf in glob.glob(os.path.join(home, 'projects', '*', '*.jsonl')):
        slug = os.path.basename(os.path.dirname(jf))
        try:
            with open(jf, errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if d.get('type') != 'user':
                        continue
                    ts = d.get('timestamp') or ''
                    if len(ts) < 19:
                        continue
                    content = (d.get('message') or {}).get('content', '')
                    text = ''
                    if isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get('type') == 'text':
                                text = (c.get('text') or '').strip()
                                break
                    elif isinstance(content, str):
                        text = content.strip()
                    if not text:
                        continue
                    try:
                        dt = datetime.datetime.fromisoformat(ts[:19])
                    except ValueError:
                        continue
                    rows.append((dt, slug, text))
        except Exception:
            continue
    return rows


def _norm(s):
    """把路徑／slug 正規化成可互相比對的鍵。

    Claude Code 產生 projects/ 資料夾名時會把 / _ . 一律換成 -，
    因此無法用字串反推原路徑，只能正規化後與 history 的真實路徑比對。
    """
    return re.sub(r'[/_.\-]+', '-', s).strip('-').lower()


def build_slug_map(hist):
    """以 history.jsonl 的真實工作目錄為準，建立 slug -> 真實路徑的對照表。"""
    return {_norm(p): p for _, p, _ in hist}


def slug_to_path(slug, slug_map):
    hit = slug_map.get(_norm(slug))
    if hit:
        return hit
    # 對照不到時保留 slug 原樣，避免產生錯誤的假路徑
    return f'(unmatched) {slug}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--claude-home', default=os.path.expanduser('~/.claude'))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    home = args.claude_home
    home_dir = os.path.expanduser('~')

    hist = load_history(home)
    slug_map = build_slug_map(hist)
    proj = [(dt, slug_to_path(slug, slug_map), t) for dt, slug, t in load_projects(home)]

    if not hist and not proj:
        print('ERROR: 找不到任何對話紀錄（history.jsonl 與 projects/ 皆為空）')
        return 1

    # --- 專案總覽（以 history.jsonl 為主，較能反映真實工作目錄） ---
    stats = collections.defaultdict(lambda: {'n': 0, 'dates': set()})
    for dt, project, _ in hist:
        stats[project]['n'] += 1
        stats[project]['dates'].add(dt.date())

    rows = []
    for p, v in stats.items():
        ds = sorted(v['dates'])
        rows.append((min(ds), max(ds), len(ds), v['n'], p))
    rows.sort()

    all_dates = sorted({d for v in stats.values() for d in v['dates']})
    with open(os.path.join(args.out, 'overview.txt'), 'w') as f:
        f.write(f'總 prompt 數：{len(hist)}\n')
        f.write(f'涵蓋期間：{all_dates[0]} ~ {all_dates[-1]}（{len(all_dates)} 個活躍日）\n')
        f.write(f'專案數：{len(rows)}\n\n')
        f.write(f'{"first":<12}{"last":<12}{"days":>5}{"prompts":>9}  project\n')
        for r in rows:
            f.write(f'{r[0].isoformat():<12}{r[1].isoformat():<12}{r[2]:>5}{r[3]:>9}  {r[4]}\n')

    # --- prompt 語料：合併兩個來源、過濾、去重、依專案與日期分組 ---
    merged = collections.defaultdict(list)
    for dt, project, text in hist + proj:
        merged[project].append((dt, text))

    seen = set()
    out_lines = []
    offsets = []
    kept = dropped = secrets = 0
    for p in sorted(merged):
        items = sorted(merged[p])
        offsets.append((len(out_lines) + 2, p, len(items)))
        out_lines.append('')
        out_lines.append(f'#### PROJECT: {p}  ({len(items)} prompts)')
        last_date = None
        for dt, text in items:
            one = ' '.join(text.split())
            if len(one) < MIN_LEN or TRIVIAL.match(one):
                dropped += 1
                continue
            if SECRET.search(one):
                secrets += 1
                dropped += 1
                continue
            key = (p, one[:DEDUP_PREFIX])
            if key in seen:
                dropped += 1
                continue
            seen.add(key)
            ds = dt.strftime('%Y-%m-%d')
            if ds != last_date:
                out_lines.append(f'-- {ds} --')
                last_date = ds
            out_lines.append(one[:MAX_LEN])
            kept += 1

    corpus = os.path.join(args.out, 'prompts.txt')
    with open(corpus, 'w') as f:
        f.write('\n'.join(out_lines) + '\n')

    print(f'語料已輸出：{corpus}')
    print(f'  保留 {kept} 筆 / 過濾 {dropped} 筆（其中 {secrets} 筆疑似含憑證，已排除）')
    print(f'  總行數 {len(out_lines)}（Read 工具請以 <=650 行為單位分段讀取）')
    print(f'總覽已輸出：{os.path.join(args.out, "overview.txt")}')
    print()
    print('各專案在語料中的起始行號：')
    for line_no, p, n in offsets:
        print(f'  L{line_no:<6} {n:>6} prompts  {p}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
