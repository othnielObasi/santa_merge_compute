"""GET /api/best?key=SITE_PASSWORD - the current best submission.csv from the private repo."""
import os, sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
sys.path.insert(0, os.path.dirname(__file__))
import _santa


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        if not _santa.authorized(q, {k.lower(): v for k, v in self.headers.items()}) or not os.environ.get('GITHUB_TOKEN'):
            self.send_response(401); self.end_headers(); self.wfile.write(b'wrong or missing key'); return
        body = (_santa.gh_read('state/best.csv', '') or '').encode()
        self.send_response(200); self.send_header('Content-Type', 'text/csv')
        self.send_header('Content-Disposition', 'attachment; filename="submission.csv"'); self.send_header('Cache-Control', 'no-store')
        self.end_headers(); self.wfile.write(body)
