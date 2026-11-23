"""POST /api/save?key=SITE_PASSWORD body = submission.csv - keep it as the shared best (every device sees it)."""
import json, os, sys, traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
sys.path.insert(0, os.path.dirname(__file__))
import _santa


class handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        q = parse_qs(urlparse(self.path).query)
        n = int(self.headers.get('content-length') or 0); data = self.rfile.read(n) if n else b''
        if not _santa.authorized(q, {k.lower(): v for k, v in self.headers.items()}):
            return self._send(401, {'error': 'wrong or missing site password'})
        try:
            self._send(200, _santa.save_best(data.decode('utf-8-sig'), (q.get('note') or ['manual merge'])[0]))
        except Exception as e:
            print('ERROR', traceback.format_exc(), flush=True)
            self._send(500, {'error': str(e)})
