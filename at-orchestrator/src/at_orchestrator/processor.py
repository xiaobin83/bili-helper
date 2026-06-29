"""Pipeline orchestrator — 3-phase AT-task processing.

Phase 1: ``process_classification()`` — classifies pending AT tasks via LLM.
Phase 2: ``build_skill_prompts()`` / ``apply_skill_results()`` — builds
         skill-specific prompts and applies LLM skill results.
Phase 3: ``execute_replies()`` — posts replies for tasks ready to reply.

Usage::

    from at_orchestrator.processor import Processor

    processor = Processor(client=http_client, sender_uid=12345)
    results = await processor.process_classification(limit=5, llm_result=llm_output)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from at_orchestrator import classifier, db, replier
from at_orchestrator.dispatcher import Dispatcher

logger = logging.getLogger(__name__)


def _decide_reply_method(reply_content: str, task: dict) -> str:
    """Decide whether to reply via comment or private message.

    Short reply → comment; long reply → PM.  If no ``subject_id``,
    default to PM.
    """
    if len(reply_content) < 200 and task.get("subject_id"):
        return "comment"
    return "pm"


class Processor:
    """Orchestrates the 3-phase AT-task pipeline.

    Phase 1: pending → classifying → classified
    Phase 2: classified → prompting → pending_reply
    Phase 3: pending_reply → replying → replied / failed
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

    # ── Phase 1: Classification ───────────────────────────────────────

    async def process_classification(
        self,
        limit: int = 1,
        dry_run: bool = False,
        llm_result: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        """Phase 1: Classify pending tasks via LLM batch prompt.

        Builds a batch classification prompt for pending tasks.  When
        ``llm_result`` is provided, parses it and writes classifications
        to DB, advancing status to ``'classified'``.

        Args:
            limit: Maximum pending tasks (default 1).
            dry_run: Print prompt, skip all DB writes.
            llm_result: Raw LLM JSON output.  When ``None`` and not
                        dry_run, the prompt is printed and tasks advance
                        to ``'classifying'`` (awaiting LLM result).

        Returns:
            List of result dicts with ``msg_id``, ``source``, ``status``,
            ``error``, ``reply_method``.
        """
        results: list[dict[str, Any]] = []

        if llm_result is not None and not dry_run:
            tasks = await db.get_tasks_by_status("classifying", limit)
        else:
            tasks = await db.get_pending_tasks(limit)
        if not tasks:
            return results

        if source is not None:
            tasks = [t for t in tasks if str(t.get("source", "")) == source]
            if not tasks:
                return results

        batch_prompt = classifier.build_batch_classification_prompt(tasks)

        if dry_run:
            print("[DRY-RUN] Batch classification prompt:")
            print(batch_prompt)
            for task in tasks:
                r = self._make_task_result(task)
                r["status"] = "classifying"
                results.append(r)
            return results

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

        class_by_msg_id: dict[int, dict[str, Any]] = {}
        for c in classifications:
            mid = c.get("msg_id")
            if mid is not None:
                class_by_msg_id[int(mid)] = c

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

            if classification.get("skill_name") == "unknown":
                await db.update_task_status(msg_id, source, "replied")
                await db.update_task_reply(msg_id, source, "none")
                r = self._make_task_result(task)
                r["status"] = "replied"
                r["reply_method"] = "none"
                results.append(r)
                continue

            classification_json = json.dumps(classification, ensure_ascii=False)
            await db.update_classification(msg_id, source, classification_json)
            r = self._make_task_result(task)
            r["status"] = "classified"
            results.append(r)

        return results

    # ── Phase 2: Skill prompt generation ──────────────────────────────

    async def build_skill_prompts(
        self, limit: int = 5, dry_run: bool = False
    ) -> list[dict[str, Any]]:
        """Phase 2a: Read 'classified' tasks, build skill prompts, print to stdout.

        Each task advances from ``'classified'`` to ``'prompting'``.
        When *dry_run* is ``True``, prints prompts without advancing status.
        """
        results: list[dict[str, Any]] = []

        tasks = await db.get_tasks_by_status("classified", limit)
        if not tasks:
            return results

        for task in tasks:
            msg_id = int(task.get("msg_id", 0))
            source = str(task.get("source", "unknown"))

            classification_json = task.get("classification_result", "{}")
            try:
                classification = json.loads(classification_json)
            except (json.JSONDecodeError, TypeError):
                classification = {"skill_name": "unknown", "params": {}}

            prompt = classifier.build_skill_prompt(task, classification)
            skill_name = classification.get("skill_name", "unknown")

            print(f"--- SKILL PROMPT msg_id={msg_id} source={source} skill={skill_name} ---")
            print(prompt)
            print(f"--- END SKILL PROMPT msg_id={msg_id} ---")

            if not dry_run:
                await db.update_task_status(msg_id, source, "prompting")
            r = self._make_task_result(task)
            r["status"] = "prompting" if not dry_run else str(task.get("status", "classified"))
            results.append(r)

        return results

    async def apply_skill_results(
        self, limit: int = 5, llm_result: str | None = None
    ) -> list[dict[str, Any]]:
        """Phase 2b: Apply LLM skill results to 'prompting' tasks.

        Expects *llm_result* to be valid JSON — either a single object
        or an array of objects.  When an array, each entry must have a
        ``msg_id`` field to match back to a task.  When a single object
        and only one task is prompting, it is applied directly.

        Each task advances from ``'prompting'`` to ``'pending_reply'``.
        """
        results: list[dict[str, Any]] = []

        tasks = await db.get_tasks_by_status("prompting", limit)
        if not tasks:
            return results

        if llm_result is None:
            return results

        parsed_items = _parse_skill_llm_output(llm_result)
        if not parsed_items:
            return results

        result_by_msg_id: dict[int, dict[str, Any]] = {}
        for item in parsed_items:
            mid = item.get("msg_id")
            if mid is not None:
                result_by_msg_id[int(mid)] = item

        for task in tasks:
            msg_id = int(task.get("msg_id", 0))
            source = str(task.get("source", "unknown"))

            classification_json = task.get("classification_result", "{}")
            try:
                classification = json.loads(classification_json)
            except (json.JSONDecodeError, TypeError):
                classification = {"skill_name": "unknown", "params": {}}
            skill_name = classification.get("skill_name", "unknown")

            skill_data = result_by_msg_id.get(msg_id)

            if skill_data is None and len(tasks) == 1 and len(parsed_items) == 1:
                single = parsed_items[0]
                if "msg_id" not in single:
                    skill_data = single

            if skill_data is None:
                await db.update_task_status(
                    msg_id, source, "failed",
                    error="no_skill_result_found",
                )
                r = self._make_task_result(task)
                r["status"] = "failed"
                r["error"] = "no_skill_result_found"
                results.append(r)
                continue

            reply_content = skill_data.get("reply_content", "")
            if not reply_content:
                reply_content = llm_result.strip()

            skill_json = json.dumps(skill_data, ensure_ascii=False)
            await db.update_skill_result(msg_id, source, skill_json, reply_content)
            r = self._make_task_result(task)
            r["status"] = "pending_reply"
            r["reply_method"] = reply_content
            results.append(r)

        return results

    # ── Phase 3: Reply execution ──────────────────────────────────────

    async def execute_replies(
        self, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Phase 3: Read 'pending_reply' tasks and post replies.

        Each task advances to ``'replied'`` or ``'failed'``.
        """
        results: list[dict[str, Any]] = []

        tasks = await db.get_tasks_by_status("pending_reply", limit)
        if not tasks:
            return results

        for task in tasks:
            msg_id = int(task.get("msg_id", 0))
            source = str(task.get("source", "unknown"))
            reply_content = str(task.get("reply_method", ""))
            r = self._make_task_result(task)

            await db.update_task_status(msg_id, source, "replying")

            try:
                reply_method = _decide_reply_method(reply_content, task)
                receiver_id = int(task.get("user_mid", 0))
                reply_ok = False

                if reply_method == "pm":
                    try:
                        has_session = await replier.check_session_detail(
                            self._client, self._sender_uid, receiver_id
                        )
                    except Exception:
                        has_session = False

                    if has_session:
                        reply_ok = await replier.reply_pm(
                            self._client, self._sender_uid, receiver_id, reply_content
                        )
                    else:
                        logger.info(
                            "PM session not available for %d → %d, falling back to comment",
                            self._sender_uid, receiver_id,
                        )
                        reply_method = "comment"
                        reply_ok = await replier.reply_comment(
                            self._client, task, reply_content
                        )
                else:
                    reply_ok = await replier.reply_comment(
                        self._client, task, reply_content
                    )

                if reply_ok:
                    await db.update_task_status(msg_id, source, "replied")
                    await db.update_task_reply(msg_id, source, reply_method)
                    r["status"] = "replied"
                    r["reply_method"] = reply_method
                else:
                    await db.update_task_status(
                        msg_id, source, "failed", error="reply_failed"
                    )
                    r["status"] = "failed"
                    r["error"] = "reply_failed"

            except Exception as exc:
                await db.update_task_status(
                    msg_id, source, "failed",
                    error=f"reply_exception: {exc}",
                )
                r["status"] = "failed"
                r["error"] = f"reply_exception: {exc}"

            results.append(r)

        return results

    # ── Backward-compat wrapper ───────────────────────────────────────

    async def process_pending(
        self,
        limit: int = 1,
        dry_run: bool = False,
        llm_result: str | None = None,
    ) -> list[dict[str, Any]]:
        """Backward-compatible wrapper — delegates to Phase 1 classification."""
        return await self.process_classification(
            limit=limit, dry_run=dry_run, llm_result=llm_result
        )


def _parse_skill_llm_output(llm_text: str) -> list[dict[str, Any]]:
    """Extract and parse JSON (array or object) from LLM text output.

    Returns a list of dicts — wraps a single object into a list.
    Returns an empty list on failure.
    """
    json_str = classifier._extract_json_text(llm_text)
    if json_str is None:
        try:
            json_str = llm_text.strip()
        except Exception:
            return []

    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        return []

    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]

    return []
