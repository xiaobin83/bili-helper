"""AT Orchestrator CLI - Bilibili up主 AT（@）互动编排工具."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

from at_orchestrator import __version__
from at_orchestrator import db as at_db
from at_orchestrator.constants import DB_PATH_DEFAULT


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="at-orchestrator",
        description="Bilibili up主 AT（@）互动编排工具",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"at-orchestrator {__version__}",
    )
    parser.add_argument(
        "--db-path",
        default=DB_PATH_DEFAULT,
        help=f"SQLite database path (default: {DB_PATH_DEFAULT})",
    )
    parser.add_argument(
        "--auth-file",
        default=None,
        help="Path to Bilibili credentials JSON file",
    )
    parser.add_argument(
        "--env-prefix",
        default="BILI_",
        help="Environment variable prefix for credentials (default: BILI_)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # fetch
    p_fetch = sub.add_parser("fetch", help="Fetch new @ messages from Bilibili")
    p_fetch.set_defaults(_handler="fetch")

    # process (Phase 1: classification)
    p_process = sub.add_parser("process", help="Process pending AT tasks (Phase 1: classification)")
    p_process.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Maximum number of tasks to process (default: 1)",
    )
    p_process.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview actions without executing",
    )
    p_process.add_argument(
        "--apply-classification-result",
        type=str,
        default=None,
        metavar="FILE",
        help="Apply LLM classification result from file",
    )
    p_process.add_argument(
        "--apply-llm-result",
        type=str,
        default=None,
        metavar="FILE",
        help=argparse.SUPPRESS,
    )
    p_process.set_defaults(_handler="process")

    # skill-prompt (Phase 2: skill prompt generation/application)
    p_skill = sub.add_parser(
        "skill-prompt",
        help="Build skill prompts or apply LLM skill results (Phase 2)",
    )
    p_skill.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of tasks (default: 5)",
    )
    p_skill.add_argument(
        "--apply-skill-result",
        type=str,
        default=None,
        metavar="FILE",
        help="Apply LLM skill result from file (Phase 2b)",
    )
    p_skill.set_defaults(_handler="skill_prompt")

    # reply (Phase 3: post replies)
    p_reply = sub.add_parser(
        "reply",
        help="Post replies for pending_reply tasks (Phase 3)",
    )
    p_reply.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of tasks (default: 5)",
    )
    p_reply.set_defaults(_handler="reply")

    # status
    p_status = sub.add_parser("status", help="Show task status counts")
    p_status.set_defaults(_handler="status")

    # reset
    p_reset = sub.add_parser("reset", help="Reset database (drop and recreate tables)")
    p_reset.add_argument(
        "--force",
        action="store_true",
        required=True,
        help="Confirm database reset (required)",
    )
    p_reset.set_defaults(_handler="reset")

    return parser


# ── Resolve LLM result input ──────────────────────────────────────────


def _read_llm_result(arg_value: str | None) -> str | None:
    if arg_value is None:
        return None
    if arg_value == "-":
        return sys.stdin.read()
    p = Path(arg_value)
    if p.exists():
        return p.read_text(encoding="utf-8")
    return arg_value


# ── Async handlers ────────────────────────────────────────────────────


async def _handle_fetch(args: argparse.Namespace) -> None:
    """Fetch new @ and reply messages from Bilibili, store in DB."""
    from bili_core.auth import get_credentials
    from bili_core.http_client import BiliHTTPClient
    from at_orchestrator.fetcher import Fetcher

    creds = get_credentials(env_prefix=args.env_prefix)
    client = BiliHTTPClient(
        sessdata=creds.sessdata,
        bili_jct=creds.bili_jct,
        buvid3=creds.buvid3,
    )
    try:
        os.makedirs(Path(args.db_path).parent, exist_ok=True)
        await at_db.init_db(args.db_path)

        fetcher = Fetcher(client)
        reply_tasks = await fetcher.fetch_reply_messages()
        at_tasks = await fetcher.fetch_at_messages()

        all_tasks = reply_tasks + at_tasks
        inserted = 0
        for task in all_tasks:
            if await at_db.insert_task(task):
                inserted += 1

        print(f"拉取完成: {len(all_tasks)} 条消息, 新增 {inserted} 条")
    finally:
        await client.close()


async def _handle_process(args: argparse.Namespace) -> None:
    """Phase 1: Classify pending AT tasks."""
    from bili_core.auth import get_credentials
    from bili_core.http_client import BiliHTTPClient
    from at_orchestrator.processor import Processor

    llm_result = _read_llm_result(
        args.apply_classification_result or args.apply_llm_result
    )

    creds = get_credentials(env_prefix=args.env_prefix)
    client = BiliHTTPClient(
        sessdata=creds.sessdata,
        bili_jct=creds.bili_jct,
        buvid3=creds.buvid3,
    )
    try:
        os.makedirs(Path(args.db_path).parent, exist_ok=True)
        await at_db.init_db(args.db_path)
        processor = Processor(client=client, sender_uid=creds.mid)
        results = await processor.process_classification(
            limit=args.limit,
            dry_run=args.dry_run,
            llm_result=llm_result,
        )
        for r in results:
            status = r["status"]
            msg_id = r["msg_id"]
            error = r.get("error")
            if error:
                print(f"[{status}] msg_id={msg_id}, error={error}")
            else:
                print(f"[{status}] msg_id={msg_id}")
    finally:
        await client.close()


async def _handle_skill_prompt(args: argparse.Namespace) -> None:
    """Phase 2: Build skill prompts or apply LLM skill results."""
    from at_orchestrator.processor import Processor

    await at_db.init_db(args.db_path)

    if args.apply_skill_result is not None:
        llm_result = _read_llm_result(args.apply_skill_result)
        processor = Processor(client=None, sender_uid=0)
        results = await processor.apply_skill_results(
            limit=args.limit, llm_result=llm_result
        )
        for r in results:
            status = r["status"]
            msg_id = r["msg_id"]
            error = r.get("error")
            if error:
                print(f"[{status}] msg_id={msg_id}, error={error}")
            else:
                print(f"[{status}] msg_id={msg_id}")
    else:
        processor = Processor(client=None, sender_uid=0)
        results = await processor.build_skill_prompts(limit=args.limit)
        if not results:
            print("No classified tasks found.")
        else:
            for r in results:
                print(f"[{r['status']}] msg_id={r['msg_id']}")


async def _handle_reply(args: argparse.Namespace) -> None:
    """Phase 3: Post replies for pending_reply tasks."""
    from bili_core.auth import get_credentials
    from bili_core.http_client import BiliHTTPClient
    from at_orchestrator.processor import Processor

    creds = get_credentials(env_prefix=args.env_prefix)
    client = BiliHTTPClient(
        sessdata=creds.sessdata,
        bili_jct=creds.bili_jct,
        buvid3=creds.buvid3,
    )
    try:
        os.makedirs(Path(args.db_path).parent, exist_ok=True)
        await at_db.init_db(args.db_path)
        processor = Processor(client=client, sender_uid=creds.mid)
        results = await processor.execute_replies(limit=args.limit)
        for r in results:
            status = r["status"]
            msg_id = r["msg_id"]
            error = r.get("error")
            reply_method = r.get("reply_method", "")
            if error:
                print(f"[{status}] msg_id={msg_id}, error={error}")
            else:
                print(f"[{status}] msg_id={msg_id}, method={reply_method}")
    finally:
        await client.close()


async def _handle_status(args: argparse.Namespace) -> None:
    """Show task status counts from the database."""
    os.makedirs(Path(args.db_path).parent, exist_ok=True)
    await at_db.init_db(args.db_path)

    def _query_counts() -> tuple[dict[str, int], int]:
        conn = sqlite3.connect(args.db_path, check_same_thread=False)
        try:
            total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status"
            ).fetchall()
            return {r[0]: r[1] for r in rows}, total
        finally:
            conn.close()

    counts, total = await asyncio.to_thread(_query_counts)

    for status in (
        "pending", "classifying", "classified", "prompting",
        "pending_reply", "replied", "failed",
    ):
        cnt = counts.get(status, 0)
        if cnt > 0:
            print(f"{status}:  {cnt}")
    other = total - sum(counts.values())
    if other > 0:
        print(f"other:    {other}")
    print(f"total:    {total}")


async def _handle_reset(args: argparse.Namespace) -> None:
    """Drop and recreate all tables."""
    await asyncio.to_thread(_drop_tables, args.db_path)
    os.makedirs(Path(args.db_path).parent, exist_ok=True)
    await at_db.init_db(args.db_path)
    print(f"数据库已重置: {args.db_path}")


def _drop_tables(db_path: str) -> None:
    """Drop tasks and cursor_state tables."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        conn.execute("DROP TABLE IF EXISTS tasks")
        conn.execute("DROP TABLE IF EXISTS cursor_state")
        conn.commit()
    finally:
        conn.close()


# ── Entry point ───────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "_handler", None)

    if handler == "fetch":
        asyncio.run(_handle_fetch(args))
    elif handler == "process":
        asyncio.run(_handle_process(args))
    elif handler == "skill_prompt":
        asyncio.run(_handle_skill_prompt(args))
    elif handler == "reply":
        asyncio.run(_handle_reply(args))
    elif handler == "status":
        asyncio.run(_handle_status(args))
    elif handler == "reset":
        asyncio.run(_handle_reset(args))


if __name__ == "__main__":
    main()
