"""Persistent storage selection and bounded, app-owned media cleanup."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import time
from .paths import INSTALL_DIR, DATA_DIR, DOWNLOAD_DIR
from .cache import atomic_json, file_identity

MAX_AGE_DAYS = 7
MAX_BYTES = 2 * 1024 ** 3

def policy(base=DATA_DIR):
    try:
        value = json.loads((Path(base) / 'media-policy.json').read_text(encoding='utf-8'))
        return {'keep_media': value.get('keep_media') is True}
    except (OSError, ValueError, AttributeError):
        return {'keep_media': False}

@contextmanager
def media_lock(base):
    """OS releases the lock after crashes. No cleanup can race our own writers."""
    base = Path(base).resolve()
    base.mkdir(parents=True, exist_ok=True)
    with (base / '.media.lock').open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('媒体缓存正在使用中，请等待当前下载或转写完成后重试。') from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)

def cleanup_media(base=DOWNLOAD_DIR, *, all_media=False, now=None):
    # Caller holds media_lock. Never inspect arbitrary user-provided local media.
    base = Path(base).resolve()
    if not all_media and policy()['keep_media']:
        return []
    now = time.time() if now is None else now
    entries = []
    for folder in base.iterdir():
        if not folder.is_dir() or folder.is_symlink() or (hasattr(folder, 'is_junction') and folder.is_junction()):
            continue
        if folder.resolve().parent != base or not (folder / '.videonotes-media').is_file():
            continue
        children = list(folder.rglob('*'))
        if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()) for p in children):
            continue
        size = sum(p.stat().st_size for p in children if p.is_file())
        touched = (folder / '.videonotes-media').stat().st_mtime
        entries.append((touched, size, folder))
    total = sum(size for _, size, _ in entries)
    removed = []
    for touched, size, folder in sorted(entries):
        if all_media or now - touched > MAX_AGE_DAYS * 86400 or total > MAX_BYTES:
            # Fully resolved boundary verified above; only our marked cache folders.
            shutil.rmtree(folder)
            total -= size
            removed.append(str(folder))
    return removed

def move_data(destination):
    """Copy then switch. No destructive migration; refuse conflicting destinations."""
    target = Path(destination).expanduser()
    if not target.is_absolute():
        raise ValueError('请提供数据目录的绝对路径。')
    target = target.resolve()
    old = DATA_DIR.resolve()
    if target == old:
        return old
    if target.is_relative_to(old) or old.is_relative_to(target):
        raise ValueError('新旧数据目录不能互相包含。')
    names = ('settings.json', 'models', 'downloads', 'cache', 'results', 'media-policy.json')
    with media_lock(DOWNLOAD_DIR):
        if target.is_symlink() or (hasattr(target, 'is_junction') and target.is_junction()):
            raise ValueError('目标数据目录不能是链接。')
        pairs = []
        for name in names:
            source = old / name
            if not source.exists():
                continue
            candidates = [source] if source.is_file() else [source, *source.rglob('*')]
            for item in candidates:
                if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
                    raise ValueError('数据目录包含链接，请先处理后再迁移。')
                if item.is_file() and item.name != '.media.lock':
                    dest = target / item.relative_to(old)
                    if dest.resolve() != dest or any(parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()) for parent in [dest, *dest.parents] if parent != target.parent and parent.is_relative_to(target)):
                        raise ValueError('目标目录包含链接，未迁移。')
                    if dest.exists() and (not dest.is_file() or file_identity(item) != file_identity(dest)):
                        raise ValueError('目标目录包含不同的同名文件，未切换数据目录。')
                    pairs.append((item, dest))
        for source, dest in pairs:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                shutil.copy2(source, dest)
            if file_identity(source) != file_identity(dest):
                raise OSError('迁移文件校验失败，未切换数据目录。')
        target.mkdir(parents=True, exist_ok=True)
        # runtime media_dir is advisory; SKILL reads --show-storage after configuration.
        runtime_path = INSTALL_DIR / 'runtime.json'
        if runtime_path.exists():
            runtime = json.loads(runtime_path.read_text(encoding='utf-8'))
            runtime.update(data_dir=str(target), media_dir=str(target / 'downloads'))
            atomic_json(runtime_path, runtime)
        atomic_json(INSTALL_DIR / 'storage.json', {'data_dir': str(target)})
    return target

def manage_storage(args):
    if args.set_data_dir and (args.clean_media or args.keep_media is not None):
        raise ValueError('迁移与清理/保留策略请分次执行。')
    data = move_data(args.set_data_dir) if args.set_data_dir else DATA_DIR
    if args.keep_media is not None:
        atomic_json(DATA_DIR / 'media-policy.json', {'keep_media': args.keep_media})
    removed = []
    if args.clean_media:
        with media_lock(DOWNLOAD_DIR):
            removed = cleanup_media(all_media=True)
    return {'data_dir': str(data), 'media_dir': str(data / 'downloads'),
            'models_dir': str(data / 'models'), 'cache_dir': str(data / 'cache'),
            'keep_media': policy(data)['keep_media'], 'media_max_age_days': MAX_AGE_DAYS,
            'media_max_bytes': MAX_BYTES, 'removed': removed,
            'migration': 'copied; original files retained; restart Chrome to reload paths' if args.set_data_dir else None}
