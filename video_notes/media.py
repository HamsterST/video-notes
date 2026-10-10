import hashlib
import json
import os
from pathlib import Path
import time
import sys
from .urls import canonical_media_url
from . import cache
from .settings import resolve_model, saved_model, configure_model, RECOMMENDED_MODELS, MOSS_MODEL
from importlib.metadata import version, PackageNotFoundError

import yt_dlp
from yt_dlp.cookies import load_cookies

from .core import QuietLogger, check_coverage
from .paths import DATA_DIR, DOWNLOAD_DIR, MODELS_DIR
from .douyin import video_id_for, fetch_video_info, download_video



def download_progress(reporter):
    last_report = [0.0]
    def hook(event):
        if event.get('status') == 'finished':
            reporter('媒体下载完成，正在检查文件。')
        elif event.get('status') == 'downloading':
            now = time.monotonic()
            if now - last_report[0] < 2:
                return
            last_report[0] = now
            count = event.get('downloaded_bytes') or 0
            total = event.get('total_bytes') or event.get('total_bytes_estimate')
            reporter(f'下载进度：{count / total:.0%}' if total else f'已下载 {count / 1048576:.1f} MB')
    return hook


def download_and_transcribe(url, **kwargs):
    from .storage import media_lock, cleanup_media
    if kwargs.get('local_video'):
        return _download_and_transcribe(url, **kwargs)
    base = kwargs.get('download_dir', DOWNLOAD_DIR)
    with media_lock(base):
        try:
            return _download_and_transcribe(url, **kwargs)
        finally:
            if not kwargs.get('cache_only'):
                try:
                    cleanup_media(base)
                except OSError:
                    kwargs.get('progress', lambda message: None)('媒体缓存清理未完成，将在下次处理时重试。')


def recognition_recipe(model, device, beam_size, cpu_threads):
    name, selected = resolve_model(model)
    if name == MOSS_MODEL:
        from .moss_backend import MOSS_REVISION
        try:
            engine = version('moss-transcribe-diarize')
        except PackageNotFoundError:
            engine = 'unavailable'
        return {'model': {'name': name, 'repository': selected, 'revision': MOSS_REVISION},
                'engine': 'moss-transcribe-diarize', 'engine_version': engine, 'device': device,
                'compute_type': 'float32' if device == 'cpu' else 'auto-fp16-bf16',
                'cpu_threads': cpu_threads, 'speaker_diarization': True}
    signature = {'name': name, 'path': selected}
    path = Path(selected)
    if path.is_dir():
        signature['files'] = [(str(p.relative_to(path)), p.stat().st_size, p.stat().st_mtime_ns)
                              for p in sorted(path.rglob('*')) if p.is_file()]
    try:
        engine = version('faster-whisper')
    except PackageNotFoundError:
        engine = 'unavailable'
    return {'model': signature, 'engine_version': engine, 'device': device,
            'compute_type': 'int8' if device == 'cpu' else 'float16', 'beam_size': beam_size,
            'cpu_threads': cpu_threads, 'vad_filter': True}


def reuse_result(result, output_language, gap_seconds, gap_ratio):
    result['output_language'] = output_language
    old = result.get('coverage', {})
    result['warnings'] = [w for w in result.get('warnings', []) if w not in old.get('warnings', [])]
    result['coverage'] = check_coverage(old.get('video_duration'), result['segments'], gap_seconds, gap_ratio)
    result['warnings'] += result['coverage']['warnings']
    return result


