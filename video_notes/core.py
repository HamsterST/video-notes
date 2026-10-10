import math
import json
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit

import yt_dlp
from yt_dlp.networking import Request
from yt_dlp.cookies import load_cookies

from .parsers import parse_subtitles
from .diagnostics import check_login, cookie_failure_reason
from . import cache
from .urls import canonical_media_url
from .douyin import DouyinLookupError, fetch_video_info, video_id_for

FORMATS = ("json3", "json", "vtt", "srt", "srv1", "srv2", "srv3", "ttml")
MAX_SUBTITLE_BYTES = 20 * 1024 * 1024


class QuietLogger:
    # Never print request URLs, headers or cookie diagnostics into result JSON.
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def check_coverage(duration, segments, gap_seconds=120, gap_ratio=0.1):
    last = max(s["end"] for s in segments)
    result = {"video_duration": duration, "subtitle_end": last, "status": "unknown", "warnings": []}
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        result["warnings"].append("无法获取有效视频时长，未检查字幕末尾时间差。")
        return result
    gap = max(0, duration - last)
    result.update(tail_gap_seconds=gap, tail_gap_ratio=gap/duration, status="no_large_tail_gap")
    if gap > gap_seconds and gap / duration > gap_ratio:
        result["status"] = "possible_missing_tail"
        result["warnings"].append(f"视频时长 {duration:.1f} 秒，字幕截至 {last:.1f} 秒，末尾约 {gap:.1f} 秒可能未被覆盖，总结可能遗漏内容；也可能是无对白片段。")
    elif last > duration + 5:
        result["status"] = "timestamp_mismatch"
        result["warnings"].append("字幕结束时间明显超过视频时长，可能存在时间轴偏差。")
    return result


def candidates(info, preferred=None):
    tracks = []
    for field, source in (("subtitles", "platform"), ("automatic_captions", "automatic")):
        for lang, formats in (info.get(field) or {}).items():
            if lang.lower() == "danmaku":
                continue
            if preferred and not (lang.lower() == preferred.lower() or lang.lower().startswith(preferred.lower()+"-")):
                continue
            usable = [f for f in formats if f.get("ext") in FORMATS and (f.get("url") or f.get("data"))]
            usable.sort(key=lambda f: FORMATS.index(f["ext"]))
            automatic = source == "automatic" or lang.startswith("ai-")
            tracks.append({"language": lang, "source": "automatic" if automatic else source,
                           "formats": usable})
    # Platform tracks are not assumed to be manually authored or original language.
    original = info.get("language")
    tracks.sort(key=lambda t: (t["language"] != original if original else False, t["source"] == "automatic"))
    return tracks


