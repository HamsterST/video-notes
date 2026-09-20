"""Authenticated loopback broker, launched by Chrome through Native Messaging."""
import hmac
import json
import os
import secrets
import struct
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

from .paths import NATIVE_DIR

PORT = 17653

def config_path():
    return NATIVE_DIR / 'bridge-secret.json'


def main():
    if os.name == 'nt':
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    from .native_host import read_exact
    token = json.loads(config_path().read_text(encoding='utf-8'))['token']
    pending, guard, writer = {}, threading.Lock(), threading.Lock()
    def send(obj):
        data = json.dumps(obj).encode('utf-8')
        with writer:
            sys.stdout.buffer.write(struct.pack('=I', len(data)) + data)
            sys.stdout.buffer.flush()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            if self.headers.get('Origin') or self.headers.get('Host') != f'127.0.0.1:{PORT}' or not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                self.send_error(403)
                return
            if self.path != '/cookies':
                self.send_error(404)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 8192:
                    raise ValueError()
                body = json.loads(self.rfile.read(size))
                from .native_host import make_jar
                url, _ = make_jar({'url':body.get('url'), 'cookies':[]})
                request_id = uuid4().hex
                event, result = threading.Event(), {}
                with guard:
                    if len(pending) >= 8:
                        self.send_error(503)
                        return
                    pending[request_id] = (event, result)
                try:
                    send({'type':'cookies_request','id':request_id,'url':url})
                    if not event.wait(25):
                        self.send_error(504)
                        return
                    data = json.dumps(result).encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type','application/json')
                    self.send_header('Cache-Control','no-store')
                    self.send_header('Content-Length',str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                finally:
                    with guard:
                        pending.pop(request_id, None)
            except (ValueError, TypeError, AttributeError):
                self.send_error(400)
            except (BrokenPipeError, OSError):
                pass
        def setup(self):
            super().setup()
            self.connection.settimeout(35)
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        while True:
            size = struct.unpack('=I', read_exact(sys.stdin.buffer, 4))[0]
            if size > 4 * 1024 * 1024:
                break
            message = json.loads(read_exact(sys.stdin.buffer, size))
            if not isinstance(message, dict):
                break
            with guard:
                item = pending.get(message.get('id'))
                if item:
                    item[1].update(message)
                    item[0].set()
    except (EOFError, OSError, ValueError):
        pass
    finally:
        server.shutdown()
        server.server_close()

if __name__ == '__main__':
    main()
