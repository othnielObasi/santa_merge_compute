"""Shared logic for the automation endpoints (api/tick.py, api/status.py).

Does by itself what is otherwise done by hand on https://www.kaggle.com/competitions/santa-2026/code :
list the notebooks (best score first, newest first), skip the ones already merged, check each one's run
status, download its .ipynb at once and its output once the run has finished (still running -> retried on
the next tick), replay every program under the rules, keep the shortest per puzzle, submit when better than
everything already on Kaggle, and save best.csv / history / seen-list in the private GitHub repo.

Environment (Vercel → Settings → Environment Variables):
  KAGGLE_API_TOKEN            (or KAGGLE_USERNAME + KAGGLE_KEY)
  GITHUB_TOKEN                fine-grained token, Contents: read & write on GITHUB_REPO
  SITE_PASSWORD               protects tick + status
  GITHUB_REPO   optional      default othniel74/santa2026compete
"""
import base64, csv, datetime, glob, io, json, os, re, shutil, subprocess, sys, time, urllib.error, urllib.request, zlib

csv.field_size_limit(1 << 30)
COMP = 'santa-2026'
# private repo that stores best.csv / history / seen-list; the first one GITHUB_TOKEN can write to is used
REPO_CANDIDATES = [r for r in (os.environ.get('GITHUB_REPO', '').strip(), 'othniel74/santa2026compete',
                               'othnielObasi/santa2026-automation') if r]
REPO = REPO_CANDIDATES[0]
TMP = '/tmp/santa'
os.environ.setdefault('KAGGLE_CONFIG_DIR', '/tmp/kaggle_cfg')
os.makedirs(os.environ['KAGGLE_CONFIG_DIR'], exist_ok=True)


def _kaggle_credentials():
    """Accept any of: KAGGLE_API_TOKEN; KAGGLE_USERNAME + KAGGLE_KEY (legacy key or a new API token pasted as the key)."""
    cfg, user, key = os.environ['KAGGLE_CONFIG_DIR'], os.environ.get('KAGGLE_USERNAME', '').strip(), os.environ.get('KAGGLE_KEY', '').strip()
    tok = os.environ.get('KAGGLE_API_TOKEN', '').strip()
    if not tok and key.startswith('KGAT_'):          # a new-style API token stored under KAGGLE_KEY
        tok = key
    if tok:
        os.environ['KAGGLE_API_TOKEN'] = tok
        open(os.path.join(cfg, 'access_token'), 'w').write(tok)
    if user and key and not key.startswith('KGAT_'):  # legacy kaggle.json credentials
        p = os.path.join(cfg, 'kaggle.json')
        open(p, 'w').write(json.dumps({'username': user, 'key': key})); os.chmod(p, 0o600)
    for p in (os.path.join(cfg, 'access_token'), os.path.join(cfg, 'kaggle.json')):
        if os.path.exists(p): os.chmod(p, 0o600)


_kaggle_credentials()
now = lambda: datetime.datetime.now(datetime.timezone.utc)


# ------------------------------------------------------------------ helpers
def authorized(query, headers):
    """Password from ?key=, the x-site-key header, or 'Authorization: Bearer <password>'. Spaces around it are ignored."""
    pw = os.environ.get('SITE_PASSWORD', '').strip()
    auth = headers.get('authorization', '')
    given = ((query.get('key') or [''])[0] or headers.get('x-site-key', '') or
             (auth[7:] if auth.lower().startswith('bearer ') else '')).strip()
    ok = bool(pw) and given == pw
    if not ok:   # diagnostic without revealing anything secret
        print('auth refused: password set on server=%s, sent via %s, length sent=%d expected=%d' % (
            bool(pw), 'query' if query.get('key') else 'x-site-key' if headers.get('x-site-key') else
            'authorization' if auth else 'nothing', len(given), len(pw)), flush=True)
    return ok


