"""Interactive policy separated from subtitle extraction and media processing."""
from .core import get_video_subtitles, load_subtitle_cache, import_subtitle_file

RECOVERABLE = {'no_subtitles', 'query_failed', 'cookie_load_failed', 'site_cookies_missing', 'subtitle_fetch_failed'}


def show_diagnostics(result, progress):
    diagnostic = result.get('diagnostics')
    if diagnostic:
        labels = {'disabled': '未启用 Cookie', 'read_failed': 'Cookie 读取失败',
                  'site_cookies_missing': '未读到目标站点 Cookie', 'read_ok': '目标站点 Cookie 已读取', 'pending': '尚未读取'}
        progress('登录诊断：' + labels[diagnostic['cookie_status']])
        if diagnostic['login']['status'] != 'not_checked':
            progress('登录诊断：' + diagnostic['login']['message'])


def run_workflow(url, *, choose=None, fallback='ask', cookies_from_browser='chrome',
                 browser_profile=None, cookie_file=None, cookie_jar=None, transcriber=None, progress=lambda message: None,
                 cookie_loader=None, transcript_loader=None, force=False, subtitle_file=None, **subtitle_options):
    if fallback not in {'ask', 'transcribe', 'stop'}:
        raise ValueError('未知 fallback 策略')
    if subtitle_file:
        return import_subtitle_file(url, subtitle_file, **subtitle_options)
    if not force:
        cached = load_subtitle_cache(url, **subtitle_options)
        if cached:
            progress('复用已保存的平台字幕，跳过登录检查和字幕下载。')
            return cached
        if transcript_loader is not None and fallback != 'stop':
            cached = transcript_loader(url, output_language=subtitle_options.get('output_language', 'zh'), progress=progress)
            if cached:
                return cached
    if cookie_loader is not None:
        cookie_jar = cookie_loader(url)
    if cookie_file or cookie_jar is not None:
        cookies_from_browser = None
    query = dict(cookie_jar=cookie_jar, cookie_file=cookie_file, cookies_from_browser=cookies_from_browser, browser_profile=browser_profile, **subtitle_options)
    media_info = None
    def remember_media_info(info):
        nonlocal media_info
        media_info = info
    query['on_media_info'] = remember_media_info
    if force:
        query['force'] = True
    progress('正在使用扩展提供的登录信息查询字幕。' if cookie_jar is not None else '正在使用 Cookie 文件查询字幕。' if cookie_file else '正在使用浏览器登录信息查询字幕。' if cookies_from_browser else '正在匿名查询字幕。')
    result = get_video_subtitles(url, **query)
    show_diagnostics(result, progress)
    if result['status'] not in RECOVERABLE or fallback == 'stop':
        return result
    progress(result['message'])
    if fallback == 'ask':
        if choose is None:
            result['next_action'] = 'retry_or_transcribe'
            result['message'] += ' 非交互运行请指定 --fallback transcribe 或 --fallback stop。'
            return result
        action = choose()
        if action is None:
            result.update(status='cancelled', message='已取消，未下载媒体。')
            return result
        if action == 'retry':
            progress('重新查询字幕；如果仍未获取到，将自动下载音频并转写。')
            result = get_video_subtitles(url, **query)
            show_diagnostics(result, progress)
            if result['status'] not in RECOVERABLE:
                return result
        elif action != 'transcribe':
            raise ValueError('未知选项')
    if transcriber is None:
        from .media import download_and_transcribe
        transcriber = download_and_transcribe
    # A cookie read failure must not prevent public video download.
    browser = None if result['status'] in {'cookie_load_failed', 'site_cookies_missing'} else cookies_from_browser
    usable_file = cookie_file if result['status'] not in {'cookie_load_failed', 'site_cookies_missing'} else None
    progress('进入下载和语音转写流程。' + (' 登录信息不可用，将尝试匿名下载。' if (cookies_from_browser or cookie_file) and not (browser or usable_file) else ''))
    file_options = {'cookie_file': usable_file} if usable_file else {}
    if cookie_jar is not None and result['status'] not in {'cookie_load_failed', 'site_cookies_missing'}:
        file_options['cookie_jar'] = cookie_jar
    converted = transcriber(url, **file_options, cookies_from_browser=browser, browser_profile=browser_profile if browser else None,
                            output_language=subtitle_options.get('output_language', 'zh'), progress=progress,
                            media_info=media_info)
    if 'diagnostics' in result:
        converted['subtitle_query_diagnostics'] = result['diagnostics']
    return converted
