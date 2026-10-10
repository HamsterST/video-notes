"""Make CUDA runtime DLLs from an optional local PyTorch install discoverable."""
import importlib.util
import os
from pathlib import Path

_DLL_HANDLES = []


def prepare_cuda_libraries():
    if os.name != 'nt':
        return
    # Inspect package metadata without importing torch or adding it as a Whisper dependency.
    spec = importlib.util.find_spec('torch')
    if spec is None or spec.origin is None:
        return
    library_dir = Path(spec.origin).parent / 'lib'
    if not (library_dir / 'cublas64_12.dll').is_file():
        return
    path = str(library_dir)
    entries = os.environ.get('PATH', '').split(os.pathsep)
    if path not in entries:
        os.environ['PATH'] = os.pathsep.join([path, *entries])
        if hasattr(os, 'add_dll_directory'):
            _DLL_HANDLES.append(os.add_dll_directory(path))
