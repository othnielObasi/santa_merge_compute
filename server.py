"""Santa 2026 merge site as one always-on server (for Render, or any host that runs `python server.py`).

* Serves the page (index.html, app.js, core.js, vendor/) and the same /api/* endpoints as the Vercel version.
* Checks the Kaggle Code tab by itself once a minute in a background thread, so nothing outside has to
  trigger the work. cron-job.org (or any pinger) only needs to call /api/tick or /healthz now and then to
  keep a free instance awake; /api/tick answers with the latest check instead of starting a second one.
"""
import importlib, json, mimetypes, os, sys, threading, time, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, 'api'))
import _santa  # noqa: E402

MODULES = {n: importlib.import_module(n) for n in ('best', 'puzzles', 'save', 'status', 'submit')}
LOCK = threading.Lock()
LAST = {'result': None, 'at': 0.0}
INTERVAL = float(os.environ.get('CHECK_EVERY_SECONDS', 60))


def env_missing():
    miss = [v for v in ('GITHUB_TOKEN', 'SITE_PASSWORD') if not os.environ.get(v)]
    if not (os.environ.get('KAGGLE_API_TOKEN') or (os.environ.get('KAGGLE_USERNAME') and os.environ.get('KAGGLE_KEY'))):
        miss.append('KAGGLE_API_TOKEN (or KAGGLE_USERNAME + KAGGLE_KEY)')
    return miss


def run_tick(budget):
    with LOCK:
        try:
            r = _santa.tick(budget=budget)
        except Exception as e:
            print('ERROR tick', traceback.format_exc(), flush=True)
            r = {'error': repr(e)}
        LAST.update(result=r, at=time.time())
        return r


def loop():
    time.sleep(15)
    while True:
        t0 = time.time()
        miss = env_missing()
        if miss:
            print('waiting for environment variables:', ', '.join(miss), flush=True)
        else:
            run_tick(budget=max(25.0, INTERVAL * 1.6))      # a slow instance may take longer than a minute
        time.sleep(max(5.0, INTERVAL - (time.time() - t0)))


STATIC = {'/': 'index.html', '/index.html': 'index.html', '/index': 'index.html', '/app.js': 'app.js', '/core.js': 'core.js'}


class Handler(BaseHTTPRequestHandler):
    server_version = 'santa'

    def log_message(self, fmt, *args):            # one short line per request, without the query (it may hold the key)
        print('%s %s %s' % (self.command, urlparse(self.path).path, args[1] if len(args) > 1 else ''), flush=True)

    def _json(self, code, obj):
        body = json.dumps(obj, indent=1).encode()
        self.send_response(code); self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body)

    def do_GET(self): self.route('GET')
    def do_POST(self): self.route('POST')
    def do_HEAD(self): self.route('GET')

    def route(self, method):
        path = urlparse(self.path).path.rstrip('/') or '/'
        if path == '/healthz':
            return self._json(200, {'ok': True, 'last_check_seconds_ago': round(time.time() - LAST['at']) if LAST['at'] else None})
        if path.startswith('/api/'):
            name = path[5:].split('/')[0]
            if name == 'tick':
                return self.tick()
            mod = MODULES.get(name)
            if not mod:
                return self._json(404, {'error': 'no such endpoint'})
            fn = getattr(mod.handler, 'do_' + method, None)
            if not fn:
                return self._json(405, {'error': 'method not allowed'})
            self.close_connection = True
            self.__class__ = mod.handler                  # hand the request to the original endpoint code
            return fn(self)
        rel = STATIC.get(path) or (path[1:] if path.startswith('/vendor/') and '..' not in path else None)
        full = os.path.join(ROOT, rel) if rel else None
        if not full or not os.path.isfile(full):
            return self._json(404, {'error': 'not found'})
        data = open(full, 'rb').read()
        self.send_response(200)
        self.send_header('Content-Type', (mimetypes.guess_type(full)[0] or 'application/octet-stream') + ('; charset=utf-8' if full.endswith(('.html', '.js')) else ''))
        self.send_header('X-Content-Type-Options', 'nosniff'); self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-cache'); self.end_headers()
        if self.command != 'HEAD': self.wfile.write(data)

    def tick(self):
        q = parse_qs(urlparse(self.path).query)
        if not _santa.authorized(q, {k.lower(): v for k, v in self.headers.items()}):
            return self._json(401, {'error': 'wrong or missing key'})
        miss = env_missing()
        if miss:
            return self._json(500, {'error': 'missing environment variables: ' + ', '.join(miss)})
        if LAST['result'] is not None and time.time() - LAST['at'] < 50:
            return self._json(200, LAST['result'])          # the background check ran moments ago
        if LOCK.locked():                                   # a check is running: wait for it
            t_end = time.time() + 120
            while LOCK.locked() and time.time() < t_end: time.sleep(0.5)
            return self._json(200, LAST['result'] or {'error': 'check still running'})
        return self._json(200, run_tick(budget=25.0))


for _m in MODULES.values():                               # their request log lines must not show ?key=
    _m.handler.log_message = Handler.log_message


if __name__ == '__main__':
    threading.Thread(target=loop, daemon=True).start()
    port = int(os.environ.get('PORT', 10000))
    print('serving on port', port, flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()
