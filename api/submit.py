"""POST /api/submit?key=SITE_PASSWORD&message=... body = submission.csv
Re-checks every program on the server, then runs: kaggle competitions submit -c santa-2026 -f submission.csv -m "message"."""
import json, os, sys, traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
sys.path.insert(0, os.path.dirname(__file__))
import _santa


class handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header('Content-Type', 'application/json'); self.send_header('Cache-Control', 'no-store')
        self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        q = parse_qs(urlparse(self.path).query)
        n = int(self.headers.get('content-length') or 0)
        data = self.rfile.read(n) if n else b''                     # always read the body first
        if not _santa.authorized(q, {k.lower(): v for k, v in self.headers.items()}):
            return self._send(401, {'error': 'wrong or missing site password'})
        try:
            text = data.decode('utf-8-sig')
            P = _santa.load_puzzles()
            progs = _santa.csv_programs(text)
            bad = [k for k in P if k not in progs or _santa.check(P[k], progs[k]) is None]
            if bad:
                return self._send(400, {'error': '%d puzzles missing or invalid (e.g. %s) - not submitted' % (len(bad), ', '.join(bad[:3]))})
            total = sum(len(progs[k]) for k in P)
            path = os.path.join(_santa.TMP, 'submission.csv'); os.makedirs(_santa.TMP, exist_ok=True)
            open(path, 'w', encoding='utf-8').write(_santa.to_csv(P, progs))
            msg = ((q.get('message') or [''])[0] or 'merge %d' % total)[:200]
            ok, out = _santa.kaggle('competitions', 'submit', '-c', _santa.COMP, '-f', path, '-m', msg, timeout=120)
            print('SUBMIT', total, 'ok' if ok else 'failed', out.strip()[-300:], flush=True)
            if not ok:
                return self._send(502, {'error': 'Kaggle refused the submission: ' + out.strip()[-300:]})
            self._send(200, {'submitted': total, 'message': msg, 'kaggle': out.strip()[-300:]})
        except Exception as e:
            print('ERROR', traceback.format_exc(), flush=True)
            self._send(500, {'error': repr(e)})
