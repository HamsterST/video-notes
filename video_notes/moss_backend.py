"""Optional MOSS transcription backend; never imported for subtitle-only work."""
import math
import os
from pathlib import Path
import time

from .settings import MOSS_REPOSITORY

MOSS_REVISION = '704aa4a9c304e8520be88901e0d1960158ef5b15'
MAX_AUDIO_SECONDS = 90 * 60


class MossInputError(ValueError):
    """A user-actionable input or device limitation."""


def media_duration(path):
    import av

    with av.open(str(path)) as container:
        if container.duration is not None and container.duration > 0:
            return container.duration / av.time_base
        stream = next((item for item in container.streams if item.type == 'audio'), None)
        if stream and stream.duration is not None and stream.time_base is not None:
            return float(stream.duration * stream.time_base)
    return None


def transcribe(video, *, model_cache, device='cuda', cpu_threads=None, progress=lambda message: None):
    import torch
    from transformers import AutoModelForCausalLM, AutoProcessor
    from moss_transcribe_diarize import parse_transcript
    from moss_transcribe_diarize.inference_utils import build_transcription_messages, generate_transcription

    video = Path(video)
    duration = media_duration(video)
    if duration and duration > MAX_AUDIO_SECONDS:
        raise MossInputError('MOSS 官方支持最长 90 分钟音频；本视频超过该范围。')
    if device == 'cuda' and not torch.cuda.is_available():
        raise MossInputError('未检测到可用的 CUDA 设备。请配置 CUDA 版 PyTorch，或使用 --device cpu。')
    threads = cpu_threads or min(8, os.cpu_count() or 4)
    if device == 'cpu':
        torch.set_num_threads(threads)
    torch_device = torch.device(device)
    dtype = (torch.float32 if device == 'cpu' else
             torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16)
    cache_dir = Path(model_cache)
    cache_dir.mkdir(parents=True, exist_ok=True)
    progress(f'正在加载 MOSS 0.9B：设备 {device}，模型文件约 1.83 GB；首次使用将下载。')
    started = time.perf_counter()
    model = AutoModelForCausalLM.from_pretrained(
        MOSS_REPOSITORY, revision=MOSS_REVISION, trust_remote_code=True,
        cache_dir=str(cache_dir), dtype='auto', attn_implementation='sdpa',
    ).to(dtype=dtype).to(torch_device).eval()
    processor = AutoProcessor.from_pretrained(
        MOSS_REPOSITORY, revision=MOSS_REVISION, trust_remote_code=True, cache_dir=str(cache_dir),
    )
    loading_seconds = time.perf_counter() - started
    progress(f'MOSS 模型加载完成，耗时 {loading_seconds:.1f} 秒；开始转写与说话人区分。')
    max_new_tokens = min(65536, max(4096, math.ceil(duration * 16))) if duration else 8192
    last_report = time.perf_counter()

    def report_tokens(count):
        nonlocal last_report
        now = time.perf_counter()
        if now - last_report >= 5:
            progress(f'MOSS 已生成 {count} 个文本 token。')
            last_report = now

    transcription_started = time.perf_counter()
    output = generate_transcription(
        model, processor, build_transcription_messages(video),
        max_new_tokens=max_new_tokens, do_sample=False, device=torch_device, dtype=dtype,
        token_callback=report_tokens,
    )
    if output.get('generated_tokens', 0) >= max_new_tokens:
        raise MossInputError('MOSS 输出达到长度上限，转写可能不完整；请改用其他模型。')
    rows = []
    for segment in parse_transcript(output['text']):
        start, end = float(segment.start), float(segment.end)
        if (segment.text.strip() and math.isfinite(start) and math.isfinite(end)
                and 0 <= start <= end):
            rows.append({'start': start, 'end': end, 'speaker': str(segment.speaker),
                         'text': segment.text.strip()})
    elapsed = time.perf_counter() - transcription_started
    progress(f'MOSS 转写完成：{len(rows)} 段，耗时 {elapsed:.1f} 秒。')
    return {'segments': rows, 'duration': duration, 'compute_type': str(dtype).removeprefix('torch.'),
            'performance': {'device': device, 'cpu_threads': threads, 'loading_seconds': round(loading_seconds, 2),
                            'transcription_seconds': round(elapsed, 2), 'max_new_tokens': max_new_tokens}}
