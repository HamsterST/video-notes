"""One optional first-run model question, shared by CLI and native host."""
import json
import sys
from pathlib import Path
from .paths import DATA_DIR, MODELS_DIR
from .cache import atomic_json

SETTINGS_FILE = DATA_DIR / 'settings.json'
RECOMMENDED_MODELS = ('small', 'large-v3-turbo', 'large-v3')

def saved_model():
    try:
        value = json.loads(SETTINGS_FILE.read_text(encoding='utf-8')).get('model')
        return value if isinstance(value, str) and value.strip() else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None

def set_model(model):
    if not model or not model.strip():
        raise ValueError('模型名称不能为空。')
    # Preserve local-path overrides as absolute paths across working directories.
    if Path(model).is_dir():
        model = str(Path(model).resolve())
    atomic_json(SETTINGS_FILE, {'model': model})
    return model

def configure_model(interactive=False):
    current = saved_model()
    if current:
        return current
    if not interactive:
        return None
    choice = 'small'
    if interactive:
        print(f'模型和缓存将保存到 {DATA_DIR}；如需换盘，可让 Agent 在选择模型时同时指定目录。', file=sys.stderr, flush=True)
        print('选择本地转写模型（只问一次，模型在需要转写时下载）：\n'
              '1. small：轻量、较快（默认）\n'
              '2. large-v3-turbo：速度与质量折中\n'
              '3. large-v3：质量优先，CPU 上较慢\n'
              '请输入 1/2/3 或模型名，回车选择 small：', file=sys.stderr, flush=True)
        try:
            answer = input().strip()
        except EOFError:
            return None
        choice = dict(zip(('1', '2', '3'), RECOMMENDED_MODELS)).get(answer, answer or 'small')
        if choice not in RECOMMENDED_MODELS:
            raise ValueError('模型选择无效，未保存；请使用 1/2/3 或所列模型名。')
    return set_model(choice)

def resolve_model(model=None):
    name = model or saved_model() or 'small'
    local = MODELS_DIR / ('faster-whisper-' + name)
    if name in RECOMMENDED_MODELS and (local / 'model.bin').is_file() and (local / 'config.json').is_file():
        return name, str(local)
    return name, str(Path(name).resolve()) if Path(name).is_dir() else name
