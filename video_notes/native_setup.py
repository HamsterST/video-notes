"""First-run registration for the fixed development extension ID."""
import json
from pathlib import Path
import re
import secrets
import sys

from .paths import INSTALL_DIR, NATIVE_DIR

EXTENSION_ID = 'bdflbgaljaicihmkghggejbdclhjnnpk'


def write_if_changed(path, text, encoding):
    if not path.exists() or path.read_text(encoding=encoding) != text:
        path.write_text(text, encoding=encoding)


def ensure_registered(extension_id=EXTENSION_ID):
    if sys.platform != 'win32':
        raise ValueError('后台扩展自动注册目前仅支持 Windows。')
    if not re.fullmatch('[a-p]{32}', extension_id):
        raise ValueError('扩展 ID 无效。')
    import winreg
    root = INSTALL_DIR
    python = Path(sys.executable).resolve()
    # CMD launchers need literal paths; reject shell expansion characters.
    if any(c in str(root) + str(python) for c in '%!\r\n"'):
        raise ValueError('项目或 Python 路径包含不支持的 CMD 特殊字符。')
    target = NATIVE_DIR
    target.mkdir(parents=True, exist_ok=True)
    launcher = target / 'host.cmd'
    write_if_changed(launcher, f'@echo off\ncd /d "{root}"\n"{python}" -u -m video_notes.native_host\n', encoding='mbcs')
    manifest = target / 'com.videonotes.host.json'
    write_if_changed(manifest, json.dumps({'name':'com.videonotes.host', 'description':'VideoNotes local subtitle extraction',
                                   'path':str(launcher), 'type':'stdio',
                                   'allowed_origins':[f'chrome-extension://{extension_id}/']}, indent=2), encoding='utf-8')
    key_name = r'Software\Google\Chrome\NativeMessagingHosts\com.videonotes.host'
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_name, 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, str(manifest))
    secret_file = target / 'bridge-secret.json'
    if not secret_file.exists():
        secret_file.write_text(json.dumps({'token': secrets.token_urlsafe(32)}), encoding='utf-8')
    bridge_launcher = target / 'bridge.cmd'
    write_if_changed(bridge_launcher, f'@echo off\ncd /d "{root}"\n"{python}" -u -m video_notes.cookie_bridge\n', encoding='mbcs')
    bridge_manifest = target / 'com.videonotes.bridge.json'
    write_if_changed(bridge_manifest, json.dumps({'name':'com.videonotes.bridge', 'description':'VideoNotes background cookie bridge',
                                          'path':str(bridge_launcher), 'type':'stdio',
                                          'allowed_origins':[f'chrome-extension://{extension_id}/']}, indent=2), encoding='utf-8')
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, r'Software\Google\Chrome\NativeMessagingHosts\com.videonotes.bridge', 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, str(bridge_manifest))


def registration_missing():
    """Read-only check. Access denial is not evidence of a missing registration."""
    if sys.platform != 'win32':
        raise ValueError('后台扩展自动注册目前仅支持 Windows。')
    import winreg
    for name in ('host', 'bridge'):
        key_name = r'Software\Google\Chrome\NativeMessagingHosts\com.videonotes.' + name
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name, 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, '')
        except FileNotFoundError:
            return True
        expected = NATIVE_DIR / f'com.videonotes.{name}.json'
        if not isinstance(value, str) or Path(value).resolve() != expected.resolve():
            raise ValueError('本机通信注册指向其他安装目录，请在当前项目运行 scripts/install_native_host.py 修复。')
        if not expected.is_file() or not (NATIVE_DIR / f'{name}.cmd').is_file():
            return True
    return not (NATIVE_DIR / 'bridge-secret.json').is_file()
