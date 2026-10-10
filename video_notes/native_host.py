"""Chrome Native Messaging host; credentials remain in memory."""
import contextlib
import json
import math
import os
from pathlib import Path
import re
import struct
import sys
from datetime import datetime
from functools import partial
from http.cookiejar import Cookie
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4

from yt_dlp.cookies import YoutubeDLCookieJar
from .workflow import run_workflow
from .media import download_and_transcribe
from .paths import DATA_DIR, DOWNLOAD_DIR, RESULTS_DIR
from .douyin import DouyinShareError, resolve_douyin_share_url


def read_exact(stream, size):
    parts = bytearray()
    while len(parts) < size:
        block = stream.read(size - len(parts))
        if not block:
            raise EOFError()
        parts.extend(block)
    return bytes(parts)


def make_jar(message):
    url = resolve_douyin_share_url(message.get('url', ''))
    u = urlsplit(url)
    host = u.hostname or ''
    if u.scheme != 'https' or u.username or u.password or u.port not in (None, 443):
        raise ValueError('仅支持 HTTPS 视频链接。')
    if (host == 'bilibili.com' or host.endswith('.bilibili.com')) and re.match(r'^/video/(BV[A-Za-z0-9]+|av\d+)', u.path):
        domain = 'bilibili.com'
    elif (host == 'youtube.com' or host.endswith('.youtube.com')) and ((u.path == '/watch' and parse_qs(u.query).get('v')) or re.match(r'^/(shorts|live)/[\w-]+', u.path)):
        domain = 'youtube.com'
    elif host in {'douyin.com', 'www.douyin.com'} and re.fullmatch(r'/video/\d+/?', u.path):
        domain = 'douyin.com'
    else:
        raise ValueError('当前扩展暂不支持此链接，请提供已适配网站的单个视频链接。')
    rows = message.get('cookies')
    if not isinstance(rows, list) or len(rows) > 2000:
        raise ValueError('Cookie 数据格式无效。')
    jar = YoutubeDLCookieJar()
    for row in rows:
        if not isinstance(row, dict) or row.get('partitionKey'):
            continue
        d = row.get('domain', '')
        if not isinstance(d, str) or not (d.lstrip('.') == domain or d.lstrip('.').endswith('.' + domain)):
            continue
        name, value, path = row.get('name'), row.get('value'), row.get('path', '/')
        if not all(isinstance(v, str) for v in (name, value, path)) or any(ch in name + value + path for ch in '\r\n\0') or not path.startswith('/'):
            raise ValueError('Cookie 数据格式无效。')
        expiry = row.get('expirationDate')
        if expiry is not None:
            if not isinstance(expiry, (int, float)) or not math.isfinite(expiry):
                raise ValueError('Cookie 有效期无效。')
            expiry = int(expiry)
        jar.set_cookie(Cookie(0, name, value, None, False, d, not row.get('hostOnly', not d.startswith('.')), d.startswith('.'), path, True,
                              bool(row.get('secure')), expiry, expiry is None, None, None,
                              {'HttpOnly': None} if row.get('httpOnly') else {}, False))
    return url, jar


def main():
    if os.name == 'nt':
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    incoming, outgoing = sys.stdin.buffer, sys.stdout.buffer
    def send(payload):
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(data) > 900000:
            raise ValueError('响应过大。')
        outgoing.write(struct.pack('=I', len(data)) + data)
        outgoing.flush()
    try:
        size = struct.unpack('=I', read_exact(incoming, 4))[0]
        if size > 4 * 1024 * 1024:
            raise ValueError('请求过大。')
        message = json.loads(read_exact(incoming, size))
        if not isinstance(message, dict) or message.get('version') != 1 or message.get('fallback') not in ('stop', 'transcribe'):
            raise ValueError('请求格式不支持。')
        url, jar = make_jar(message)
        fallback = message['fallback']
        del message
        base = DATA_DIR
        base.mkdir(parents=True, exist_ok=True)
        # Third-party text must never corrupt the stdout framing or reveal credentials.
        with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            transcriber = partial(download_and_transcribe, download_dir=str(DOWNLOAD_DIR))
            result = run_workflow(url, cookie_jar=jar, cookies_from_browser=None, fallback=fallback,
                                  progress=lambda text: send({'type': 'progress', 'message': text}),
                                  transcriber=transcriber, transcript_loader=partial(transcriber, cache_only=True))
        jar.clear()
        lines = [result['message'], *result.get('warnings', [])]
        lines += [f"[{s['start']:.1f}s–{s['end']:.1f}s] " + (f"[{s['speaker']}] " if s.get('speaker') else '') + s['text']
                  for s in result.get('segments', [])]
        transcript = '\n'.join(lines)
        files = []
        if result.get('status') == 'ok':
            folder = RESULTS_DIR
            folder.mkdir(exist_ok=True)
            stem = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid4().hex[:8]
            for suffix, content in (('.txt', transcript), ('.json', json.dumps(result, ensure_ascii=False, indent=2))):
                path = folder / (stem + suffix)
                path.write_text(content, encoding='utf-8')
                files.append(str(path))
        send({'type':'result', 'status':result['status'], 'message':result['message'], 'files':files,
              'preview': transcript[:40000] + ('\n页面仅预览前 40000 字符，完整原文已保存。' if len(transcript)>40000 else '')})
    except (EOFError, BrokenPipeError):
        return 1
    except DouyinShareError as exc:
        try:
            send({'type': 'error', 'message': str(exc)})
        except BrokenPipeError:
            pass
        return 2
    except Exception:
        try:
            send({'type':'error','message':'处理失败，请检查本机依赖、视频链接和输出目录权限。登录凭据不会写入错误日志。'})
        except BrokenPipeError:
            pass
        return 2
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
