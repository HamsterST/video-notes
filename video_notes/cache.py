"""Complete-result caches; interrupted recognition is never committed."""
import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone
from .paths import DATA_DIR

CACHE_DIR = DATA_DIR / 'cache'

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()

def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)

def file_identity(path):
    path = Path(path)
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    return {'size': path.stat().st_size, 'sha256': hasher.hexdigest()}

def valid_result(result):
    if not isinstance(result, dict) or result.get('status') != 'ok':
        return False
    rows = result.get('segments')
    return isinstance(rows, list) and bool(rows) and all(
        isinstance(r, dict) and isinstance(r.get('text'), str) and r['text'].strip()
        and all(isinstance(r.get(k), (int, float)) and math.isfinite(r[k]) for k in ('start', 'end'))
        and 0 <= r['start'] <= r['end'] for r in rows)

def read(kind, identity):
    try:
        path = CACHE_DIR / kind / (digest(identity) + '.json')
        envelope = json.loads(path.read_text(encoding='utf-8'))
        result = envelope['result']
        if envelope['sha256'] != digest(result) or not valid_result(result):
            return None
        result['cache'] = {'reused': True, 'kind': kind}
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return None

def write(kind, identity, result):
    if not valid_result(result):
        return
    # Authentication diagnostics describe a past request, not the current login.
    saved = {k: v for k, v in result.items() if k not in ('authentication', 'diagnostics', 'subtitle_query_diagnostics')}
    try:
        atomic_json(CACHE_DIR / kind / (digest(identity) + '.json'), {'result': saved, 'sha256': digest(saved)})
    except OSError:
        result.setdefault('warnings', []).append('完整结果未能写入缓存，下次可能需要重新获取。')

def utc_now():
    return datetime.now(timezone.utc).isoformat()