def kaggle(*args, timeout=120):
    """Run the Kaggle CLI (installed from requirements.txt) and return (ok, output)."""
    try:
        cmd = os.environ.get('SANTA_KAGGLE_CMD', '').split() or [sys.executable, '-m', 'kaggle']   # override for tests
        r = subprocess.run([*cmd, *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print('kaggle', args[:2], 'timeout', flush=True); return False, 'timeout'
    if r.returncode:
        print('kaggle', ' '.join(args[:3]), 'failed:', (r.stderr or r.stdout).strip()[-400:], flush=True)
    return r.returncode == 0, (r.stdout + r.stderr)


# ------------------------------------------------------------------ GitHub repo as storage
def gh(method, path, body=None, raw=False):
    req = urllib.request.Request('https://api.github.com/repos/%s%s' % (REPO, path), method=method,
                                 data=None if body is None else json.dumps(body).encode())
    req.add_header('Authorization', 'Bearer ' + os.environ['GITHUB_TOKEN'])
    req.add_header('Accept', 'application/vnd.github.raw+json' if raw else 'application/vnd.github+json')
    req.add_header('X-GitHub-Api-Version', '2022-11-28')
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    return data if raw else (json.loads(data) if data else {})


_REPO_OK = None


def gh_check_access():
    """Pick the first storage repo GITHUB_TOKEN can write to; fail with a clear message if none."""
    global REPO, _REPO_OK
    if _REPO_OK: REPO = _REPO_OK; return
    tried = []
    for cand in REPO_CANDIDATES:
        REPO = cand
        try:
            info = gh('GET', '')
        except urllib.error.HTTPError as e:
            tried.append('%s (HTTP %d)' % (cand, e.code)); continue
        if info.get('permissions', {}).get('push', False):
            _REPO_OK = cand; print('storage repo:', cand, flush=True); return
        tried.append('%s (read-only)' % cand)
    raise RuntimeError('storage: GITHUB_TOKEN cannot write to any of: ' + ', '.join(tried) +
                       '. Give the token Contents: read and write on one of these repositories.')


def gh_read(path, default=None):
    try:
        return gh('GET', '/contents/%s?ref=main' % path, raw=True).decode('utf-8')
    except urllib.error.HTTPError as e:
        if e.code == 404: return default
        raise


def gh_commit(files, message):
    """Write several files in ONE commit on main (Git data API). Retries once if main moved."""
    for attempt in range(2):
        head = gh('GET', '/git/ref/heads/main')['object']['sha']
        tree = gh('GET', '/git/commits/%s' % head)['tree']['sha']
        entries = []
        for path, text in files.items():
            blob = gh('POST', '/git/blobs', {'content': base64.b64encode(text.encode()).decode(), 'encoding': 'base64'})
            entries.append({'path': path, 'mode': '100644', 'type': 'blob', 'sha': blob['sha']})
        new_tree = gh('POST', '/git/trees', {'base_tree': tree, 'tree': entries})['sha']
        commit = gh('POST', '/git/commits', {'message': message, 'tree': new_tree, 'parents': [head]})['sha']
        try:
            gh('PATCH', '/git/refs/heads/main', {'sha': commit, 'force': False})
            return commit
        except urllib.error.HTTPError as e:
            if e.code != 422 or attempt: raise


# ------------------------------------------------------------------ rules
def load_puzzles():
    p = os.path.join(TMP, 'puzzles.csv')
    if not os.path.exists(p):
        d = os.path.join(TMP, 'comp'); os.makedirs(d, exist_ok=True)
        ok, out = kaggle('competitions', 'download', '-c', COMP, '-f', 'puzzles.csv', '-p', d)
        for z in glob.glob(os.path.join(d, '*.zip')): shutil.unpack_archive(z, d)
        hits = glob.glob(os.path.join(d, '**', 'puzzles.csv'), recursive=True)
        if not hits: raise RuntimeError('could not download puzzles.csv: ' + out[-300:])
        shutil.copy(hits[0], p)
    P = {}
    for r in csv.DictReader(open(p, newline='', encoding='utf-8-sig')):
        P[r['id']] = (json.loads(r['starting_values']), set(json.loads(r['targets'])), int(r['value_limit']), int(r['registers']))
    return P


def check(p, prog):
    start, targets, limit, nreg = p
    if not isinstance(prog, list) or not 0 < len(prog) <= 4096 or len(json.dumps(prog, separators=(',', ':'))) > 131072:
        return None
    reg = list(start) + [None] * (nreg - len(start)); seen = set(start)
    for ins in prog:
        if not isinstance(ins, list) or len(ins) != 4: return None
        op, a, b, d = ins
        if op not in ('+', '-', '*', '/') or not all(type(x) is int and 0 <= x < nreg for x in (a, b, d)): return None
        x, y = reg[a], reg[b]
        if x is None or y is None: return None
        if op == '+': v = x + y
        elif op == '-': v = x - y
        elif op == '*': v = x * y
        else:
            if y == 0 or x % y: return None
            v = x // y
        if not 0 < v <= limit: return None
        reg[d] = v; seen.add(v)
    return len(prog) if targets <= seen else None


def csv_programs(text):
    out = {}
    try:
        for r in csv.DictReader(io.StringIO(text)):
            try: out[r['id']] = json.loads(r['program'])
            except Exception: pass
    except Exception: pass
    return out


def notebook_programs(path):
    """Programs written in a notebook (GIFT / CHANGE / embedded blobs) - read as data, never executed."""
    try: nb = json.load(open(path, encoding='utf-8'))
    except Exception: return {}
    src = '\n'.join(''.join(c.get('source', '')) for c in nb.get('cells', []) if c.get('cell_type') == 'code')
    found = {}
    for m in re.finditer(r"GIFT\s*=\s*'''(.*?)'''", src, re.S):
        try: found.update(json.loads(zlib.decompress(base64.b64decode(''.join(m.group(1).split())))))
        except Exception: pass
    for m in re.finditer(r'^CHANGE\s*=\s*(\{.*\})\s*$', src, re.M):
        try: found.update(json.loads(m.group(1)))
        except Exception: pass
    for m in re.finditer(r"(?:'''|\"\"\")\s*([A-Za-z0-9+/=\s]{400,}?)\s*(?:'''|\"\"\")", src, re.S):
        try: raw = zlib.decompress(base64.b64decode(''.join(m.group(1).split())))
        except Exception: continue
        try:
            o = json.loads(raw)
            if isinstance(o, dict): found.update(o); continue
        except Exception: pass
        found.update(csv_programs(raw.decode('utf-8', 'replace')))
    return {k: v for k, v in found.items() if isinstance(v, list)}


def files_programs(d):
    out = []
    for f in sorted(glob.glob(os.path.join(d, '**', '*'), recursive=True)):
        low = f.lower()
        if low.endswith('.zip'):
            try: shutil.unpack_archive(f, os.path.dirname(f))
            except Exception: pass
    for f in sorted(glob.glob(os.path.join(d, '**', '*'), recursive=True)):
        low, base = f.lower(), os.path.basename(f).lower()
        if base in ('puzzles.csv', 'practice_puzzles.csv', 'sample_submission.csv'): continue
        if low.endswith('.ipynb'): out.append((f, notebook_programs(f)))
        elif low.endswith('.csv'): out.append((f, csv_programs(open(f, encoding='utf-8-sig', errors='replace').read())))
    return out


def to_csv(P, best):
    s = io.StringIO(); w = csv.writer(s); w.writerow(['id', 'program'])
    for k in sorted(P): w.writerow([k, json.dumps(best[k], separators=(',', ':'))])
    return s.getvalue()


# ------------------------------------------------------------------ the Code tab
def list_code_tab(best_scores=25, newest=60, own=()):
    """Code-tab notebooks (best scores + newest) plus your own search notebooks listed in sources.json."""
    rows = []
    queries = [('--competition', COMP, '--sort-by', 'scoreAscending', '--page-size', str(best_scores)),
               ('--competition', COMP, '--sort-by', 'dateRun', '--page-size', str(newest))]
    if own: queries.append(('--mine', '--page-size', '100'))
    for qargs in queries:
        mine = '--mine' in qargs
        ok, out = kaggle('kernels', 'list', *qargs, '--csv')
        if not ok: continue
        lines = [l for l in out.splitlines() if l.strip()]
        hi = next((i for i, l in enumerate(lines) if l.split(',')[0].strip() == 'ref' or l.split()[0] == 'ref'), None)
        if hi is None: continue
        if ',' in lines[hi]:
            for r in csv.DictReader(lines[hi:]):
                ref = r.get('ref', '').strip()
                if mine and ref.lower() not in own: continue
                rows.append((ref, (r.get('lastRunTime') or '').strip(), (r.get('title') or '').strip()))
        else:                                    # table output
            for l in lines[hi + 1:]:
                if set(l.strip()) <= set('- '): continue
                if mine and l.split()[0].lower() not in own: continue
                m = re.search(r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?', l)
                rows.append((l.split()[0], m.group(0) if m else '', l))
    return rows


def title_score(t):
    v = [int(x.replace(',', '')) for x in re.findall(r'\b1[2-3]\d,?\d{3}\b', t or '')]
    return min(v) if v else None


def status_of(ref):
    ok, out = kaggle('kernels', 'status', ref, timeout=60)
    t = out.lower()
    for k in ('complete', 'running', 'queued', 'error', 'cancel'):
        if k in t: return 'cancelled' if k == 'cancel' else k
    return 'unknown'


def kaggle_submissions():
    """(best public score, submissions today UTC) over all your Kaggle submissions."""
    ok, out = kaggle('competitions', 'submissions', '-c', COMP, '--csv')
    if not ok: return None, 0
    lines = [l for l in out.splitlines() if l.strip()]
    hi = next((i for i, l in enumerate(lines) if 'publicScore' in l), None)
    if hi is None: return None, 0
    today, head = now().date().isoformat(), lines[hi]
    if ',' in head:
        rows = list(csv.DictReader(lines[hi:])); vals = [r.get('publicScore') for r in rows]
        n = sum(1 for r in rows if str(r.get('date', '')).startswith(today))
    else:
        a = head.index('publicScore'); b = head.find('privateScore'); b = b if b > a else None
        body = [l for l in lines[hi + 1:] if not set(l.strip()) <= set('- ')]
        vals = [l[a:b] for l in body]; n = sum(1 for l in body if today in l)
    sc = []
    for v in vals:
        try: sc.append(float(str(v).strip()))
        except Exception: pass
    # submissions still being scored have no score yet: use the number in their description (e.g. "vercel 128251")
    raw = [json.dumps(r) for r in rows] if ',' in head else body
    for line in raw:
        if 'COMPLETE' not in line and 'ERROR' not in line:
            m = re.search(r'\b(1[2-3]\d),?(\d{3})\b', line.split('.csv', 1)[-1])
            if m: sc.append(float(m.group(1) + m.group(2)))
    return (int(min(sc)) if sc else None), n


# ------------------------------------------------------------------ one check
def tick(budget=25.0, allow_submit=True):   # 25 s: fits cron-job.org's 30 s timeout; the rest waits for the next minute
    t0 = time.time(); os.makedirs(TMP, exist_ok=True)
    log = {'started': now().isoformat(timespec='seconds'), 'notebooks': [], 'events': []}
    gh_check_access()
    P = load_puzzles()
    cfg = json.loads(gh_read('sources.json', '{}') or '{}')
    best_text = gh_read('state/best.csv', '') or ''
    seen = json.loads(gh_read('state/seen_kernels.json', '{}') or '{}')
    runlog = json.loads(gh_read('state/log.json', '{"submits": []}') or '{"submits": []}')
    history = gh_read('state/history.csv', 'time,score,source,note\n') or 'time,score,source,note\n'

    best = {}
    for k, prog in csv_programs(best_text).items():
        if k in P and check(P[k], prog): best[k] = prog
    before = sum(len(v) for v in best.values()) if len(best) == len(P) else None
    log['start_total'] = before

    skip = set(cfg.get('discover', {}).get('ignore', []))
    own = {k.lower() for k in cfg.get('kernels', []) if 'watcher' not in k}         # our own search notebooks
    rows = list_code_tab(cfg.get('discover', {}).get('best_scores', 25), cfg.get('discover', {}).get('page_size', 60), own)
    todo, dup = [], set()
    for ref, t, title in rows:
        if '/' in ref and ref not in skip and ref not in dup and seen.get(ref) != t:
            todo.append((ref, t, title_score(title))); dup.add(ref)
    todo.sort(key=lambda x: (x[0].lower() in own, x[2] is None, x[2] or 0))
    log['code_tab'] = len({r for r, _, _ in rows}); log['to_check'] = len(todo)

    changed_seen = False
    for ref, t, ts in todo:
        if time.time() - t0 > budget - 12: log['events'].append('time budget reached, rest on next tick'); break
        d = os.path.join(TMP, 'nb', ref.replace('/', '__')); shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
        st = status_of(ref)
        if ref.lower() not in own:                                                  # own search notebooks: output only
            kaggle('kernels', 'pull', ref, '-p', d, timeout=60)                     # the .ipynb, at once
        elif st not in ('complete', 'error', 'cancelled'):
            log['notebooks'].append({'ref': ref, 'title_score': ts, 'status': st, 'saved': 0}); continue
        if st == 'complete':
            kaggle('kernels', 'output', ref, '-p', d, timeout=90)                    # its output, once finished
        gain = 0
        for f, progs in files_programs(d):
            for k, prog in progs.items():
                if k not in P: continue
                n = check(P[k], prog)
                if n is None: continue
                if k not in best or n < len(best[k]):
                    if k in best: gain += len(best[k]) - n
                    best[k] = prog
        log['notebooks'].append({'ref': ref, 'title_score': ts, 'status': st, 'saved': gain})
        if st in ('complete', 'error', 'cancelled'):
            seen[ref] = t; changed_seen = True                                       # still running -> next tick retries
            if ref.lower() in own:                                                   # finished search run: start it again
                pend = runlog.setdefault('restart_pending', [])
                if ref not in pend: pend.append(ref); changed_seen = True

    total = sum(len(v) for v in best.values()) if len(best) == len(P) else None
    log['total'] = total
    files, note = {}, []
    if total and (before is None or total < before):
        for k in P: assert check(P[k], best[k]) is not None
        text = to_csv(P, best)
        files['state/best.csv'] = text; files['dataset/best.csv'] = text
        src = ', '.join(n['ref'].split('/')[0] for n in log['notebooks'] if n['saved'])[:80]
        history = history.rstrip('\n') + '\n%s,%d,vercel,"%s"\n' % (now().strftime('%Y-%m-%d %H:%M'), total, src.replace('"', "'"))
        files['state/history.csv'] = history
        note.append('new best %d' % total)
        open(os.path.join(TMP, 'submission.csv'), 'w').write(text)

    # submit: better than everything on Kaggle, within the daily cap (optional minimum gap from sources.json)
    if allow_submit and total:
        last = max([s.get('time', '') for s in runlog.get('submits', [])] or [''])
        gap_min = float(cfg.get('min_minutes_between_submits', 0) or 0)
        gap_ok = not last or (now() - datetime.datetime.fromisoformat(last.replace('Z', '+00:00'))).total_seconds() >= gap_min * 60
        if gap_ok and time.time() - t0 < budget - 6:
            kb, kn = kaggle_submissions()
            mine = [s.get('score') for s in runlog.get('submits', [])[-5:]]       # our own recent submits, even if still scoring
            known = [x for x in (kb, cfg.get('already_submitted_floor'), *mine) if x]
            floor = min(known) if known else None
            cap = cfg.get('max_submits_per_day', 20)
            if floor is not None and floor - total < cfg.get('min_gain_to_submit', 1):
                log['events'].append('not submitted: %d is not better than %d already on Kaggle' % (total, floor))
            elif kn >= cap:
                log['events'].append('not submitted: %d submissions today, daily limit %d' % (kn, cap))
            else:
                p = os.path.join(TMP, 'submission.csv')
                if not os.path.exists(p): open(p, 'w').write(to_csv(P, best))
                ok, out = kaggle('competitions', 'submit', '-c', COMP, '-f', p, '-m', 'vercel %d' % total, timeout=120)
                log['events'].append(('SUBMITTED %d' % total) if ok else ('submit failed: ' + out[-200:]))
                if ok:
                    runlog.setdefault('submits', []).append({'date': now().date().isoformat(), 'time': now().isoformat(timespec='minutes'), 'score': total})
                    note.append('submitted %d' % total)
    # keep the Kaggle dataset santa-2026-best equal to the current best, so search notebooks start from it
    cur = total or before
    ds_id = cfg.get('own_dataset', 'othnielobasi/santa-2026-best')
    if cur and (runlog.get('dataset_score') is None or cur < runlog['dataset_score']) and time.time() - t0 < budget - 8:
        last_ds = runlog.get('dataset_time')
        if not last_ds or (now() - datetime.datetime.fromisoformat(last_ds)).total_seconds() >= 15 * 60:
            dsd = os.path.join(TMP, 'ds'); shutil.rmtree(dsd, ignore_errors=True); os.makedirs(dsd)
            json.dump({'title': ds_id.split('/')[1], 'id': ds_id, 'licenses': [{'name': 'CC0-1.0'}]}, open(os.path.join(dsd, 'dataset-metadata.json'), 'w'))
            open(os.path.join(dsd, 'best.csv'), 'w').write(to_csv(P, best))
            ok, out = kaggle('datasets', 'version', '-p', dsd, '-m', 'best %d' % cur, timeout=60)
            log['events'].append(('dataset updated to %d' % cur) if ok else ('dataset update failed: ' + out[-160:]))
            if ok:
                runlog['dataset_score'] = cur; runlog['dataset_time'] = now().isoformat(timespec='seconds'); note.append('dataset %d' % cur)
    # restart one finished (or flagged) search notebook per tick, once the dataset has had 5 minutes to process
    if cfg.get('restart_search_now') and not runlog.get('restart_all_done'):
        runlog['restart_pending'] = sorted(set(runlog.get('restart_pending', [])) | {k for k in cfg.get('kernels', []) if 'watcher' not in k})
        runlog['restart_all_done'] = True; note.append('restart requested')
    if not cfg.get('restart_search_now'): runlog.pop('restart_all_done', None)
    pend = runlog.get('restart_pending', [])
    ds_ready = runlog.get('dataset_score') == cur and runlog.get('dataset_time') and \
        (now() - datetime.datetime.fromisoformat(runlog['dataset_time'])).total_seconds() >= 5 * 60
    if pend and ds_ready and time.time() - t0 < budget - 10:
        ref = pend[0]; kd = os.path.join(TMP, 'push'); shutil.rmtree(kd, ignore_errors=True); os.makedirs(kd)
        ok1, out1 = kaggle('kernels', 'pull', ref, '-p', kd, '-m', timeout=60)
        ok2, out2 = kaggle('kernels', 'push', '-p', kd, timeout=90) if ok1 else (False, out1)
        log['events'].append(('restarted %s from %d' % (ref, cur)) if ok2 else ('restart %s failed: %s' % (ref, out2[-160:])))
        if ok2 or 'not found' in out2.lower():
            pend.pop(0); note.append('restart %s' % ref.split('/')[-1])
    if changed_seen: files['state/seen_kernels.json'] = json.dumps(seen, indent=1, sort_keys=True)
    if files or note:
        runlog['last_run'] = {'time': now().isoformat(timespec='minutes'), 'score': total}
        runlog['vercel_last_change'] = log['started']
        files['state/log.json'] = json.dumps(runlog, indent=1)
        log['commit'] = gh_commit(files, 'vercel: ' + ('; '.join(note) if note else 'merged notebooks %s' % ', '.join(n['ref'] for n in log['notebooks'])))
    log['seconds'] = round(time.time() - t0, 1)
    print('TICK', json.dumps({k: log.get(k) for k in ('start_total', 'total', 'code_tab', 'to_check', 'events', 'seconds')}),
          [(n['ref'], n['status'], n['saved']) for n in log['notebooks']], flush=True)
    return log


def save_best(text, note='manual merge'):
    """Merge an uploaded submission into the stored best (shortest per puzzle); commit if better."""
    gh_check_access()
    P = load_puzzles()
    up = csv_programs(text)
    best = {}
    for k, prog in csv_programs(gh_read('state/best.csv', '') or '').items():
        if k in P and check(P[k], prog): best[k] = prog
    before = sum(len(v) for v in best.values()) if len(best) == len(P) else None
    for k, prog in up.items():
        if k in P:
            n = check(P[k], prog)
            if n and (k not in best or n < len(best[k])): best[k] = prog
    if len(best) != len(P): raise RuntimeError('only %d of %d puzzles have a valid program' % (len(best), len(P)))
    total = sum(len(v) for v in best.values())
    if before is not None and total >= before:
        return {'saved': False, 'total': before, 'note': 'stored best is already as good'}
    text = to_csv(P, best)
    hist = (gh_read('state/history.csv', 'time,score,source,note\n') or 'time,score,source,note\n').rstrip('\n')
    hist += '\n%s,%d,site,"%s"\n' % (now().strftime('%Y-%m-%d %H:%M'), total, note.replace('"', "'")[:80])
    gh_commit({'state/best.csv': text, 'dataset/best.csv': text, 'state/history.csv': hist}, 'site: new best %d' % total)
    return {'saved': True, 'total': total, 'before': before}


def status():
    gh_check_access()
    hist = gh_read('state/history.csv', '') or ''
    runlog = json.loads(gh_read('state/log.json', '{}') or '{}')
    seen = json.loads(gh_read('state/seen_kernels.json', '{}') or '{}')
    rows = list(csv.DictReader(io.StringIO(hist))) if hist else []
    return {'history': [{'time': r['time'], 'total': int(r['score']), 'note': (r.get('source', '') + ' ' + r.get('note', '')).strip()} for r in rows if r.get('score')],
            'submits': runlog.get('submits', [])[-10:], 'last_run': runlog.get('last_run'),
            'notebooks_merged': len(seen), 'checked_at': now().isoformat(timespec='seconds'), 'storage': REPO}
