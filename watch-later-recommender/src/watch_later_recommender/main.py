"""watch-later-recommender CLI — B站 智能推荐.

Usage:
    uv run watch-later-recommender                        # Full pipeline (toview)
    uv run watch-later-recommender --target fav           # Recommend to favorites
    uv run watch-later-recommender --target fav --topic "编程教程"  # Topic-specific fav
    uv run watch-later-recommender --dry-run              # No actual add
    uv run watch-later-recommender --init-prefs           # Create config
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from bili_core.auth import DEFAULT_AUTH_FILE, get_credentials
from bili_core.errors import AuthError, RateLimitError

from watch_later_recommender.api_client import BiliAPIClient
from watch_later_recommender.prefs import DEFAULT_PREFS_PATH, init_prefs, load_prefs
from watch_later_recommender.models import Folder
from watch_later_recommender.recommender import (
    TOVIEW_CAPACITY_LIMIT,
    TOVIEW_WARN_THRESHOLD,
    add_recommendations,
    build_llm_prompt,
    determine_fav_target,
    fallback_selection,
    fetch_candidates,
    fetch_folders,
    parse_llm_result,
    search_candidates,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="B站 智能推荐 — 从热门/排行/推荐中精选视频加入稍后再看或收藏夹",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只推荐不实际添加到稍后再看",
    )
    parser.add_argument(
        "--init-prefs",
        action="store_true",
        help="初始化偏好配置文件（创建模板）",
    )
    parser.add_argument(
        "--prefs",
        type=str,
        default=None,
        help=f"自定义偏好配置文件路径（默认: {DEFAULT_PREFS_PATH}）",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=5,
        help="推荐视频数量（默认: 5, 最大: 10）",
    )
    parser.add_argument(
        "--target",
        type=str,
        default="toview",
        choices=["toview", "fav"],
        help="推荐目标: toview（稍后再看，默认）| fav（收藏夹）",
    )
    parser.add_argument(
        "--folder-name",
        type=str,
        default=None,
        help="目标收藏夹名称（仅 --target fav 时有效），不指定则由 LLM 或偏好自动匹配",
    )
    parser.add_argument(
        "--topic",
        type=str,
        default=None,
        help="推荐主题关键词，使用搜索获取相关视频作为候选池",
    )
    parser.add_argument(
        "--auth-file",
        type=str,
        default=None,
        help="自定义凭证文件路径",
    )
    parser.add_argument(
        "--env-prefix",
        type=str,
        default="BILI_",
        help="环境变量前缀（默认: BILI_）",
    )
    return parser.parse_args(argv)


def _print_results(
    candidates: list,
    counts: dict,
    selected_bvids: list[str],
    reasons: list[str],
    target: str = "toview",
    target_folder: str = "",
) -> None:
    """Print formatted results."""
    # Source summary
    parts = []
    if counts.get("popular"):
        parts.append(f"热门榜: {counts['popular']} 个")
    if counts.get("ranking"):
        parts.append(f"排行榜: {counts['ranking']} 个")
    if counts.get("rcmd"):
        parts.append(f"个性化推荐: {counts['rcmd']} 个")
    print("📊 数据源: " + " | ".join(parts))
    print(f"📈 去重前: {counts.get('before_dedup', 0)}, 去重后: {counts.get('after_dedup', 0)}")
    if counts.get("ads_removed"):
        print(f"🚫 过滤广告: -{counts['ads_removed']}")
    if counts.get("toview_existing"):
        print(f"📋 已有稍后再看: {counts['toview_existing']} 个, 过滤重复: -{counts.get('toview_removed', 0)}")
    print(f"🎯 候选池: {counts.get('candidates', 0)} 个视频")

    # Target info
    if target == "fav":
        print(f"📁 目标收藏夹: {target_folder or '（待确定）'}")
    else:
        print(f"📋 目标: 稍后再看")
    print()

    # Build bvid->item lookup from candidates
    lookup = {v.bvid: v for v in candidates}

    print("🎬 推荐结果:")
    print("-" * 60)
    for i, (bvid, reason) in enumerate(zip(selected_bvids, reasons), 1):
        v = lookup.get(bvid)
        if v:
            dur = f"{v.duration // 60}分{v.duration % 60}秒" if v.duration else "?"
            print(f"  {i}. {v.title[:50]}")
            print(f"     {bvid} | {v.tname} | {dur} | {v.owner_name}")
        else:
            print(f"  {i}. {bvid}")
        print(f"     💡 {reason}")
        print()


async def _main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Handle --init-prefs
    if args.init_prefs:
        prefs_path = Path(args.prefs) if args.prefs else DEFAULT_PREFS_PATH
        path = init_prefs(prefs_path)
        print(f"✅ 已创建偏好配置文件: {path}")
        print(f"   请编辑该文件设置您的偏好后重新运行。")
        return 0

    # Validate count
    if args.count < 1 or args.count > 10:
        print(f"❌ 推荐数量必须在 1-10 之间（输入: {args.count}）")
        return 1

    # Load preferences
    prefs_path = Path(args.prefs) if args.prefs else DEFAULT_PREFS_PATH
    try:
        prefs = load_prefs(prefs_path)
    except FileNotFoundError:
        return 3

    # Get credentials (graceful: None on failure -> anonymous mode)
    auth_file = Path(args.auth_file) if args.auth_file else None
    try:
        creds = get_credentials(env_prefix=args.env_prefix, auth_file=auth_file)
    except (SystemExit, RuntimeError):
        print("⚠️ 未登录，将跳过个性化推荐和稍后再看添加功能")
        creds = None

    if not creds or not creds.sessdata:
        print("⚠️ 未登录，将以游客模式运行（仅推荐，不添加）")

    # Run pipeline
    async with BiliAPIClient(creds) as client:
        # Use search when topic is provided
        if args.topic:
            print(f"🔍 正在搜索「{args.topic}」相关视频...")
            candidates, counts = await search_candidates(client, args.topic)
        else:
            candidates, counts = await fetch_candidates(client)

        if not candidates:
            print("❌ 未获取到候选视频")
            return 1

        # Build prompt (will be used by agent to call LLM)
        if args.target == "fav":
            folders = await fetch_folders(client, creds.mid) if creds else []
        else:
            folders = []

        prompt = build_llm_prompt(
            candidates, prefs, count=args.count,
            target=args.target, folders=folders, topic=args.topic,
        )

        # Print candidate info for agent to use
        print(f"📝 LLM Prompt ({len(prompt)} chars):")
        print(prompt)
        print()
        print("=" * 60)
        print()

        # For dry-run, show what would be added
        if args.dry_run:
            if args.target == "fav":
                result = fallback_selection(candidates, count=args.count)
                if args.folder_name:
                    folder_name = args.folder_name
                    exists = any(f.title == folder_name for f in folders)
                    action = "add_to_existing" if exists else "create_new"
                else:
                    action, folder_name, folder_desc = determine_fav_target(
                        result.bvids, candidates, folders, prefs,
                    )
                _print_results(
                    candidates, counts, result.bvids, result.reasons,
                    target=args.target, target_folder=folder_name,
                )
                print(f"📁 目标收藏夹: {folder_name} ({'新建' if action == 'create_new' else '添加到已有'})")
            else:
                result = fallback_selection(candidates, count=args.count)
                _print_results(candidates, counts, result.bvids, result.reasons)
            return 0

        if args.target == "toview":
            # Get toview list count for capacity check
            toview_list = await client.fetch_toview_list()
            toview_count = len(toview_list)

            if toview_count >= TOVIEW_WARN_THRESHOLD:
                print(
                    f"❌ 稍后再看列表空间不足（{toview_count}/{TOVIEW_CAPACITY_LIMIT}），"
                    f"请先清理后再试"
                )
                return 4

            # In agent execution context, the orchestrator would:
            # 1. Send prompt to LLM via task()
            # 2. Call parse_llm_result() on the response
            # 3. Call add_recommendations() with selected bvids
            #
            # For CLI mode, use fallback selection
            result = fallback_selection(candidates, count=args.count)

            # Add to toview
            rec_dicts = [
                {"bvid": bvid, "aid": next((v.aid for v in candidates if v.bvid == bvid), 0), "title": "", "reason": reason}
                for bvid, reason in zip(result.bvids, result.reasons)
            ]
            add_result = await add_recommendations(
                client, rec_dicts, target="toview", toview_count=toview_count, dry_run=args.dry_run,
            )

            _print_results(candidates, counts, result.bvids, result.reasons)

            if add_result.get("success"):
                print(f"✅ {add_result.get('message', '操作成功')}")
                return 0
            else:
                if add_result.get("message"):
                    print(f"⚠️ {add_result['message']}")
                return 0 if add_result.get("added") else 4

        else:
            # fav target — fallback selection, determine folder, add to fav
            result = fallback_selection(candidates, count=args.count)

            # Use --folder-name when specified, otherwise auto-determine
            if args.folder_name:
                folder_name = args.folder_name
                folder_desc = ""
                # Check if folder exists; if not, create it
                exists = any(f.title == folder_name for f in folders)
                action = "add_to_existing" if exists else "create_new"
            else:
                action, folder_name, folder_desc = determine_fav_target(
                    result.bvids, candidates, folders, prefs,
                )

            rec_dicts = [
                {"bvid": bvid, "aid": next((v.aid for v in candidates if v.bvid == bvid), 0), "title": "", "reason": reason}
                for bvid, reason in zip(result.bvids, result.reasons)
            ]
            add_result = await add_recommendations(
                client, rec_dicts, target="fav",
                target_folder=folder_name, target_action=action,
                folders=folders, dry_run=args.dry_run,
            )

            _print_results(
                candidates, counts, result.bvids, result.reasons,
                target=args.target, target_folder=folder_name,
            )

            if add_result.get("success"):
                print(f"✅ {add_result.get('message', '操作成功')}")
                return 0
            else:
                if add_result.get("message"):
                    print(f"⚠️ {add_result['message']}")
                return 0 if add_result.get("added") else 4


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Calls ``_main()`` which is async for B站 API calls."""
    try:
        return asyncio.run(_main(argv))
    except AuthError:
        print("❌ 登录已过期，请重新登录")
        return 1
    except RateLimitError:
        print("❌ 请求过于频繁，请稍后再试")
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