def _download_and_transcribe(url, *, cookies_from_browser=None, browser_profile=None, cookie_file=None, cookie_jar=None,
                           output_language='zh', progress=lambda message: None,
                           download_dir=DOWNLOAD_DIR, model=None, model_dir=None,
                           device='cuda', local_video=None, gap_seconds=120, gap_ratio=0.1,
                           beam_size=1, cpu_threads=None, force=False, cache_only=False, media_info=None):
    if beam_size < 1 or (cpu_threads is not None and cpu_threads < 1):
        raise ValueError('beam-size 和 cpu-threads 必须为正整数。')
    result = dict(status='download_failed', segments=[], warnings=[], notes_generated=False,
                  output_language=output_language, source='speech_recognition')
    selected_choice = model or saved_model()
    index_key = None
    if selected_choice:
        recipe = recognition_recipe(selected_choice, device, beam_size, cpu_threads)
        index_key = {'schema': 1, 'url': canonical_media_url(url), **recipe}
        if not force and not local_video:
            indexed = cache.read('transcript-index', index_key)
            if indexed:
                relative = indexed.get('cached_media_relative')
                candidate = (Path(download_dir).resolve() / relative).resolve() if relative else None
                base = Path(download_dir).resolve()
                if candidate and candidate.is_relative_to(base) and (not candidate.exists() or cache.file_identity(candidate) == indexed.get('cached_audio_identity')):
                    indexed['media_path'] = str(candidate) if candidate.is_file() else None
                    indexed['media_available'] = candidate.is_file()
                    marker = candidate.parent / '.videonotes-media'
                    if marker.is_file():
                        marker.touch()
                    progress('复用完整转写结果；无需下载媒体或加载模型。')
                    return reuse_result(indexed, output_language, gap_seconds, gap_ratio)
    if not selected_choice:
        if cache_only:
            return None
        selected_choice = configure_model(interactive=sys.stdin.isatty())
        if not selected_choice:
            result.update(status='model_selection_required', next_action='select_model',
                          models=list(RECOMMENDED_MODELS),
                          data_dir=str(DATA_DIR),
                          storage_notice=f'模型和缓存将保存到 {DATA_DIR}；如需换盘，可在选择模型时同时指定目录。',
                          message=f'模型和缓存将保存到 {DATA_DIR}；如需换盘，可在选择模型时同时指定目录。首次转写需要选择本地模型。请让 Agent 选择模型后运行 scripts/bootstrap.py --transcribe --model <模型>，再重试；尚未下载媒体。')
            return result
    model = selected_choice
    if model == MOSS_MODEL and beam_size != 1:
        raise ValueError('MOSS 不使用 --beam-size；请使用默认值 1。')
    if not cache_only:
        try:
            if model == MOSS_MODEL:
                import torch
                import transformers
                import moss_transcribe_diarize
            else:
                import faster_whisper
        except ImportError:
            result.update(status='dependency_missing', next_action='install_transcription',
                          message='首次转写尚未安装语音依赖。请让 Agent 运行 scripts/bootstrap.py --transcribe --model ' + model + '，然后重试；尚未下载媒体。')
            return result
    if local_video:
        video = Path(local_video).expanduser().resolve()
        if not video.is_file():
            if cache_only:
                return None
            result.update(status='download_failed', message='指定的本地视频不存在。')
            return result
    else:
        base = Path(download_dir).expanduser().resolve()
        directory = base / hashlib.sha256(canonical_media_url(url).encode()).hexdigest()[:16]
        legacy = base / hashlib.sha256(url.encode()).hexdigest()[:16]
        if not directory.exists() and (legacy / 'media.json').is_file():
            directory = legacy
        manifest = directory / 'media.json'
        video = None
        if manifest.exists():
            try:
                saved = json.loads(manifest.read_text(encoding='utf-8'))
                candidate = (directory / saved['file']).resolve()
                if candidate.parent == directory and candidate.is_file() and candidate.stat().st_size == saved['size']:
                    video = candidate
                    (directory / '.videonotes-media').touch()
                    progress('复用已下载的音视频文件。')
            except (ValueError, KeyError, OSError):
                pass
        if video is None:
            if cache_only:
                return None
            directory.mkdir(parents=True, exist_ok=True)
            (directory / '.videonotes-media').touch()
            if video_id_for(url):
                try:
                    info = media_info if isinstance(media_info, dict) and media_info.get('id') == video_id_for(url) and media_info.get('_douyin_media_urls') else fetch_video_info(url)
                    progress('正在下载抖音原声视频；没有独立原声音轨。')
                    video = download_video(info, directory, progress)
                    manifest.write_text(json.dumps({'file': video.name, 'size': video.stat().st_size}), encoding='utf-8')
                except Exception:
                    result['message'] = '抖音媒体下载失败，请检查网络与视频访问状态；未执行转写。'
                    return result
            else:
                options = dict(noplaylist=True, cachedir=False, quiet=True, logger=QuietLogger(),
                           socket_timeout=20, retries=2, outtmpl=str(directory / '%(id)s.%(ext)s'),
                           format='bestaudio/best', windowsfilenames=True,
                           progress_hooks=[download_progress(progress)])
                final_paths = []
                options['postprocessor_hooks'] = [lambda event: final_paths.append(event['info_dict'].get('filepath')) if event.get('status') == 'finished' else None]
                try:
                    with yt_dlp.YoutubeDL(options) as ydl:
                        if cookie_jar is not None or cookie_file or cookies_from_browser:
                            try:
                                ydl.cookiejar = cookie_jar if cookie_jar is not None else load_cookies(cookie_file, None if cookie_file else (cookies_from_browser, browser_profile), ydl)
                            except Exception:
                                result['warnings'].append('下载时无法读取 Cookie，尝试匿名下载。')
                        if media_info is None:
                            progress('正在解析媒体地址；网络请求可能需要等待。')
                            info = ydl.extract_info(url, download=False)
                        else:
                            progress('复用字幕查询时取得的媒体地址。')
                            info = media_info
                        if not info or info.get('_type') in {'playlist', 'multi_video'}:
                            result.update(status='unsupported_input', message='请提供单个视频链接。')
                            return result
                        if info.get('vcodec') == 'none':
                            progress('正在下载独立音轨，不下载视频画面。')
                        else:
                            progress('未选到独立音轨，将下载包含音频的媒体文件。')
                        ydl.process_info(info)
                        paths = list(reversed([p for p in final_paths if p])) + [info.get('filepath'), ydl.prepare_filename(info)]
                        video = next((Path(p).resolve() for p in paths if p and Path(p).is_file() and Path(p).suffix not in {'.part', '.ytdl'}), None)
                        if video is None:
                            raise ValueError('No completed media file')
                        if video.parent != directory:
                            raise ValueError('Unexpected media path')
                        manifest.write_text(json.dumps({'file': video.name, 'size': video.stat().st_size}), encoding='utf-8')
                except Exception:
                    result['message'] = '音频或媒体下载失败，请检查网络与访问权限；未执行转写。'
                    return result
    model_cache = Path(model_dir).expanduser().resolve() if model_dir else MODELS_DIR / ('moss' if model == MOSS_MODEL else 'faster-whisper')
    recipe = recognition_recipe(model, device, beam_size, cpu_threads)
    model_name, selected_model = resolve_model(model)
    engine_version = recipe['engine_version']
    identity = {'schema': 1, 'url': canonical_media_url(url), 'audio': cache.file_identity(video), **recipe}
    index_key = {'schema': 1, 'url': canonical_media_url(url), **recipe}
    def index_result(value):
        if not local_video:
            value['cached_media_relative'] = str(video.relative_to(Path(download_dir).resolve()))
            value['cached_audio_identity'] = identity['audio']
            cache.write('transcript-index', index_key, value)
    cached = cache.read('transcripts', identity) if not force else None
    if cached:
        cached['output_language'] = output_language
        cached['media_path'] = str(video)
        index_result(cached)
        progress('复用完整转写结果，跳过模型加载和识别。')
        return reuse_result(cached, output_language, gap_seconds, gap_ratio)
    if cache_only:
        return None
    if model == MOSS_MODEL:
        try:
            from .moss_backend import MossInputError, transcribe as transcribe_moss
            moss = transcribe_moss(video, model_cache=model_cache, device=device,
                                  cpu_threads=cpu_threads, progress=progress)
            rows = moss['segments']
            if not rows:
                result.update(status='no_speech', message='MOSS 转写完成，但未识别到讲话内容。')
                return result
            coverage = check_coverage(moss['duration'], rows, gap_seconds, gap_ratio)
            speakers = sorted({row['speaker'] for row in rows})
            result.update(status='ok', segments=rows, coverage=coverage, media_path=str(video),
                          performance=moss['performance'],
                          selected_subtitle={'language': 'und', 'source': 'speech_recognition'},
                          speakers=speakers,
                          message='MOSS 转写及说话人区分完成；当前是原语言文字，尚未翻译或生成笔记。')
            result['provenance'] = {'url': canonical_media_url(url), 'source': 'speech_recognition',
                                    'engine': 'moss-transcribe-diarize', 'engine_version': engine_version,
                                    'model': model_name, 'model_revision': recipe['model']['revision'],
                                    'device': device, 'compute_type': moss['compute_type'],
                                    'speaker_diarization': True, 'speaker_labels': 'anonymous',
                                    'max_new_tokens': moss['performance']['max_new_tokens'],
                                    'language': 'und', 'completed_at': cache.utc_now()}
            result['cache'] = {'reused': False, 'kind': 'transcripts'}
            result['warnings'] += coverage['warnings'] + [
                '自动语音识别可能存在人名、术语和内容误差；S01 等标签仅区分声音，不代表真实身份。'
            ]
        except MossInputError as exc:
            result.update(status='transcription_failed', media_path=str(video), message=str(exc))
        except Exception:
            result.update(status='transcription_failed', media_path=str(video),
                          message='MOSS 模型加载或转写失败，媒体文件已保留。请检查依赖、模型下载、内存和设备；可改用 Whisper 模型重试。')
        if result['status'] == 'ok':
            cache.write('transcripts', identity, result)
            index_result(result)
        return result
    progress('正在加载语音转写依赖，请稍候。')
    try:
        if device == 'cuda':
            from .gpu import prepare_cuda_libraries
            prepare_cuda_libraries()
        from faster_whisper import WhisperModel
    except ImportError:
        result.update(status='dependency_missing', message='请先安装转写依赖：python -m pip install "faster-whisper>=1.2.1"')
        return result
    try:
        threads = cpu_threads or min(8, os.cpu_count() or 4)
        progress(f'加载模型 {model_name}：设备 {device}，CPU 线程 {threads}，beam-size {beam_size}。')
        loading_started = time.perf_counter()
        model_cache.mkdir(parents=True, exist_ok=True)
        recognizer = WhisperModel(selected_model, device=device, compute_type='int8' if device == 'cpu' else 'float16',
                                  cpu_threads=threads, download_root=str(model_cache))
        progress(f'模型加载完成，耗时 {time.perf_counter()-loading_started:.1f} 秒；开始解码音频和检测语言。')
        transcribe_started = time.perf_counter()
        segments, metadata = recognizer.transcribe(str(video), beam_size=beam_size, vad_filter=True)
        progress(f'开始转写，检测语言：{metadata.language}，时长约 {metadata.duration:.0f} 秒。')
        rows = []
        last_report = time.perf_counter()
        for i, segment in enumerate(segments, 1):
            if segment.text.strip():
                rows.append(dict(start=segment.start, end=segment.end, text=segment.text.strip()))
            now = time.perf_counter()
            if i == 1 or now-last_report >= 5:
                elapsed = now-transcribe_started
                speed = segment.end/max(elapsed, 0.001)
                remaining = max(0, metadata.duration-segment.end)/max(speed, 0.001)
                progress(f'已处理音频 {segment.end:.0f}/{metadata.duration:.0f} 秒；实际耗时 {elapsed:.1f} 秒，约 {speed:.1f} 倍速，预计剩余 {remaining:.0f} 秒。')
                last_report = now
        if not rows:
            result.update(status='no_speech', message='转写完成，但未识别到讲话内容。')
            return result
        coverage = check_coverage(metadata.duration, rows, gap_seconds, gap_ratio)
        elapsed = time.perf_counter()-transcribe_started
        progress(f'转写完成：{metadata.duration:.0f} 秒音频，实际耗时 {elapsed:.1f} 秒，平均 {metadata.duration/max(elapsed, 0.001):.1f} 倍速。')
        result.update(status='ok', segments=rows, coverage=coverage, media_path=str(video),
                      performance={'device': device, 'cpu_threads': threads, 'beam_size': beam_size,
                                   'transcription_seconds': round(time.perf_counter()-transcribe_started, 2)},
                      selected_subtitle={'language': metadata.language, 'source': 'speech_recognition'},
                      message='语音转写完成；当前是原语言文字，尚未翻译或生成笔记。')
        result['provenance'] = {'url': canonical_media_url(url), 'source': 'speech_recognition',
                                'engine': 'faster-whisper', 'engine_version': engine_version,
                                'model': model_name, 'device': device, 'beam_size': beam_size,
                                'compute_type': identity['compute_type'], 'vad_filter': True,
                                'language': metadata.language, 'completed_at': cache.utc_now()}
        result['cache'] = {'reused': False, 'kind': 'transcripts'}
        result['warnings'] += coverage['warnings'] + ['自动语音识别可能存在人名、术语和内容误差。']
    except Exception:
        result.update(status='transcription_failed', media_path=str(video),
                      message='模型加载或转写失败，音视频文件已保留。请检查模型路径、网络和设备依赖；默认 CUDA 需要 NVIDIA GPU、CUDA 12 cuBLAS 与 cuDNN 9，可显式使用 --device cpu 重试。')
    if result['status'] == 'ok':
        cache.write('transcripts', identity, result)
        index_result(result)
    return result
