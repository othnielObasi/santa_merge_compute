"""GET /api/puzzles?key=SITE_PASSWORD - the competition's puzzles.csv, fetched from Kaggle with your key (not public)."""
import os, sys, traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
sys.path.insert(0, os.path.dirname(__file__))
import _santa


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        if not _santa.authorized(q, {k.lower(): v for k, v in self.headers.items()}):
            self.send_response(401); self.end_headers(); self.wfile.write(b'wrong or missing key'); return
        try:
            _santa.load_puzzles()
            body = open(os.path.join(_santa.TMP, 'puzzles.csv'), 'rb').read()
            self.send_response(200); self.send_header('Content-Type', 'text/csv'); self.send_header('Cache-Control', 'no-store')
            self.end_headers(); self.wfile.write(body)
        except Exception:
            print('ERROR', traceback.format_exc(), flush=True)
            self.send_response(500); self.end_headers(); self.wfile.write(b'could not fetch puzzles.csv from Kaggle')
