"""video-analyzer: Bilibili video analysis CLI."""

import argparse
import asyncio
import sys

from bili_core.errors import AuthError, CSRFError, RateLimitError

from video_analyzer.api_client import VideoAPIClient
from video_analyzer.markdown_renderer import render_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video-analyzer",
        description="B站视频分析工具 — 一键获取视频六维数据分析报告",
    )
    parser.add_argument("--bvid", type=str, required=True, help="视频 BV 号")
    parser.add_argument("-o", "--output", type=str, default=None, help="输出文件路径")
    parser.add_argument("--no-comments", action="store_true", help="跳过热门评论")
    parser.add_argument("--no-pbp", action="store_true", help="跳过高能进度条")
    parser.add_argument("--no-summary", action="store_true", help="跳过 AI 总结")
    parser.add_argument("--no-playurl", action="store_true", help="跳过播放地址")
    parser.add_argument("--no-screenshot", action="store_true", help="跳过视频截图")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    skip_flags: set[str] = set()
    flag_map = {
        "no_comments": "comments",
        "no_pbp": "pbp",
        "no_summary": "summary",
        "no_playurl": "playurl",
        "no_screenshot": "screenshot",
    }
    for cli_attr, flag_name in flag_map.items():
        if getattr(args, cli_attr, False):
            skip_flags.add(flag_name)

    try:
        client = VideoAPIClient()
        result = asyncio.run(client.analyze_video(args.bvid, skip_flags))
        markdown = render_markdown(result, skip_flags)
    except AuthError:
        print(
            "登录已过期，请重新运行工具以扫码登录，或设置 BILI_SESSDATA 环境变量",
            file=sys.stderr,
        )
        sys.exit(1)
    except CSRFError:
        print("CSRF 校验失败，请重新运行工具以刷新凭证", file=sys.stderr)
        sys.exit(1)
    except RateLimitError as e:
        print(f"请求频率受限，请稍后重试: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"分析失败: {e}", file=sys.stderr)
        sys.exit(1)

    sys.stdout.write(markdown)
    sys.stdout.write("\n")

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(markdown)
                f.write("\n")
        except OSError as e:
            print(f"写入文件失败: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