def _get_video_subtitles(url, *, subtitle_language=None, output_language="zh", gap_seconds=120, gap_ratio=0.1,
                        cookies_from_browser=None, browser_profile=None, diagnostics=True, cookie_file=None, cookie_jar=None,
                        on_media_info=None):
    if urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).hostname:
        raise ValueError("请提供 HTTP(S) 视频链接。")
    if not math.isfinite(gap_seconds) or gap_seconds < 0 or not 0 <= gap_ratio <= 1:
        raise ValueError("时间阈值必须非负，比例阈值必须在 0 到 1 之间。")
    if cookies_from_browser not in {None, 'chrome', 'edge', 'firefox', 'brave', 'chromium', 'opera', 'vivaldi', 'safari'}:
        raise ValueError("不支持此浏览器。")
    if cookie_file or cookie_jar is not None:
        cookies_from_browser = None
    if browser_profile and not cookies_from_browser:
        raise ValueError("--browser-profile 需要配合 --cookies-from-browser 使用。")
    result = {"status": "query_failed", "output_language": output_language,
              "segments": [], "warnings": [], "notes_generated": False,
              "authentication": {"mode": "extension" if cookie_jar is not None else "file" if cookie_file else "browser" if cookies_from_browser else "anonymous", "login_verified": None}}
    if diagnostics:
        result['diagnostics'] = {'cookie_status': 'pending' if (cookie_jar is not None or cookie_file or cookies_from_browser) else 'disabled',
                                 'login': {'status': 'not_checked', 'message': '未检查平台登录状态。'}}
    options = dict(skip_download=True, noplaylist=True, writesubtitles=True, format='bestaudio/best',
                   writeautomaticsub=True, cachedir=False, quiet=True, logger=QuietLogger(),
                   socket_timeout=20, retries=1, extractor_retries=1, ignore_no_formats_error=True)
    with yt_dlp.YoutubeDL(options) as ydl:
        if cookie_jar is not None or cookie_file or cookies_from_browser:
            try:
                jar = cookie_jar if cookie_jar is not None else load_cookies(cookie_file, None if cookie_file else (cookies_from_browser, browser_profile), ydl)
            except Exception as exc:
                reason, explanation = (("cookie_file_invalid", "无法读取 Cookie 文件，请检查文件路径、读取权限及 Netscape 格式。")
                                       if cookie_file else cookie_failure_reason(exc))
                if diagnostics:
                    result['diagnostics']['cookie_status'] = 'read_failed'
                    result['diagnostics']['cookie_failure_reason'] = reason
                result.update(status="cookie_load_failed", message=explanation + ' 尚未查询字幕。')
                return result
            host = urlsplit(url).hostname.lower()
            matching = [c for c in jar if not c.is_expired() and (host == c.domain.lstrip('.').lower() or host.endswith('.' + c.domain.lstrip('.').lower()))]
            if not matching:
                if diagnostics:
                    result['diagnostics']['cookie_status'] = 'site_cookies_missing'
                result.update(status="site_cookies_missing", message=("文件中没有此网站的未过期 Cookie，请在已登录的网站页面重新导出。" if cookie_file else "没有读到此网站的有效 Cookie。请确认所选浏览器和配置目录中已登录该网站；也可能是浏览器加密限制导致读取不完整。"))
                return result
            # Keep the jar in memory; never export cookies or claim login is verified.
            ydl.cookiejar = jar
            if diagnostics:
                result['diagnostics']['cookie_status'] = 'read_ok'
                login = check_login(ydl, url)
                result['diagnostics']['login'] = login
                result['authentication']['login_verified'] = {'logged_in': True, 'not_logged_in': False}.get(login['status'])
        try:
            info = fetch_video_info(url) if video_id_for(url) else ydl.extract_info(url, download=False)
        except (yt_dlp.utils.DownloadError, DouyinLookupError):
            result["message"] = "查询失败：可能是网络、访问限制或站点解析问题；不能据此判断没有字幕。"
            return result
        if not info or info.get("_type") in {"playlist", "multi_video"}:
            result["status"] = "unsupported_input"
            result["message"] = "请提供单个视频或具体分集链接，不接受播放列表。"
            return result
        if on_media_info is not None:
            on_media_info(info)
        result["video"] = {k: info.get(k) for k in ("id", "title", "duration", "language", "extractor")}
        all_tracks = candidates(info)
        result["available_subtitles"] = [{"language": t["language"], "source": t["source"],
                                            "formats": [f["ext"] for f in t["formats"]]} for t in all_tracks]
        if not all_tracks:
            if video_id_for(url):
                message = '该视频返回的数据未提供可用的独立字幕轨；可下载原声视频并使用现有模型转写。'
            else:
                message = ("已携带 Cookie 查询，仍未获取到可用字幕。Cookie 不一定代表登录仍有效；若网页可正常使用，可尝试音频转写。"
                           if (cookie_jar is not None or cookie_file or cookies_from_browser) else "暂未获取到可用字幕。可尝试使用浏览器登录状态重试；若已登录，请添加 --cookies-from-browser chrome。")
                if result['authentication']['login_verified'] is True:
                    message = '登录状态有效，但暂未获取到可用字幕。可直接使用音频转写。'
                elif result['authentication']['login_verified'] is False:
                    message = ('文件中的登录状态无效，请登录后重新导出 Cookie，或直接使用音频转写。' if cookie_file else '当前请求未登录，且暂未获取到字幕。可在所选浏览器登录后重试，或直接使用音频转写。')
            result.update(status="no_subtitles", message=message)
            return result
        tracks = candidates(info, subtitle_language)
        if not tracks:
            result.update(status="language_unavailable", message="没有匹配指定字幕语言的轨道；请查看 available_subtitles。")
            return result
        failures = []
        for track in tracks:
            for fmt in track["formats"]:
                try:
                    if "data" in fmt:
                        text = fmt["data"]
                    else:
                        headers = {**(info.get("http_headers") or {}), **(fmt.get("http_headers") or {})}
                        with ydl.urlopen(Request(fmt["url"], headers=headers)) as response:
                            raw = response.read(MAX_SUBTITLE_BYTES + 1)
                        if len(raw) > MAX_SUBTITLE_BYTES:
                            raise ValueError("Subtitle size limit exceeded")
                        text = raw.decode("utf-8-sig")
                    segments = parse_subtitles(text, fmt["ext"])
                except Exception as exc:
                    failures.append({"language": track["language"], "format": fmt["ext"], "error": type(exc).__name__})
                    continue
                coverage = check_coverage(info.get("duration"), segments, gap_seconds, gap_ratio)
                result.update(status="ok", selected_subtitle={"language": track["language"], "source": track["source"], "format": fmt["ext"]},
                              segments=segments, coverage=coverage, warnings=coverage["warnings"],
                              message="已获取字幕；仅检查末尾时间差，未验证语义准确性或中途遗漏。目标语言供后续笔记生成使用，当前字幕未翻译。")
                if failures:
                    result["warnings"].append("部分候选字幕读取失败，已使用后续可用轨道或格式。")
                return result
        result.update(status="subtitle_fetch_failed", attempts=failures,
                      message="发现字幕轨道，但没有成功读取可解析的字幕内容。")
        return result


