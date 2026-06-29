"""Tests for CLI argument parsing and subcommand routing."""

from __future__ import annotations

import pytest

from at_orchestrator.main import _build_parser, main


# ── Global arguments ──────────────────────────────────────────────────


class TestGlobalArgs:
    """Global arguments: --db-path, --auth-file, --env-prefix."""

    def test_default_db_path(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["status"])
        assert args.db_path.endswith(".bili-helper/at-orchestrator.db")

    def test_custom_db_path(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["--db-path", "/tmp/test.db", "status"])
        assert args.db_path == "/tmp/test.db"

    def test_auth_file(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["--auth-file", "/tmp/auth.json", "status"])
        assert args.auth_file == "/tmp/auth.json"

    def test_default_env_prefix(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["--env-prefix", "BILI_", "status"])
        assert args.env_prefix == "BILI_"

    def test_custom_env_prefix(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["--env-prefix", "MY_", "status"])
        assert args.env_prefix == "MY_"

    def test_version(self, capsys: pytest.CaptureFixture[str]) -> None:
        parser = _build_parser()
        with pytest.raises(SystemExit, match="0"):
            parser.parse_args(["--version"])
        captured = capsys.readouterr()
        assert "at-orchestrator" in captured.out


# ── fetch ──────────────────────────────────────────────────────────────


class TestFetch:
    """fetch subcommand — no additional args."""

    def test_fetch_subcommand(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["fetch"])
        assert args.command == "fetch"
        assert args._handler == "fetch"


# ── process (Phase 1) ─────────────────────────────────────────────────


class TestProcess:
    """process subcommand — --limit, --dry-run, --apply-classification-result."""

    def test_process_defaults(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["process"])
        assert args.limit == 1
        assert args.dry_run is False
        assert args.apply_classification_result is None

    def test_process_limit(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["process", "--limit", "5"])
        assert args.limit == 5

    def test_process_dry_run(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["process", "--dry-run"])
        assert args.dry_run is True

    def test_process_apply_classification_result(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["process", "--apply-classification-result", "result.json"])
        assert args.apply_classification_result == "result.json"

    def test_process_apply_llm_result_backward_compat(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["process", "--apply-llm-result", "old.json"])
        assert args.apply_llm_result == "old.json"

    def test_process_combined_args(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(
            [
                "process",
                "--limit", "10",
                "--dry-run",
                "--apply-classification-result", "/tmp/out.json",
            ]
        )
        assert args.limit == 10
        assert args.dry_run is True
        assert args.apply_classification_result == "/tmp/out.json"


# ── skill-prompt (Phase 2) ────────────────────────────────────────────


class TestSkillPrompt:
    """skill-prompt subcommand — --limit, --apply-skill-result."""

    def test_skill_prompt_defaults(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["skill-prompt"])
        assert args.limit == 5
        assert args.apply_skill_result is None

    def test_skill_prompt_limit(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["skill-prompt", "--limit", "3"])
        assert args.limit == 3

    def test_skill_prompt_apply_skill_result(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["skill-prompt", "--apply-skill-result", "skill.json"])
        assert args.apply_skill_result == "skill.json"

    def test_skill_prompt_combined(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(
            ["skill-prompt", "--limit", "10", "--apply-skill-result", "out.json"]
        )
        assert args.limit == 10
        assert args.apply_skill_result == "out.json"


# ── reply (Phase 3) ───────────────────────────────────────────────────


class TestReply:
    """reply subcommand — --limit."""

    def test_reply_defaults(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["reply"])
        assert args.limit == 5
        assert args._handler == "reply"

    def test_reply_limit(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["reply", "--limit", "3"])
        assert args.limit == 3


# ── status ────────────────────────────────────────────────────────────


class TestStatus:
    """status subcommand — no additional args."""

    def test_status_subcommand(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["status"])
        assert args.command == "status"
        assert args._handler == "status"


# ── reset ─────────────────────────────────────────────────────────────


class TestReset:
    """reset subcommand — requires --force."""

    def test_reset_with_force(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["reset", "--force"])
        assert args.command == "reset"
        assert args.force is True

    def test_reset_requires_force(self) -> None:
        parser = _build_parser()
        with pytest.raises(SystemExit, match="2"):
            parser.parse_args(["reset"])


# ── Error cases ───────────────────────────────────────────────────────


class TestErrors:
    """Error cases: unknown subcommand, missing subcommand."""

    def test_no_command_errors(self) -> None:
        with pytest.raises(SystemExit, match="2"):
            main([])

    def test_unknown_command_errors(self) -> None:
        with pytest.raises(SystemExit, match="2"):
            main(["unknown"])
