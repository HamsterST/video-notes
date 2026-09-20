"""Code/native registration stay installed; large persistent data is configurable."""
import json
import os
from pathlib import Path

INSTALL_DIR = Path(__file__).resolve().parents[1]

def data_directory(root=INSTALL_DIR):
    root = Path(root).resolve()
    config = root / 'storage.json'
    if config.exists():
        value = json.loads(config.read_text(encoding='utf-8'))['data_dir']
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError('storage.json 的 data_dir 必须是绝对路径。')
        return Path(value).resolve()
    # Preserve existing installations; never silently strand their models/caches.
    legacy = root / 'data'
    if any((legacy / name).exists() for name in ('settings.json', 'models', 'downloads', 'cache', 'results')):
        return legacy
    return (Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'VideoNotes').resolve()

DATA_DIR = data_directory()
DOWNLOAD_DIR = DATA_DIR / 'downloads'
RESULTS_DIR = DATA_DIR / 'results'
NATIVE_DIR = INSTALL_DIR / 'data/native'
MODELS_DIR = DATA_DIR / 'models'
