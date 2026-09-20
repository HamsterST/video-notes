"""Use the running broker first; never rewrite healthy native registrations."""
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler
from .native_setup import ensure_registered, registration_missing
from .cookie_bridge import PORT, config_path
from .native_host import make_jar


def _register_if_missing():
    try:
        missing = registration_missing()
    except PermissionError:
        raise ValueError('现有扩展连接不可用，且无权限读取本机通信注册状态；未尝试写入注册表。请在有权限的环境检查注册。') from None
    except OSError:
        raise ValueError('无法读取本机通信注册状态；未尝试重复注册。') from None
    if not missing:
        return False
    try:
        ensure_registered()
    except PermissionError:
        raise ValueError('本机通信注册失败：当前执行环境拒绝写入配置或 Windows 注册表（权限不足）。请在有权限的终端运行 scripts/install_native_host.py；这不是扩展登录失败。') from None
    except OSError:
        raise ValueError('本机通信注册失败：无法写入配置或注册表，请检查安装目录和执行环境。') from None
    return True


def _token():
    try:
        value = json.loads(config_path().read_text(encoding='utf-8'))['token']
        if not isinstance(value, str) or not value or any(c in value for c in '\r\n'):
            raise ValueError()
        return value
    except FileNotFoundError:
        raise
    except PermissionError:
        raise ValueError('无法读取本机通信口令文件：当前执行环境没有读取权限。未尝试重新注册。') from None
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError('本机通信口令配置损坏或不可读，请运行 scripts/install_native_host.py 检查修复。') from None


def extension_cookies(url):
    checked_registration = False
    try:
        token = _token()
    except FileNotFoundError:
        _register_if_missing()
        checked_registration = True
        try:
            token = _token()
        except FileNotFoundError:
            raise ValueError('本机通信口令文件缺失，注册未生成有效配置。') from None
    request = Request(f'http://127.0.0.1:{PORT}/cookies',
                      data=json.dumps({'url': url}).encode('utf-8'),
                      headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + 40
    while True:
        try:
            with opener.open(request, timeout=30) as response:
                data = response.read(4 * 1024 * 1024 + 1)
            break
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise ValueError('本机扩展连接认证失败：口令可能与正在运行的服务不一致。请重新加载扩展后重试。') from None
            raise ValueError(f'本机扩展服务返回 HTTP {exc.code}；未重新注册，请检查扩展运行状态。') from None
        except URLError as exc:
            if not isinstance(exc.reason, ConnectionRefusedError):
                raise ValueError('请求本机扩展服务失败或超时；未重新注册，请检查服务和执行环境的网络权限。') from None
            if not checked_registration:
                _register_if_missing()
                checked_registration = True
            if time.monotonic() >= deadline:
                raise ValueError('本机通信已注册，但扩展服务连接被拒绝。请确认 Chrome 正在运行且扩展已启用；也可使用 --cookies 或 --no-cookies。') from None
            time.sleep(1)
        except (TimeoutError, OSError):
            raise ValueError('请求本机扩展服务失败或超时；未重新注册。') from None
    try:
        if len(data) > 4 * 1024 * 1024:
            raise ValueError()
        result = json.loads(data)
        if not isinstance(result, dict) or result.get('type') != 'cookies_response' or not isinstance(result.get('cookies'), list):
            raise ValueError()
        _, jar = make_jar({'url': url, 'cookies': result['cookies']})
        return jar
    except (ValueError, TypeError, KeyError):
        raise ValueError('本机扩展返回的数据格式无效；连接已建立，未重新注册。') from None
