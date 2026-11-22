"""GET /api/status?key=SITE_PASSWORD - score history, submissions and last run, read from the private repo."""
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

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        if not _santa.authorized(q, {k.lower(): v for k, v in self.headers.items()}):
            return self._send(401, {'error': 'wrong or missing key'})
        if not os.environ.get('GITHUB_TOKEN'):
            return self._send(500, {'error': 'missing environment variable GITHUB_TOKEN'})
        try:
            self._send(200, _santa.status())
        except Exception as e:
            self._send(500, {'error': repr(e), 'trace': traceback.format_exc()[-1200:]})
