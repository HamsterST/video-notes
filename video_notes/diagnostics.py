"""Optional login checks. Return status only, never account details or tokens."""
import json
from urllib.parse import urlsplit

from yt_dlp.networking import Request


def cookie_failure_reason(error):
    parts = []
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        parts.append(str(error).lower())
        error = error.__cause__ or error.__context__
    text = ' '.join(parts)
    if 'could not copy' in text or 'permission denied' in text:
        return 'database_access_denied', '无法访问 Chrome/浏览器 Cookie 数据库，可能正被浏览器占用或文件权限不足。请自行完全退出浏览器（包括后台进程）后重试；无需退出网站账号。'
    if any(word in text for word in ('dpapi', 'decrypt', 'app-bound')):
        return 'decryption_failed', '浏览器 Cookie 解密失败。关闭标签页通常无法解决加密限制；可尝试已登录该网站的 Firefox。'
    if 'could not find' in text:
        return 'profile_not_found', '找不到浏览器 Cookie 数据库，请检查浏览器配置目录。'
    return 'unknown', '无法读取浏览器登录信息，请检查配置目录、文件权限或 Cookie 加密限制。'


def check_login(ydl, url):
    host = urlsplit(url).hostname.lower()
    if host != 'bilibili.com' and not host.endswith('.bilibili.com'):
        return {'status': 'unsupported', 'message': '此平台暂未接入登录有效性检测；Cookie 读取成功不等于登录有效。'}
    try:
        with ydl.urlopen(Request('https://api.bilibili.com/x/web-interface/nav',
                                headers={'Referer': 'https://www.bilibili.com/'})) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('Response too large')
        data = json.loads(raw.decode('utf-8'))
        logged_in = (data.get('data') or {}).get('isLogin')
        if data.get('code') == 0 and logged_in is True:
            return {'status': 'logged_in', 'message': '视频网站已确认当前请求的登录状态有效。'}
        if data.get('code') == -101 or (data.get('code') == 0 and logged_in is False):
            return {'status': 'not_logged_in', 'message': '视频网站未认可当前登录状态：可能未登录，或 Cookie 已失效。'}
    except Exception:
        pass
    return {'status': 'unknown', 'message': '暂时无法确认视频网站的登录状态，继续查询字幕。'}
