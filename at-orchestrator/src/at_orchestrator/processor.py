"""Pipeline orchestrator — the heart of at-orchestrator.

Orchestrates the full pipeline: reads pending tasks from DB → builds a
batch classification prompt → parses LLM result (JSON array) → dispatches
each task to its sub-skill → smart routes reply → updates DB state machine.

Usage::

    from at_orchestrator.processor import Processor

    processor = Processor(client=http_client, sender_uid=12345)
    results = await processor.process_pending(limit=5, llm_result=llm_output)
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
    """
    if "stdout" not in dispatch_result:
        return "pm"
    stdout: str = str(dispatch_result.get("stdout", ""))
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
        client: object,
        sender_uid: int,
        dispatcher: Dispatcher | None = None,
    ) -> None:
        self._client = client
        self._sender_uid = sender_uid
        self._dispatcher = dispatcher or Dispatcher()

    @staticmethod
    def _make_task_result(task: dict[str, Any]) -> dict[str, Any]:
        return {
            "msg_id": int(task.get("msg_id", 0)),
            "source": str(task.get("source", "unknown")),
            "status": "pending",
            "error": None,
            "reply_method": None,
        }

    async def _process_one(
        self,
        task: dict[str, Any],
        classification: dict[str, Any],
    ) -> dict[str, Any]:
        """Run dispatch → reply for a single classified task.

        This method owns the dispatching and replying phases.
        """
        msg_id: int = int(task.get("msg_id", 0))
        source: str = str(task.get("source", "unknown"))
        result = self._make_task_result(task)

        try:
            # ── Phase 2: Dispatching ─────────────────────────────────
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
                result["status"] = "failed"
                result["error"] = error_msg
                return result

        except Exception as exc:
            await db.update_task_status(
                msg_id, source, "failed",
                error=f"dispatch_error: {exc}",
            )
            result["status"] = "failed"
            result["error"] = f"dispatch_error: {exc}"
            return result

        # ── Phase 3: Replying ────────────────────────────────────────
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
                result["status"] = "replied"
                result["reply_method"] = reply_method
            else:
                await db.update_task_status(
                    msg_id, source, "failed", error="reply_failed"
                )
                result["status"] = "failed"
                result["error"] = "reply_failed"

        except Exception as exc:
            await db.update_task_status(
                msg_id, source, "failed",
                error=f"reply_exception: {exc}",
            )
            result["status"] = "failed"
            result["error"] = f"reply_exception: {exc}"

        return result

    # ── Public API ──────────────────────────────────────────────────

    async def process_pending(
        self,
        limit: int = 1,
        dry_run: bool = False,
        llm_result: str | None = None,
    ) -> list[dict[str, Any]]:
        """Process pending AT tasks through the full pipeline.

        Builds a single batch prompt for all pending tasks.  When
        ``llm_result`` is provided, parses the JSON array and processes
        each task through dispatch → reply.

        Args:
            limit: Maximum number of pending tasks to process (default 1).
            dry_run: If ``True``, prints the classification prompt and
                     skips all side-effects (no DB writes, no dispatch).
            llm_result: Raw LLM output text (JSON array) to parse for
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

        # 2. Build a single batch prompt for all tasks
        batch_prompt = classifier.build_batch_classification_prompt(tasks)

        # 3. Dry run — print and skip, no DB changes
        if dry_run:
            print("[DRY-RUN] Batch classification prompt:")
            print(batch_prompt)
            for task in tasks:
                r = self._make_task_result(task)
                r["status"] = "classifying"
                results.append(r)
            return results

        # 4. No LLM result yet — print prompt, mark all as classifying, stop
        if llm_result is None:
            print(batch_prompt)
            for task in tasks:
                msg_id = int(task.get("msg_id", 0))
                source = str(task.get("source", "unknown"))
                await db.update_task_status(msg_id, source, "classifying")
                r = self._make_task_result(task)
                r["status"] = "classifying"
                results.append(r)
            return results

        # 5. Parse LLM result
        classifications = classifier.parse_llm_result(llm_result)
        if classifications is None:
            for task in tasks:
                msg_id = int(task.get("msg_id", 0))
                source = str(task.get("source", "unknown"))
                await db.update_task_status(
                    msg_id, source, "failed", error="classification_failed"
                )
                r = self._make_task_result(task)
                r["status"] = "failed"
                r["error"] = "classification_failed"
                results.append(r)
            return results

        # Build msg_id → classification lookup
        class_by_msg_id: dict[int, dict[str, Any]] = {}
        for c in classifications:
            mid = c.get("msg_id")
            if mid is not None:
                class_by_msg_id[int(mid)] = c

        # 6. Process each task
        for task in tasks:
            msg_id = int(task.get("msg_id", 0))
            source = str(task.get("source", "unknown"))

            classification = class_by_msg_id.get(msg_id)

            if classification is None:
                await db.update_task_status(
                    msg_id, source, "failed",
                    error="no_classification_in_llm_output",
                )
                r = self._make_task_result(task)
                r["status"] = "failed"
                r["error"] = "no_classification_in_llm_output"
                results.append(r)
                continue

            # Mark as classifying
            await db.update_task_status(msg_id, source, "classifying")

            # Unknown skill — skip dispatch, mark replied
            if classification.get("skill_name") == "unknown":
                await db.update_task_status(msg_id, source, "replied")
                await db.update_task_reply(msg_id, source, "none")
                r = self._make_task_result(task)
                r["status"] = "replied"
                r["reply_method"] = "none"
                results.append(r)
                continue

            # Full pipeline: dispatch → reply
            r = await self._process_one(task, classification)
            results.append(r)

        return results
