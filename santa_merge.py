#!/usr/bin/env python3
"""
santa_merge.py - Santa 2026 (Elf Calculator): merge everything you collect into one submission.csv

Give it whatever you have - in any mix:
  * results zips            results_88.zip
  * CSV files               submission.csv  submission_best.csv  base.csv ...
  * notebooks (.ipynb)      Pardheev's "GIFT" cell, Asterios's "CHANGE" cell, embedded BEST_B64 blobs
  * Kaggle notebook links   https://www.kaggle.com/code/pardheev/130-934-best-public-score-santa-2026
                            pardheev/130-934-best-public-score-santa-2026
                            "kaggle kernels output pardheev/130-934-... -p /path/to/dest"  (pasted as is)
  * Kaggle datasets         dataset:goodpjw2008/santa-2026-dag-lns-programs
  * folders                 (every csv / zip / ipynb inside)

It replays every program under the competition rules, keeps the shortest valid program per puzzle,
and writes submission.csv. It also keeps best.csv up to date, so every run starts from your best so far.

  python santa_merge.py results_88.zip 130-934-best-public-score-santa-2026.ipynb
  python santa_merge.py https://www.kaggle.com/code/pardheev/130-934-best-public-score-santa-2026
  python santa_merge.py *.zip *.ipynb --submit          # also submits, only if it beats best.csv

Kaggle links/datasets and --submit need the Kaggle CLI (pip install kaggle) and your API token
(~/.kaggle/kaggle.json or KAGGLE_USERNAME + KAGGLE_KEY). Files alone need nothing but Python 3.8+.
Notebook code is never executed: the programs are read out of the notebook as data.
"""
import argparse, base64, csv, glob, io, json, os, re, shutil, subprocess, sys, tempfile, zipfile, zlib

csv.field_size_limit(1 << 30)
COMPETITION = 'santa-2026'
SKIP_CSV = {'puzzles.csv', 'practice_puzzles.csv', 'sample_submission.csv'}


# ----------------------------------------------------------------- rules
def load_puzzles(path):
    P = {}
    with open(path, newline='', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            P[r['id']] = (json.loads(r['starting_values']), set(json.loads(r['targets'])),
                          int(r['value_limit']), int(r['registers']))
    return P


def check(p, prog):
    """Length of a valid program, or None. Same rules as the competition replay."""
    start, targets, limit, nreg = p
    if not isinstance(prog, list) or not 0 < len(prog) <= 4096:
        return None
    if len(json.dumps(prog, separators=(',', ':'))) > 131072:
        return None
    reg = list(start) + [None] * (nreg - len(start))
    seen = set(start)
    for ins in prog:
        if not isinstance(ins, list) or len(ins) != 4:
            return None
        op, a, b, d = ins
        if op not in ('+', '-', '*', '/') or not all(type(x) is int and 0 <= x < nreg for x in (a, b, d)):
            return None
        x, y = reg[a], reg[b]
        if x is None or y is None:
            return None
        if op == '+': v = x + y
        elif op == '-': v = x - y
        elif op == '*': v = x * y
        else:
            if y == 0 or x % y: return None
            v = x // y
        if not 0 < v <= limit:
            return None
        reg[d] = v
        seen.add(v)
    return len(prog) if targets <= seen else None


# ----------------------------------------------------------------- readers
def rows_from_csv_text(text):
    rd = csv.DictReader(io.StringIO(text))
    if not rd.fieldnames or not {'id', 'program'} <= set(rd.fieldnames):
        return {}
    out = {}
    for r in rd:
        try: out[r['id']] = json.loads(r['program'])
        except Exception: pass
    return out


def programs_from_notebook(path):
    """Programs embedded in a notebook, read as data (never executed)."""
    try:
        nb = json.load(open(path, encoding='utf-8'))
    except Exception:
        return {}
    src = '\n'.join(''.join(c.get('source', '')) for c in nb.get('cells', []) if c.get('cell_type') == 'code')
    found = {}
    # 1) GIFT = '''base64(zlib(json {id: program}))'''   (Pardheev)
    for m in re.finditer(r"GIFT\s*=\s*'''(.*?)'''", src, re.S):
        try: found.update(json.loads(zlib.decompress(base64.b64decode(''.join(m.group(1).split())))))
        except Exception: pass
    # 2) CHANGE = {"id": [[...]]}   (Asterios, one line)
    for m in re.finditer(r'^CHANGE\s*=\s*(\{.*\})\s*$', src, re.M):
        try: found.update(json.loads(m.group(1)))
        except Exception: pass
    # 3) any other big triple-quoted base64 blob: zlib'd JSON dict or zlib'd id,program CSV
    for m in re.finditer(r"(?:'''|\"\"\")\s*([A-Za-z0-9+/=\s]{400,}?)\s*(?:'''|\"\"\")", src, re.S):
        try: raw = zlib.decompress(base64.b64decode(''.join(m.group(1).split())))
        except Exception: continue
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict): found.update(obj)
            continue
        except Exception: pass
        try: found.update(rows_from_csv_text(raw.decode('utf-8')))
        except Exception: pass
    return {k: v for k, v in found.items() if isinstance(k, str) and isinstance(v, list)}


