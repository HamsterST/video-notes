"""Small Douyin media adapter. Signed CDN URLs stay in memory only."""
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from .urls import normalize_douyin_url

FEED_URL = 'https://api5-normal-c-hl.amemv.com/aweme/v1/feed/'
FEED_USER_AGENT = 'Aweme 35.0.0 rv:350000 (iPhone; iOS 17.0; zh_CN)'
MEDIA_USER_AGENT = 'Mozilla/5.0'
MAX_FEED_BYTES = 8 * 1024 * 1024
MAX_MEDIA_BYTES = 2 * 1024 * 1024 * 1024
SHORT_LINK_HOST = 'v.douyin.com'
REDIRECT_HOSTS = {'v.douyin.com', 'douyin.com', 'www.douyin.com', 'www.iesdouyin.com'}


class DouyinLookupError(Exception):
    pass


class DouyinShareError(ValueError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _share_target(url):
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.hostname not in REDIRECT_HOSTS
            or parts.username or parts.password or parts.port not in (None, 443)):
        raise DouyinShareError('抖音分享短链跳转到了不支持的网站。')
    if parts.hostname in {'douyin.com', 'www.douyin.com'}:
        normalized = normalize_douyin_url(url)
        if video_id_for(normalized):
            return normalized
    if parts.hostname in {'douyin.com', 'www.douyin.com', 'www.iesdouyin.com'}:
        match = re.fullmatch(r'/share/video/(\d+)/?', parts.path)
        if match:
            return f'https://www.douyin.com/video/{match.group(1)}'
        if re.fullmatch(r'/share/note/\d+/?', parts.path):
            raise DouyinShareError('这条分享链接指向抖音图文作品，不是可转写的视频。')
    return None


def resolve_douyin_share_url(url):
    """Resolve only the official short-link host to a canonical single-video URL."""
    if not url.startswith('https://'):
        links = re.findall(r'https://v\.douyin\.com/[A-Za-z0-9_-]{1,128}/?(?:\?[^\s]*)?', url)
        if len(links) == 1:
            url = links[0]
    parts = urlsplit(url)
    if parts.hostname != SHORT_LINK_HOST:
        if parts.hostname == 'www.iesdouyin.com':
            return _share_target(url) or url
        return normalize_douyin_url(url)
    if (parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443)
            or not re.fullmatch(r'/[A-Za-z0-9_-]{1,128}/?', parts.path)):
        raise DouyinShareError('抖音分享短链格式无效。')
    opener = build_opener(_NoRedirect())
    current = url
    for _ in range(6):
        target = _share_target(current)
        if target:
            return target
        location = None
        for method in ('HEAD', 'GET'):
            request = Request(current, headers={'User-Agent': MEDIA_USER_AGENT}, method=method)
            try:
                with opener.open(request, timeout=15) as response:
                    status = response.status
                    location = response.headers.get('Location')
            except HTTPError as exc:
                status = exc.code
                location = exc.headers.get('Location')
            except (OSError, URLError) as exc:
                raise DouyinShareError('抖音分享短链访问失败。') from exc
            if method == 'HEAD' and status in {400, 403, 405, 501}:
                continue
            break
        if status not in {301, 302, 303, 307, 308} or not location:
            raise DouyinShareError('抖音分享短链未跳转到单个视频。')
        current = urljoin(current, location)
        _share_target(current)  # Reject untrusted redirect hosts before another request.
    raise DouyinShareError('抖音分享短链跳转次数过多。')


def video_id_for(url):
    parts = urlsplit(normalize_douyin_url(url))
    if (parts.scheme != 'https' or parts.hostname not in {'douyin.com', 'www.douyin.com'}
            or parts.username or parts.password or parts.port not in (None, 443)):
        return None
    match = re.fullmatch(r'/video/(\d+)/?', parts.path)
    return match.group(1) if match else None


def _safe_media_url(url):
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    host = parts.hostname or ''
    return (parts.scheme == 'https' and not parts.username and not parts.password
            and parts.port in (None, 443) and
            (host.endswith('.douyinvod.com') or host.endswith('.amemv.com')))


