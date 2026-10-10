"""Lightweight setup; optional speech dependencies only with --transcribe."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
MOSS_MODEL = 'moss-0.9b'
MODELS = ('small', 'large-v3-turbo', 'large-v3', MOSS_MODEL)


def atomic_json(path, value):
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def dependencies_available(python, root, environment, transcribe=False):
    """Read-only probe: minimum versions, actual imports, then dependency consistency."""
    probe = """
import sys
if sys.version_info < (3, 10):
    raise RuntimeError('Python 3.10+ required')
from importlib.metadata import version
try:
    from packaging.version import Version
except ImportError:
    from pip._vendor.packaging.version import Version
import yt_dlp
assert Version(version('yt-dlp')) >= Version('2026.8.19')
"""
    if transcribe == 'moss':
        probe += "\nimport torch, transformers, moss_transcribe_diarize\nassert Version(version('moss-transcribe-diarize')) >= Version('0.1.0')\nassert Version(version('transformers')) >= Version('5.6.0')\n"
    elif transcribe:
        probe += "\nimport faster_whisper\nassert Version(version('faster-whisper')) >= Version('1.2.1')\n"
    try:
        for arguments in ([str(python), '-c', probe], [str(python), '-m', 'pip', 'check']):
            result = subprocess.run(arguments, cwd=root, env=environment,
                                    capture_output=True, timeout=60, check=False)
            if result.returncode != 0:
                return False
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def runtime_candidates(root, managed_python):
    candidates = []
    try:
        saved = json.loads((root / 'runtime.json').read_text(encoding='utf-8')).get('python')
        if isinstance(saved, str) and Path(saved).is_absolute():
            candidates.append(Path(saved))
    except (OSError, ValueError, AttributeError):
        pass
    candidates.extend([managed_python, Path(sys.executable)])
    seen = set()
    for python in candidates:
        python = python.resolve()
        if python.is_file() and python not in seen:
            seen.add(python)
            yield python


def install(root, model=None, transcribe=False):
    root = Path(root).resolve()
    if sys.platform != 'win32':
        raise RuntimeError('此安装入口目前支持 Windows + Chrome。')
    if sys.version_info < (3, 10):
        raise RuntimeError('需要 Python 3.10 或以上，请用户自行安装；本项目不下载或安装 Python。')
    for item in ('SKILL.md', 'pyproject.toml', 'video_notes', 'extension', 'rules/notes-generation.md', 'video_notes/paths.py'):
        if not (root / item).exists():
            raise RuntimeError('需要完整仓库，缺少：' + item)
    # Load paths without importing optional runtime dependencies.
    import importlib.util
    spec = importlib.util.spec_from_file_location('install_paths', root / 'video_notes/paths.py')
    paths = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(paths)
    data = paths.data_directory(root)
    runtime_python = root / '.venv/Scripts/python.exe'
    import tempfile
    with tempfile.TemporaryDirectory(prefix='video-notes-install-') as temporary:
        return _install_runtime(root, data, runtime_python, model, transcribe, temporary)


def _install_runtime(root, data, runtime_python, model, transcribe, temporary):
    if transcribe and not model:
        try:
            model = json.loads((data / 'settings.json').read_text(encoding='utf-8'))['model']
        except (OSError, ValueError, KeyError, TypeError):
            raise RuntimeError('首次转写请由 Agent 询问模型，再传入 --transcribe --model。') from None
    environment = dict(os.environ, TEMP=temporary, TMP=temporary,
                       PIP_NO_INPUT='1', PIP_NO_CACHE_DIR='1',
                       PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    def run(arguments):
        subprocess.run([str(x) for x in arguments], cwd=root, env=environment, check=True)
    required = 'moss' if transcribe and model == MOSS_MODEL else transcribe
    selected = next((python for python in runtime_candidates(root, runtime_python)
                     if dependencies_available(python, root, environment, required)), None)
    reused = selected is not None
    if reused:
        runtime_python = selected
        print('复用依赖已满足的 Python 环境；不向该环境安装或升级软件。', flush=True)
    else:
        if not runtime_python.is_file():
            print('现有环境依赖不满足，创建项目内独立 Python 环境……', flush=True)
            run([sys.executable, '-m', 'venv', str(root / '.venv')])
        print('仅在项目独立环境中安装所需依赖……', flush=True)
        extra = '.[moss]' if required == 'moss' else '.[transcribe]' if transcribe else '.'
        run([runtime_python, '-m', 'pip', 'install', '--disable-pip-version-check', '-e', extra])
        run([runtime_python, '-m', 'pip', 'check'])
    if model:
        run([runtime_python, '-m', 'video_notes', '--set-model', model])
    print('注册本机通信……', flush=True)
    run([runtime_python, root / 'scripts/install_native_host.py'])
    imports = '; import torch, transformers, moss_transcribe_diarize' if required == 'moss' else '; import faster_whisper' if transcribe else ''
    run([runtime_python, '-c', 'import video_notes, yt_dlp' + imports])
    runtime = {'project_dir': str(root), 'python': str(runtime_python),
               'rules_file': str(root / 'rules/notes-generation.md'),
               'data_dir': str(data), 'media_dir': str(data / 'downloads')}
    atomic_json(root / 'runtime.json', runtime)
    return {'status': 'runtime_ready', 'model': model, 'runtime': runtime,
            'environment': 'reused' if reused else 'managed_venv',
            'transcription': 'ready' if transcribe else 'on_demand',
            'extension_path': str(root / 'extension'), 'browser_extension': 'manual_load_required'}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', choices=MODELS, help='Agent 收集的模型选择；已有配置时可省略')
    parser.add_argument('--transcribe', action='store_true', help='首次需要语音识别时安装可选依赖')
    args = parser.parse_args(argv)
    try:
        result = install(ROOT, args.model, args.transcribe)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as exc:
        print('运行环境安装未完成：' + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print('运行环境已安装。Chrome 扩展仍需手动加载上面的 extension_path；未验证浏览器连接。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
