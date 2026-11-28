"""GET /api/control?key=..             -> {"submit_paused": bool}
POST /api/control?key=..&paused=1|0 -> pause / resume automatic submissions to Kaggle."""
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

    def _auth(self):
        q = parse_qs(urlparse(self.path).query)
        return q if _santa.authorized(q, {k.lower(): v for k, v in self.headers.items()}) else None

    def do_GET(self):
        q = self._auth()
        if q is None: return self._send(401, {'error': 'wrong or missing site password'})
        try:
            _santa.gh_check_access()
            c = json.loads(_santa.gh_read('state/control.json', '{}') or '{}')
            self._send(200, {'submit_paused': bool(c.get('submit_paused'))})
        except Exception as e:
            print('ERROR', traceback.format_exc(), flush=True); self._send(500, {'error': str(e)})

    def do_POST(self):
        n = int(self.headers.get('content-length') or 0)
        if n: self.rfile.read(n)
        q = self._auth()
        if q is None: return self._send(401, {'error': 'wrong or missing site password'})
        paused = (q.get('paused') or ['1'])[0] not in ('0', 'false', 'no')
        try:
            self._send(200, _santa.set_submit_paused(paused))
        except Exception as e:
            print('ERROR', traceback.format_exc(), flush=True); self._send(500, {'error': str(e)})