def kaggle(*args):
    if not shutil.which('kaggle'):
        sys.exit('the Kaggle CLI is needed for Kaggle links: pip install kaggle (and put your API token in ~/.kaggle/kaggle.json)')
    r = subprocess.run(['kaggle', *args], capture_output=True, text=True)
    if r.returncode:
        print('   kaggle', ' '.join(args[:2]), 'failed:', (r.stderr or r.stdout).strip()[:300])
    return r.returncode == 0


KERNEL_RE = re.compile(r'(?:kaggle\.com/code/)?([A-Za-z0-9_-]+/[A-Za-z0-9_.-]+)')


def expand(item, tmp):
    """Turn one command-line input into a list of (name, path) files to read."""
    item = item.strip().strip('"\'')
    if item.startswith('kaggle kernels output'):            # pasted CLI line
        item = item.split()[3]
    if item.startswith('dataset:'):
        ref = item[len('dataset:'):]
        d = os.path.join(tmp, 'ds_' + ref.replace('/', '__'))
        print(f'   downloading dataset {ref} ...')
        kaggle('datasets', 'download', ref, '-p', d, '--unzip')
        return walk(d, tmp)
    if os.path.exists(item):
        return walk(item, tmp)
    m = KERNEL_RE.search(item)
    if m and ('kaggle.com' in item or item.count('/') == 1):
        ref = m.group(1).split('?')[0].rstrip('/')
        d = os.path.join(tmp, 'k_' + ref.replace('/', '__'))
        print(f'   downloading output of {ref} ...')
        kaggle('kernels', 'output', ref, '-p', d)
        return walk(d, tmp)
    print(f'   ! not found / not understood: {item}')
    return []


def walk(path, tmp):
    if os.path.isdir(path):
        out = []
        for f in sorted(glob.glob(os.path.join(path, '**', '*'), recursive=True)):
            if os.path.isfile(f) and f.lower().endswith(('.csv', '.zip', '.ipynb')):
                out += walk(f, tmp)
        return out
    low = path.lower()
    if low.endswith('.zip'):
        d = tempfile.mkdtemp(dir=tmp)
        try:
            with zipfile.ZipFile(path) as z:
                for n in z.namelist():           # only the files we read; no paths outside d
                    if n.lower().endswith(('.csv', '.ipynb')) and not n.startswith('/') and '..' not in n:
                        z.extract(n, d)
        except zipfile.BadZipFile:
            print(f'   ! bad zip: {path}'); return []
        return [(f'{os.path.basename(path)}:{os.path.relpath(n, d)}', n) for _, n in walk(d, tmp)]
    if low.endswith(('.csv', '.ipynb')) and os.path.basename(low) not in SKIP_CSV:
        return [(os.path.basename(path), path)]
    return []


def read_source(path):
    if path.lower().endswith('.ipynb'):
        return programs_from_notebook(path)
    try:
        with open(path, newline='', encoding='utf-8-sig') as f:
            return rows_from_csv_text(f.read())
    except Exception:
        return {}


