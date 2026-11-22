"""GET /api/tick?key=SITE_PASSWORD - one automatic check of the Code tab (called every minute by cron-job.org)."""
import json, os, sys, traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
sys.path.insert(0, os.path.dirname(__file__))
import _santa


class handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, indent=1).encode()
        self.send_response(code); self.send_header('Content-Type', 'application/json'); self.send_header('Cache-Control', 'no-store')
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        if not _santa.authorized(q, {k.lower(): v for k, v in self.headers.items()}):
            return self._send(401, {'error': 'wrong or missing key'})
        missing = [v for v in ('GITHUB_TOKEN',) if not os.environ.get(v)]
        if not (os.environ.get('KAGGLE_API_TOKEN') or (os.environ.get('KAGGLE_USERNAME') and os.environ.get('KAGGLE_KEY'))):
            missing.append('KAGGLE_API_TOKEN (or KAGGLE_USERNAME + KAGGLE_KEY)')
        if missing: return self._send(500, {'error': 'missing environment variables: ' + ', '.join(missing)})
        try:
            self._send(200, _santa.tick(allow_submit=(q.get('submit') or ['1'])[0] != '0'))
        except Exception as e:
            self._send(500, {'error': repr(e), 'trace': traceback.format_exc()[-1500:]})

    do_POST = do_GET
