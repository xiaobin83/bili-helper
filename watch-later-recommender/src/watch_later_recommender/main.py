"""watch-later-recommender CLI — B站 智能推荐.

Two-phase workflow:
  Phase 1 — generate prompt:
      uv run watch-later-recommender --target fav
  Phase 2 — apply LLM result (no --target needed, inferred from LLM output):
      uv run watch-later-recommender --apply-llm-result llm-output.json

Other options:
      uv run watch-later-recommender --target fav --topic "编程教程"
      uv run watch-later-recommender --dry-run
      uv run watch-later-recommender --init-prefs
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
from watch_later_recommender.recommender import (
    TOVIEW_CAPACITY_LIMIT,
    TOVIEW_WARN_THRESHOLD,
    add_recommendations,
    build_llm_prompt,
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
        help="推荐目标（仅生成 prompt 时需要）: toview（稍后再看，默认）| fav（收藏夹）。使用 --apply-llm-result 时无需指定，由 LLM 结果自动推断",
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
    parser.add_argument(
        "--apply-llm-result",
        type=str,
        default=None,
        metavar="PATH_OR_JSON",
        help="应用 LLM 推荐结果：JSON 文件路径、JSON 字符串，或 '-' 从 stdin 读取。无此参数时只输出 prompt，不执行添加",
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


def _load_llm_text(source: str) -> str | None:
    """Load LLM output from a file path, stdin ('-'), or inline JSON string.

    Returns ``None`` if the source cannot be read.
    """
    if source == "-":
        import sys as _sys
        return _sys.stdin.read()

    src_path = Path(source)
    if src_path.exists():
        try:
            return src_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    # Treat as inline JSON string
    return source


def _print_llm_summary(llm_text: str) -> None:
    """Print the human-readable portion of LLM output (text before JSON block)."""
    import re

    # Find where the JSON block starts (```json fence or raw {)
    m = re.search(r"```(?:json)?\s*\n", llm_text)
    if m:
        summary = llm_text[: m.start()].strip()
    else:
        json_start = llm_text.find("{")
        if json_start > 0:
            summary = llm_text[:json_start].strip()
        else:
            return  # No summary to print

    if summary:
        print("💬 LLM 推荐总结:")
        print("-" * 60)
        print(summary)
        print("-" * 60)
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
        print("⚠️ 未登录，将跳过个性化推荐和添加功能")
        creds = None

    if not creds or not creds.sessdata:
        print("⚠️ 未登录，将以游客模式运行（仅推荐，不添加）")

    # Run pipeline
    async with BiliAPIClient(creds) as client:
        # Phase 1-4: Fetch candidates
        if args.topic:
            print(f"🔍 正在搜索「{args.topic}」相关视频...")
            candidates, counts = await search_candidates(client, args.topic)
        else:
            candidates, counts = await fetch_candidates(client)

        if not candidates:
            print("❌ 未获取到候选视频")
            return 1

        # --- Without --apply-llm-result: generate prompt and exit ---
        if not args.apply_llm_result:
            folders = await fetch_folders(client, creds.mid) if (creds and args.target == "fav") else []

            prompt = build_llm_prompt(
                candidates, prefs, count=args.count,
                target=args.target, folders=folders, topic=args.topic,
            )

            print(f"📝 LLM Prompt ({len(prompt)} chars):")
            print(prompt)
            print()
            print("=" * 60)
            print()
            print("💡 将上述 prompt 发送给 LLM，然后用 --apply-llm-result 传入 LLM 的返回结果来执行添加：")
            print(f"   uv run watch-later-recommender"
                  f"{' --folder-name ' + args.folder_name if args.folder_name else ''}"
                  f"{' --topic ' + args.topic if args.topic else ''}"
                  f" --count {args.count}"
                  f" --apply-llm-result llm-output.json")
            return 0

        # --- With --apply-llm-result: parse, validate, execute ---
        llm_text = _load_llm_text(args.apply_llm_result)
        if not llm_text:
            print(f"❌ 无法读取 LLM 结果: {args.apply_llm_result}")
            return 1

        result = parse_llm_result(llm_text, candidates, args.count)
        if not result:
            print("❌ LLM 结果解析失败，请确认输出包含有效的 JSON 数据")
            return 1

        # Infer target from LLM result (no need for --target on this invocation)
        if result.target_action in ("add_to_existing", "create_new"):
            effective_target = "fav"
        else:
            effective_target = "toview"

        # Fetch folders (needed for fav target — LLM decides, not CLI arg)
        if effective_target == "fav":
            folders = await fetch_folders(client, creds.mid) if creds else []
        else:
            folders = []

        # Print the human-readable summary part (before the JSON)
        _print_llm_summary(llm_text)

        # Determine target folder for fav
        folder_name = ""
        action = "add_to_existing"
        if effective_target == "fav":
            if args.folder_name:
                folder_name = args.folder_name
                exists = any(f.title == folder_name for f in folders)
                action = "add_to_existing" if exists else "create_new"
            else:
                folder_name = result.target_folder or "默认收藏夹"
                action = result.target_action or "add_to_existing"

        # Build rec_dicts from LLM result
        rec_dicts = [
            {
                "bvid": bvid,
                "aid": next((v.aid for v in candidates if v.bvid == bvid), 0),
                "title": "",
                "reason": reason,
            }
            for bvid, reason in zip(result.bvids, result.reasons)
        ]

        # Phase 6: Execute add (or dry-run)
        if args.dry_run:
            _print_results(
                candidates, counts, result.bvids, result.reasons,
                target=effective_target, target_folder=folder_name,
            )
            if effective_target == "fav":
                print(f"📁 目标收藏夹: {folder_name} ({'新建' if action == 'create_new' else '添加到已有'})")
            print("🔍 干跑模式，未实际添加")
            return 0

        if effective_target == "toview":
            toview_list = await client.fetch_toview_list()
            toview_count = len(toview_list)

            if toview_count >= TOVIEW_WARN_THRESHOLD:
                print(
                    f"❌ 稍后再看列表空间不足（{toview_count}/{TOVIEW_CAPACITY_LIMIT}），"
                    f"请先清理后再试"
                )
                return 4

            add_result = await add_recommendations(
                client, rec_dicts, target="toview", toview_count=toview_count,
            )
        else:
            add_result = await add_recommendations(
                client, rec_dicts, target="fav",
                target_folder=folder_name, target_action=action,
                folders=folders,
            )

        _print_results(
            candidates, counts, result.bvids, result.reasons,
            target=effective_target, target_folder=folder_name,
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
