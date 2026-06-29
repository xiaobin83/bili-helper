"""Pipeline orchestrator — the heart of at-orchestrator.

Orchestrates the full pipeline: reads pending tasks from DB → builds
classification prompt → parses LLM result → dispatches to sub-skill →
smart routes reply → updates DB state machine.

Usage::

    from at_orchestrator.processor import Processor

    processor = Processor(client=http_client, sender_uid=12345)
    results = await processor.process_pending(limit=1, llm_result=llm_output)
"""

from __future__ import annotations

import logging
from typing import Any

from at_orchestrator import classifier, db, replier
from at_orchestrator.dispatcher import Dispatcher

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Smart routing helper
# ──────────────────────────────────────────────────────────────────────


def _decide_reply_method(dispatch_result: dict, task: dict) -> str:
    """Decide whether to reply via comment or private message.

    Short single result → comment reply; long or multi-result → PM.
    If ``stdout`` is missing from the dispatch result (key absent),
    default to PM as the content is not reliable for a comment.

    Args:
        dispatch_result: Dict from :meth:`Dispatcher.dispatch_with_timeout`
                         with at least a ``stdout`` key.
        task: Task dict with at least ``subject_id``.

    Returns:
        ``"comment"`` or ``"pm"``.
    """
    if "stdout" not in dispatch_result:
        return "pm"
    stdout: str = str(dispatch_result.get("stdout", ""))
    # Short (< 200 chars) + has subject_id → comment; otherwise → PM
    if len(stdout) < 200 and task.get("subject_id"):
        return "comment"
    return "pm"


# ──────────────────────────────────────────────────────────────────────
# Processor
# ──────────────────────────────────────────────────────────────────────


