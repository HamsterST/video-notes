from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

def canonical_media_url(url):
    parts = urlsplit(url)
    # Only strip known Bilibili tracking parameters; preserve p and unknown keys.
    host = (parts.hostname or '').lower()
    if host == 'bilibili.com' or host.endswith('.bilibili.com'):
        ignored = {'spm_id_from', 'vd_source', 'trackid', 'share_source', 'share_medium'}
        query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in ignored))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ''))
    return url