def _caption_tracks(item):
    video = item.get('video') or {}
    cla = video.get('cla_info') or video.get('claInfo') or {}
    captions = (cla.get('caption_infos') or cla.get('captionInfos') or
                video.get('subtitle_infos') or video.get('subtitleInfos') or [])
    subtitles, automatic = {}, {}
    for caption in captions if isinstance(captions, list) else []:
        if not isinstance(caption, dict):
            continue
        urls = caption.get('url_list') or caption.get('urlList') or []
        url = caption.get('url') or caption.get('Url') or (urls[0] if urls else None)
        fmt = str(caption.get('format') or caption.get('Format') or '').lower()
        ext = {'webvtt': 'vtt', 'vtt': 'vtt', 'srt': 'srt'}.get(fmt)
        if not ext and isinstance(url, str):
            ext = {'vtt': 'vtt', 'srt': 'srt'}.get(urlsplit(url).path.rsplit('.', 1)[-1].lower())
        if not url or not ext:
            continue
        language = caption.get('language_code') or caption.get('LanguageCodeName') or caption.get('lang') or 'zh'
        target = automatic if caption.get('is_auto_generated') else subtitles
        target.setdefault(language, []).append({'url': url, 'ext': ext})
    return subtitles, automatic


def fetch_video_info(url):
    video_id = video_id_for(url)
    if not video_id:
        raise DouyinLookupError('不是受支持的抖音单视频链接。')
    query = urlencode({'aweme_id': video_id, 'aid': 1128})
    item = None
    last_error = None
    for attempt in range(2):
        try:
            with urlopen(Request(FEED_URL + '?' + query, headers={'User-Agent': FEED_USER_AGENT}), timeout=20) as response:
                if response.status != 200:
                    raise DouyinLookupError('抖音视频数据请求失败。')
                raw = response.read(MAX_FEED_BYTES + 1)
            if len(raw) > MAX_FEED_BYTES:
                raise DouyinLookupError('抖音视频数据过大。')
            payload = json.loads(raw)
            item = next((x for x in payload.get('aweme_list', [])
                         if isinstance(x, dict) and str(x.get('aweme_id')) == video_id), None)
            if item:
                break
            last_error = DouyinLookupError('抖音未返回目标视频。')
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            last_error = DouyinLookupError('抖音视频数据请求或解析失败。')
        except DouyinLookupError as exc:
            last_error = exc
        if attempt == 0:
            time.sleep(0.3)
    if not item:
        raise last_error or DouyinLookupError('抖音未返回目标视频。')
    try:
        video = item.get('video') or {}
        address = video.get('play_addr') or {}
        urls = [x for x in address.get('url_list', []) if _safe_media_url(x)]
        if not urls:
            raise DouyinLookupError('抖音未返回可用的原声视频地址。')
        duration = video.get('duration')
        subtitles, automatic = _caption_tracks(item)
        return {'id': video_id, 'title': item.get('desc') or video_id,
                'duration': duration / 1000 if isinstance(duration, (int, float)) and duration > 0 else None,
                'extractor': 'DouyinFeed', 'subtitles': subtitles, 'automatic_captions': automatic,
                '_douyin_media_urls': urls, '_douyin_media_size': address.get('data_size'),
                'http_headers': {'Referer': 'https://www.douyin.com/', 'User-Agent': MEDIA_USER_AGENT}}
    except DouyinLookupError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise DouyinLookupError('抖音视频数据请求或解析失败。') from exc


def download_video(info, directory, progress):
    """Download the original-sound MP4; do not use music.play_url as speech."""
    video_id = info.get('id')
    if not isinstance(video_id, str) or not video_id.isdigit():
        raise DouyinLookupError('抖音视频 ID 无效。')
    target = directory / f'{video_id}.mp4'
    temporary = directory / f'{video_id}.mp4.part'
    expected = info.get('_douyin_media_size')
    for url in info.get('_douyin_media_urls') or []:
        if not _safe_media_url(url):
            continue
        try:
            headers = {'Referer': 'https://www.douyin.com/', 'User-Agent': MEDIA_USER_AGENT}
            with urlopen(Request(url, headers=headers), timeout=30) as response, temporary.open('wb') as output:
                if response.status != 200 or not _safe_media_url(response.url):
                    raise DouyinLookupError('媒体地址不可用。')
                head = response.read(32)
                if len(head) < 8 or head[4:8] != b'ftyp':
                    raise DouyinLookupError('媒体响应不是 MP4。')
                output.write(head)
                size = len(head)
                last_report = time.monotonic()
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_MEDIA_BYTES:
                        raise DouyinLookupError('媒体文件超过大小限制。')
                    output.write(chunk)
                    now = time.monotonic()
                    if now - last_report >= 2:
                        progress(f'下载进度：{size / expected:.0%}' if isinstance(expected, int) and expected > 0 else f'已下载 {size / 1048576:.1f} MB')
                        last_report = now
            if isinstance(expected, int) and expected > 0 and size != expected:
                raise DouyinLookupError('媒体下载不完整。')
            os.replace(temporary, target)
            progress('媒体下载完成，正在检查文件。')
            return target
        except (OSError, ValueError, DouyinLookupError):
            temporary.unlink(missing_ok=True)
    raise DouyinLookupError('抖音媒体下载失败。')