class Processor:
    """Orchestrates the full AT-task pipeline.

    The pipeline state machine::

        pending → classifying → dispatching → replying → replied
                      ↓                     ↓
                  failed(classif)       failed(reply)
    """

    def __init__(
        self,
        client: object,  # BiliHTTPClient (avoid circular import)
        sender_uid: int,
        dispatcher: Dispatcher | None = None,
    ) -> None:
        """Initialise the processor.

        Args:
            client: A ``BiliHTTPClient`` instance with valid credentials.
            sender_uid: The UID of the logged-in user (for PM).
            dispatcher: Optional custom :class:`Dispatcher` instance.
                        Creates a default one if not provided.
        """
        self._client = client
        self._sender_uid = sender_uid
        self._dispatcher = dispatcher or Dispatcher()

    # ── Public API ──────────────────────────────────────────────────

    async def process_pending(
        self,
        limit: int = 1,
        dry_run: bool = False,
        llm_result: str | None = None,
    ) -> list[dict[str, Any]]:
        """Process pending AT tasks through the full pipeline.

        Args:
            limit: Maximum number of pending tasks to process (default 1).
            dry_run: If ``True``, prints the classification prompt and
                     skips dispatching + reply phases.
            llm_result: Raw LLM output text (JSON) to parse for
                        classification.  When ``None`` and not dry_run,
                        the prompt is printed and processing stops
                        (awaiting LLM result from a later run).

        Returns:
            A list of result dicts, each with keys: ``msg_id``, ``source``,
            ``status``, ``error``, ``reply_method``.
        """
        results: list[dict[str, Any]] = []

        # 1. Fetch pending tasks
        tasks = await db.get_pending_tasks(limit)
        if not tasks:
            return results

        for task in tasks:
            msg_id: int = int(task.get("msg_id", 0))
            source: str = str(task.get("source", "unknown"))
            task_result: dict[str, Any] = {
                "msg_id": msg_id,
                "source": source,
                "status": "pending",
                "error": None,
                "reply_method": None,
            }

            # ── Phase 1: Classifying ─────────────────────────────────
            try:
                # 2a. Build prompt
                prompt = classifier.build_classification_prompt(task)

                # 2b. Dry run — print and skip, no DB changes
                if dry_run:
                    print(f"[DRY-RUN] Task {msg_id}/{source}")
                    print(f"[DRY-RUN] Prompt:\n{prompt}")
                    task_result["status"] = "classifying"
                    results.append(task_result)
                    continue

                # 2c. No LLM result yet — mark classifying, print prompt, skip
                if llm_result is None:
                    await db.update_task_status(msg_id, source, "classifying")
                    print(f"[PROMPT] Task {msg_id}/{source}")
                    print(prompt)
                    task_result["status"] = "classifying"
                    results.append(task_result)
                    continue

                # 2d. Mark classifying and parse LLM result
                await db.update_task_status(msg_id, source, "classifying")
                classification = classifier.parse_llm_result(llm_result)
                if classification is None:
                    await db.update_task_status(
                        msg_id, source, "failed", error="classification_failed"
                    )
                    task_result["status"] = "failed"
                    task_result["error"] = "classification_failed"
                    results.append(task_result)
                    continue

                # 2d-extra. Unknown skill — skip dispatch, mark replied
                if classification.get("skill_name") == "unknown":
                    await db.update_task_status(msg_id, source, "replied")
                    await db.update_task_reply(msg_id, source, "none")
                    task_result["status"] = "replied"
                    task_result["reply_method"] = "none"
                    results.append(task_result)
                    continue

            except Exception as exc:
                await db.update_task_status(
                    msg_id, source, "failed",
                    error=f"classification_error: {exc}",
                )
                task_result["status"] = "failed"
                task_result["error"] = f"classification_error: {exc}"
                results.append(task_result)
                continue

            # ── Phase 2: Dispatching ─────────────────────────────────
            try:
                await db.update_task_status(msg_id, source, "dispatching")

                dispatch_result = await self._dispatcher.dispatch_with_timeout(
                    classification, task, timeout=120
                )

                if dispatch_result.get("exit_code", -1) != 0:
                    error_msg = dispatch_result.get("error") or (
                        f"non-zero exit code {dispatch_result.get('exit_code')}"
                    )
                    await db.update_task_status(
                        msg_id, source, "failed", error=error_msg
                    )
                    task_result["status"] = "failed"
                    task_result["error"] = error_msg
                    results.append(task_result)
                    continue

            except Exception as exc:
                await db.update_task_status(
                    msg_id, source, "failed",
                    error=f"dispatch_error: {exc}",
                )
                task_result["status"] = "failed"
                task_result["error"] = f"dispatch_error: {exc}"
                results.append(task_result)
                continue

            # ── Phase 3: Replying ────────────────────────────────────
            try:
                await db.update_task_status(msg_id, source, "replying")

                reply_method = _decide_reply_method(dispatch_result, task)
                stdout: str = dispatch_result.get("stdout", "")
                receiver_id: int = int(task.get("user_mid", 0))
                reply_ok: bool = False

                if reply_method == "pm":
                    try:
                        has_session = await replier.check_session_detail(
                            self._client, self._sender_uid, receiver_id
                        )
                    except Exception:
                        has_session = False

                    if has_session:
                        reply_ok = await replier.reply_pm(
                            self._client, self._sender_uid, receiver_id, stdout
                        )
                    else:
                        logger.info(
                            "PM session not available for %d → %d, falling back to comment",
                            self._sender_uid, receiver_id,
                        )
                        reply_method = "comment"
                        reply_ok = await replier.reply_comment(
                            self._client, task, stdout
                        )

                elif reply_method == "comment":
                    reply_ok = await replier.reply_comment(
                        self._client, task, stdout
                    )

                if reply_ok:
                    await db.update_task_status(msg_id, source, "replied")
                    await db.update_task_reply(msg_id, source, reply_method)
                    task_result["status"] = "replied"
                    task_result["reply_method"] = reply_method
                else:
                    await db.update_task_status(
                        msg_id, source, "failed", error="reply_failed"
                    )
                    task_result["status"] = "failed"
                    task_result["error"] = "reply_failed"

            except Exception as exc:
                await db.update_task_status(
                    msg_id, source, "failed",
                    error=f"reply_exception: {exc}",
                )
                task_result["status"] = "failed"
                task_result["error"] = f"reply_exception: {exc}"

            results.append(task_result)

        return results
