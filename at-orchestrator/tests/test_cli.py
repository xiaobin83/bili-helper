"""Tests for CLI argument parsing and subcommand routing.

TDD: these tests are written BEFORE the implementation.
Run once to see them fail, then implement main.py to make them pass.

NOTE: Tests verify argument *parsing* only — they do NOT execute handlers
(which would require B站 credentials or a real database).
"""

from __future__ import annotations

import pytest

from at_orchestrator.main import _build_parser, main


# ──────────────────────────────────────────────────────────────────────
# Global arguments
# ──────────────────────────────────────────────────────────────────────


class TestGlobalArgs:
    """Global arguments: --db-path, --auth-file, --env-prefix."""

    def test_default_db_path(self) -> None:
        """--db-path defaults to ~/.bili-helper/at-orchestrator.db."""
        parser = _build_parser()
        args = parser.parse_args(["status"])
        assert args.db_path.endswith(".bili-helper/at-orchestrator.db")

    def test_custom_db_path(self) -> None:
        """--db-path should accept a custom path (parsing succeeds)."""
        parser = _build_parser()
        args = parser.parse_args(["--db-path", "/tmp/test.db", "status"])
        assert args.db_path == "/tmp/test.db"

    def test_auth_file(self) -> None:
        """--auth-file should be accepted (parsing succeeds)."""
        parser = _build_parser()
        args = parser.parse_args(["--auth-file", "/tmp/auth.json", "status"])
        assert args.auth_file == "/tmp/auth.json"

    def test_default_env_prefix(self) -> None:
        """--env-prefix defaults to 'BILI_' (parsing succeeds)."""
        parser = _build_parser()
        args = parser.parse_args(["--env-prefix", "BILI_", "status"])
        assert args.env_prefix == "BILI_"

    def test_custom_env_prefix(self) -> None:
        """--env-prefix should accept a custom prefix (parsing succeeds)."""
        parser = _build_parser()
        args = parser.parse_args(["--env-prefix", "MY_", "status"])
        assert args.env_prefix == "MY_"

    def test_version(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--version should print version and exit."""
        parser = _build_parser()
        with pytest.raises(SystemExit, match="0"):
            parser.parse_args(["--version"])
        captured = capsys.readouterr()
        assert "at-orchestrator" in captured.out


# ──────────────────────────────────────────────────────────────────────
# Subcommand: fetch
# ──────────────────────────────────────────────────────────────────────


class TestFetch:
    """fetch subcommand — no additional args."""

    def test_fetch_subcommand(self) -> None:
        """fetch should be recognized by the parser."""
        parser = _build_parser()
        args = parser.parse_args(["fetch"])
        assert args.command == "fetch"
        assert args._handler == "fetch"


# ──────────────────────────────────────────────────────────────────────
# Subcommand: process
# ──────────────────────────────────────────────────────────────────────


class TestProcess:
    """process subcommand — --limit, --dry-run, --apply-llm-result."""

    def test_process_defaults(self) -> None:
        """process should have correct default values."""
        parser = _build_parser()
        args = parser.parse_args(["process"])
        assert args.limit == 1
        assert args.dry_run is False
        assert args.apply_llm_result is None

    def test_process_limit(self) -> None:
        """--limit should be parsed correctly."""
        parser = _build_parser()
        args = parser.parse_args(["process", "--limit", "5"])
        assert args.limit == 5

    def test_process_dry_run(self) -> None:
        """--dry-run should set dry_run to True."""
        parser = _build_parser()
        args = parser.parse_args(["process", "--dry-run"])
        assert args.dry_run is True

    def test_process_apply_llm_result(self) -> None:
        """--apply-llm-result should accept a file path."""
        parser = _build_parser()
        args = parser.parse_args(["process", "--apply-llm-result", "result.json"])
        assert args.apply_llm_result == "result.json"

    def test_process_combined_args(self) -> None:
        """process should accept all args together."""
        parser = _build_parser()
        args = parser.parse_args(
            [
                "process",
                "--limit",
                "10",
                "--dry-run",
                "--apply-llm-result",
                "/tmp/out.json",
            ]
        )
        assert args.limit == 10
        assert args.dry_run is True
        assert args.apply_llm_result == "/tmp/out.json"


# ──────────────────────────────────────────────────────────────────────
# Subcommand: status
# ──────────────────────────────────────────────────────────────────────


class TestStatus:
    """status subcommand — no additional args."""

    def test_status_subcommand(self) -> None:
        """status should be recognized by the parser."""
        parser = _build_parser()
        args = parser.parse_args(["status"])
        assert args.command == "status"
        assert args._handler == "status"


# ──────────────────────────────────────────────────────────────────────
# Subcommand: reset
# ──────────────────────────────────────────────────────────────────────


class TestReset:
    """reset subcommand — requires --force."""

    def test_reset_with_force(self) -> None:
        """reset --force should be accepted by the parser."""
        parser = _build_parser()
        args = parser.parse_args(["reset", "--force"])
        assert args.command == "reset"
        assert args.force is True

    def test_reset_requires_force(self) -> None:
        """reset without --force should error."""
        parser = _build_parser()
        with pytest.raises(SystemExit, match="2"):
            parser.parse_args(["reset"])


# ──────────────────────────────────────────────────────────────────────
# Error cases
# ──────────────────────────────────────────────────────────────────────


class TestErrors:
    """Error cases: unknown subcommand, missing subcommand."""

    def test_no_command_errors(self) -> None:
        """Running without a subcommand should error."""
        with pytest.raises(SystemExit, match="2"):
            main([])

    def test_unknown_command_errors(self) -> None:
        """Running with an unknown subcommand should error."""
        with pytest.raises(SystemExit, match="2"):
            main(["unknown"])
