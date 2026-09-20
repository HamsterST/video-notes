import argparse
import json
import sys
from functools import partial

from .workflow import run_workflow
from .media import download_and_transcribe
from .paths import DOWNLOAD_DIR
from .settings import set_model


def choose_action():
    while True:
        print('1. 已处理登录或 Cookie 问题，重试获取字幕（仍失败将自动下载并转写）\n2. 直接下载音频并转写\n请输入 1 或 2，Ctrl+C 取消：', file=sys.stderr, flush=True)
        try:
            answer = input().strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if answer in {'1', '2'}:
            return 'retry' if answer == '1' else 'transcribe'


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="VideoNotes：后台扩展获取登录信息，自动获取字幕或转写音频。")
    parser.add_argument("url", nargs="?")
    parser.add_argument("--set-model", metavar="MODEL", help="保存默认 Whisper 模型，推荐 small、large-v3-turbo、large-v3；可不带视频链接")
    parser.add_argument("--refresh", action="store_true", help="重新查询字幕或识别，仍复用已有完整媒体")
    parser.add_argument("--subtitle-language", help="指定源字幕轨道，例如 en、zh-Hans；不等于翻译语言")
    parser.add_argument("--subtitle-file", help="直接使用已有 SRT/VTT/字幕 JSON 或成功的结果 JSON，不下载或识别")
    parser.add_argument("--output-language", default="zh", help="后续笔记的目标语言，默认 zh；本命令不执行翻译")
    parser.add_argument("--gap-seconds", type=float, default=120)
    parser.add_argument("--gap-ratio", type=float, default=0.1)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    auth = parser.add_mutually_exclusive_group()
    auth.add_argument("--cookies-from-browser", default=None, choices=("chrome", "edge", "firefox", "brave", "chromium", "opera", "vivaldi", "safari"), help="显式使用浏览器数据库读取；默认通过 VideoNotes 扩展获取")
    auth.add_argument('--cookies', dest='cookie_file', help='导入 Netscape 格式 Cookie 文件，替代默认 Chrome 读取')
    auth.add_argument('--no-cookies', action='store_true', help='不读取浏览器 Cookie')
    parser.add_argument("--browser-profile", help="可选浏览器配置目录，例如 Default 或 Profile 1")
    parser.add_argument('--fallback', choices=('ask', 'transcribe', 'stop'), default='transcribe', help='默认自动音频转写；ask 交互选择，stop 只查询字幕')
    parser.add_argument('--diagnostics', action=argparse.BooleanOptionalAction, default=True,
                        help='默认开启登录诊断，使用 --no-diagnostics 关闭')
    parser.add_argument('--download-dir', default=str(DOWNLOAD_DIR), help='音视频缓存目录，默认数据目录下 downloads')
    parser.add_argument('--model', help='模型名称或本地模型目录；仅本次使用，默认读取已保存选择；推荐 small、large-v3-turbo、large-v3')
    parser.add_argument('--model-dir', help='模型下载目录，默认数据目录下 models/faster-whisper')
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
    parser.add_argument('--beam-size', type=int, default=1, help='默认 1 偏重速度，5 为原有解码设置')
    parser.add_argument('--cpu-threads', type=int, help='CPU 推理线程，默认最多 8；更多不一定更快')
    parser.add_argument('--local-video', help='转写时复用指定本地视频；仍先尝试获取链接字幕')
    parser.add_argument('--set-data-dir', help='复制现有数据到指定绝对目录并保存；旧文件保留')
    parser.add_argument('--show-storage', action='store_true', help='显示实际数据路径和媒体保留策略')
    parser.add_argument('--keep-media', action=argparse.BooleanOptionalAction, default=None, help='持久设置是否保留全部媒体缓存')
    parser.add_argument('--clean-media', action='store_true', help='清理本程序管理的媒体缓存，保留模型和文字')
    args = parser.parse_args()
    if args.set_data_dir or args.show_storage or args.keep_media is not None or args.clean_media:
        if args.url:
            parser.error('存储管理请单独执行，然后再处理视频。')
        from .storage import manage_storage
        try:
            print(json.dumps(manage_storage(args), ensure_ascii=False, indent=2))
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        return 0
    if not args.url and not args.set_model:
        parser.error('请提供视频链接，或使用 --set-model MODEL。')
    try:
        if args.set_model:
            selected = set_model(args.set_model)
            print(f'默认本地转写模型已设为 {selected}；需要转写时才下载。', file=sys.stderr)
        if not args.url:
            return 0
        cookie_jar = None
        cookie_loader = None
        if not (args.no_cookies or args.cookie_file or args.cookies_from_browser):
            from urllib.parse import urlsplit, urlencode
            from .extension_client import extension_cookies
            parts = urlsplit(args.url)
            if parts.hostname == 'youtu.be':
                args.url = 'https://www.youtube.com/watch?' + urlencode({'v': parts.path.strip('/')})
                parts = urlsplit(args.url)
            host = parts.hostname or ''
            if host in {'bilibili.com', 'youtube.com'} or host.endswith(('.bilibili.com', '.youtube.com')):
                if args.browser_profile:
                    raise ValueError('--browser-profile 仅用于 --cookies-from-browser；扩展使用安装它的 Chrome 配置。')
                cookie_loader = extension_cookies
        transcriber = partial(download_and_transcribe, download_dir=args.download_dir, model=args.model,
                              model_dir=args.model_dir, device=args.device, local_video=args.local_video,
                              beam_size=args.beam_size, cpu_threads=args.cpu_threads,
                              gap_seconds=args.gap_seconds, gap_ratio=args.gap_ratio, force=args.refresh)
        result = run_workflow(args.url, subtitle_language=args.subtitle_language,
                                    output_language=args.output_language, gap_seconds=args.gap_seconds,
                                    gap_ratio=args.gap_ratio, cookies_from_browser=None if (args.no_cookies or args.cookie_file) else args.cookies_from_browser,
                                    browser_profile=args.browser_profile, cookie_file=args.cookie_file, cookie_jar=cookie_jar, fallback=args.fallback,
                                    diagnostics=args.diagnostics, force=args.refresh,
                                    subtitle_file=args.subtitle_file,
                                    cookie_loader=cookie_loader,
                                    transcript_loader=partial(transcriber, cache_only=True),
                                    choose=choose_action if sys.stdin.isatty() else None, transcriber=transcriber,
                                    progress=lambda message: print(message, file=sys.stderr, flush=True))
    except ValueError as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        print('已停止处理，下载完成的音视频仍保留。转写尚未完成，下次会重新识别。', file=sys.stderr)
        return 130
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(result["message"])
        for warning in result["warnings"]:
            print("提示：" + warning)
        for cue in result["segments"]:
            print(f'[{cue["start"]:.1f}s–{cue["end"]:.1f}s] {cue["text"]}')
    return 0 if result["status"] == "ok" else 2
