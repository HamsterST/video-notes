import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


def normalize_douyin_url(url):
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.hostname not in {'douyin.com', 'www.douyin.com'}
            or parts.username or parts.password or parts.port not in (None, 443)):
        return url
    match = re.fullmatch(r'/video/(\d+)/?', parts.path)
    video_id = match.group(1) if match else None
    if not video_id:
        ids = [v for k, v in parse_qsl(parts.query) if k == 'modal_id']
        if len(ids) == 1 and re.fullmatch(r'\d+', ids[0]):
            video_id = ids[0]
    return f'https://www.douyin.com/video/{video_id}' if video_id else url

def canonical_media_url(url):
    url = normalize_douyin_url(url)
    parts = urlsplit(url)
    # Only strip known Bilibili tracking parameters; preserve p and unknown keys.
    host = (parts.hostname or '').lower()
    if host == 'bilibili.com' or host.endswith('.bilibili.com'):
        ignored = {'spm_id_from', 'vd_source', 'trackid', 'share_source', 'share_medium'}
        query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in ignored))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ''))
    return url

