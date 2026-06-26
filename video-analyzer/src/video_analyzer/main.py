"""video-analyzer: Bilibili video analysis CLI."""

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video-analyzer",
        description="B站视频分析工具 — 一键获取视频六维数据分析报告",
    )
    parser.add_argument(
        "--bvid",
        type=str,
        required=True,
        help="视频 BV 号，例如 BV1GJ411x7",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="输出文件路径，默认输出到 .video-analyzer/ 目录",
    )
    parser.add_argument(
        "--no-comments",
        action="store_true",
        help="跳过热门评论获取",
    )
    parser.add_argument(
        "--no-pbp",
        action="store_true",
        help="跳过（高能进度条）获取",
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="跳过 AI 总结获取",
    )
    parser.add_argument(
        "--no-playurl",
        action="store_true",
        help="跳过播放地址获取",
    )
    parser.add_argument(
        "--no-screenshot",
        action="store_true",
        help="跳过视频截图获取",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    _ = args  # Implementation pending
    print(f"video-analyzer: analyzing {args.bvid} ...")


if __name__ == "__main__":
    main()