def subtitle_cache_key(url, subtitle_language=None):
    return {'schema': 1, 'url': canonical_media_url(url), 'language': subtitle_language}


def load_subtitle_cache(url, *, subtitle_language=None, output_language='zh', gap_seconds=120, gap_ratio=0.1, **unused):
    result = cache.read('subtitles', subtitle_cache_key(url, subtitle_language))
    if result:
        previous = result.get('coverage', {})
        result['warnings'] = [w for w in result.get('warnings', []) if w not in previous.get('warnings', [])]
        result['coverage'] = check_coverage(result.get('video', {}).get('duration'), result['segments'], gap_seconds, gap_ratio)
        result['warnings'] += result['coverage']['warnings']
        result['output_language'] = output_language
    return result


def get_video_subtitles(url, *, force=False, **options):
    cached = load_subtitle_cache(url, **options) if not force else None
    if cached:
        return cached
    result = _get_video_subtitles(url, **options)
    if cache.valid_result(result):
        track = result['selected_subtitle']
        result['provenance'] = {'url': canonical_media_url(url), 'source': track['source'],
                                'language': track['language'], 'format': track['format'],
                                'completed_at': cache.utc_now()}
        result['cache'] = {'reused': False, 'kind': 'subtitles'}
        cache.write('subtitles', subtitle_cache_key(url, options.get('subtitle_language')), result)
    return result


def import_subtitle_file(url, filename, *, subtitle_language=None, output_language='zh', gap_seconds=120, gap_ratio=0.1, **unused):
    """Explicitly associate an existing subtitle/result file with the supplied URL."""
    path = Path(filename).expanduser().resolve()
    try:
        text = path.read_text(encoding='utf-8-sig')
        payload = json.loads(text) if path.suffix.lower() == '.json' else None
        if cache.valid_result(payload):
            result = payload
            result = {k: v for k, v in result.items() if k not in ('authentication', 'diagnostics', 'subtitle_query_diagnostics')}
            provenance = result.setdefault('provenance', {})
            # Old outputs have no trustworthy model or completion timestamp.
            provenance.setdefault('source', result.get('selected_subtitle', {}).get('source', result.get('source', 'local_file')))
            provenance.setdefault('language', result.get('selected_subtitle', {}).get('language'))
        else:
            rows = parse_subtitles(text, path.suffix.lower().lstrip('.'))
            result = {'status': 'ok', 'segments': rows, 'warnings': [], 'notes_generated': False,
                      'selected_subtitle': {'source': 'local_file', 'language': subtitle_language, 'format': path.suffix.lstrip('.')},
                      'provenance': {'source': 'local_file', 'language': subtitle_language}}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError('无法读取完整字幕文件；支持 SRT、VTT、平台 JSON 或成功的 VideoNotes 结果 JSON。') from exc
    provenance = result['provenance']
    provenance.update(url=canonical_media_url(url), imported_at=cache.utc_now(), imported_from=path.name)
    provenance.setdefault('original_file_modified_at', datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat())
    result.update(output_language=output_language, cache={'reused': False, 'kind': 'imported'},
                  message='已读取用户指定的完整原文文件，未下载或重新识别。')
    # Explicit imports remain separate from model-keyed ASR caches.
    return result