# ----------------------------------------------------------------- main
def find_puzzles(arg, tmp):
    for p in ([arg] if arg else []) + ['puzzles.csv', os.path.join('data', 'puzzles.csv')] + \
             glob.glob('/kaggle/input/**/puzzles.csv', recursive=True):
        if p and os.path.exists(p):
            return p
    print('puzzles.csv not found here - downloading it from the competition ...')
    d = os.path.join(tmp, 'comp')
    kaggle('competitions', 'download', '-c', COMPETITION, '-f', 'puzzles.csv', '-p', d)
    for z in glob.glob(os.path.join(d, '*.zip')):
        zipfile.ZipFile(z).extractall(d)
    hits = glob.glob(os.path.join(d, '**', 'puzzles.csv'), recursive=True)
    if not hits:
        sys.exit('could not get puzzles.csv: put it next to this script, or accept the competition rules on Kaggle')
    shutil.copy(hits[0], 'puzzles.csv')
    return 'puzzles.csv'


def write_csv(path, P, best):
    tmp = path + '.tmp'
    with open(tmp, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(['id', 'program'])
        for k in sorted(P):
            w.writerow([k, json.dumps(best[k], separators=(',', ':'))])
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('inputs', nargs='*', help='zips, csvs, notebooks, folders, Kaggle links, dataset:owner/slug')
    ap.add_argument('--best', default='best.csv', help='your running best file (read and updated). default best.csv')
    ap.add_argument('--puzzles', help='path to puzzles.csv (found or downloaded automatically)')
    ap.add_argument('--out', default='submission.csv', help='file to write. default submission.csv')
    ap.add_argument('--submit', action='store_true', help='submit to Kaggle if the total beats the previous best')
    ap.add_argument('--message', default=None, help='submission message')
    a = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix='santa_merge_')
    try:
        P = load_puzzles(find_puzzles(a.puzzles, tmp))
        best = {}
        before = None
        if os.path.exists(a.best):
            for k, prog in read_source(a.best).items():
                if k in P and check(P[k], prog):
                    best[k] = prog
            if len(best) == len(P):
                before = sum(len(v) for v in best.values())
                print(f'start: {a.best}  {before:,}')
            else:
                print(f'start: {a.best} has {len(best)} valid programs (of {len(P)}) - the inputs fill the rest')
        else:
            print(f'start: no {a.best} yet - it will be created')

        files = []
        for item in a.inputs:
            files += expand(item, tmp)
        if not files and not best:
            sys.exit('nothing to merge: give me some zips / csvs / notebooks / Kaggle links')

        for name, path in files:
            progs = read_source(path)
            if not progs:
                print(f'   {name}: no programs found'); continue
            ok = bad = gain = 0
            for k, prog in progs.items():
                if k not in P: continue
                n = check(P[k], prog)
                if not n: bad += 1; continue
                ok += 1
                if k not in best:
                    best[k] = prog
                elif n < len(best[k]):
                    gain += len(best[k]) - n; best[k] = prog
            tot = sum(len(progs[k]) for k in progs if k in P) if ok == len(P) and not bad else None
            print(f'   {name}: {ok} valid' + (f', {bad} INVALID (ignored)' if bad else '') +
                  (f', file total {tot:,}' if tot else '') + (f'  ->  {gain} instructions saved' if gain else ''))

        missing = [k for k in P if k not in best]
        if missing:
            sys.exit(f'only {len(P) - len(missing)} of {len(P)} puzzles have a valid program - add a full submission file')
        for k in P:                                  # final independent check of every program
            assert check(P[k], best[k]), k
        total = sum(len(best[k]) for k in P)

        write_csv(a.out, P, best)
        improved = before is None or total < before
        if improved:
            write_csv(a.best, P, best)
        print()
        print(f'TOTAL {total:,}' + (f'   (was {before:,}, {before - total:,} saved)' if before else ''))
        print(f'wrote {a.out}  - all {len(P)} programs replay correctly')
        if not improved:
            print(f'no improvement over {a.best} - {a.out} is the same total')

        if a.submit:
            if not improved:
                print('not submitting: no improvement (submit by hand if you want to anyway)')
            else:
                msg = a.message or f'merge {total}'
                if kaggle('competitions', 'submit', '-c', COMPETITION, '-f', a.out, '-m', msg):
                    print(f'SUBMITTED {total:,}')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
