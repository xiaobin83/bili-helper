"""Tests for CLI argument parsing and subcommand routing.

TDD: these tests are written BEFORE the implementation.
Run once to see them fail, then implement main.py to make them pass.

NOTE: main()'s argv parameter expects args *without* the program name
(i.e. sys.argv[1:] equivalent). So call main(["status"]) not main(["prog", "status"]).
"""

from __future__ import annotations

import pytest

from at_orchestrator.main import main


# ──────────────────────────────────────────────────────────────────────
# Global arguments
# ──────────────────────────────────────────────────────────────────────


class TestGlobalArgs:
    """Global arguments: --db-path, --auth-file, --env-prefix."""

    def test_default_db_path(self) -> None:
        """--db-path defaults to '.at-orchestrator/tasks.db' (parsing succeeds)."""
        main(["--db-path", ".at-orchestrator/tasks.db", "status"])

    def test_custom_db_path(self) -> None:
        """--db-path should accept a custom path (parsing succeeds)."""
        main(["--db-path", "/tmp/test.db", "status"])

    def test_auth_file(self) -> None:
        """--auth-file should be accepted (parsing succeeds)."""
        main(["--auth-file", "/tmp/auth.json", "status"])

    def test_default_env_prefix(self) -> None:
        """--env-prefix defaults to 'BILI_' (parsing succeeds)."""
        main(["--env-prefix", "BILI_", "status"])

    def test_custom_env_prefix(self) -> None:
        """--env-prefix should accept a custom prefix (parsing succeeds)."""
        main(["--env-prefix", "MY_", "status"])

    def test_version(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--version should print version and exit."""
        with pytest.raises(SystemExit, match="0"):
            main(["--version"])
        captured = capsys.readouterr()
        assert "at-orchestrator" in captured.out


# ──────────────────────────────────────────────────────────────────────
# Subcommand: fetch
# ──────────────────────────────────────────────────────────────────────


class TestFetch:
    """fetch subcommand — no additional args."""

    def test_fetch_routes(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fetch should print a stub message."""
        main(["fetch"])
        captured = capsys.readouterr()
        assert "fetch" in captured.out
        assert "not yet implemented" in captured.out.lower()


# ──────────────────────────────────────────────────────────────────────
# Subcommand: process
# ──────────────────────────────────────────────────────────────────────


class TestProcess:
    """process subcommand — --limit, --dry-run, --apply-llm-result."""

    def test_process_routes(self, capsys: pytest.CaptureFixture[str]) -> None:
        """process should print a stub message with default values."""
        main(["process"])
        captured = capsys.readouterr()
        assert "process" in captured.out.lower()
        assert "limit=1" in captured.out
        assert "dry_run=False" in captured.out or "dry-run:" in captured.out

    def test_process_limit(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--limit should be accepted."""
        main(["process", "--limit", "5"])
        captured = capsys.readouterr()
        assert "limit=5" in captured.out

    def test_process_dry_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--dry-run should set dry_run to True."""
        main(["process", "--dry-run"])
        captured = capsys.readouterr()
        assert "dry_run=True" in captured.out or "dry-run" in captured.out.lower()

    def test_process_apply_llm_result(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--apply-llm-result should accept a file path."""
        main(["process", "--apply-llm-result", "result.json"])
        captured = capsys.readouterr()
        assert "result.json" in captured.out

    def test_process_combined_args(self, capsys: pytest.CaptureFixture[str]) -> None:
        """process should accept all args together."""
        main([
            "process",
            "--limit", "10",
            "--dry-run",
            "--apply-llm-result", "/tmp/out.json",
        ])
        captured = capsys.readouterr()
        assert "limit=10" in captured.out
        assert "dry_run=True" in captured.out or "dry-run" in captured.out.lower()
        assert "/tmp/out.json" in captured.out

    def test_process_limit_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--limit should default to 1."""
        main(["process"])
        captured = capsys.readouterr()
        assert "limit=1" in captured.out

    def test_process_apply_llm_default_none(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--apply-llm-result should be None by default."""
        main(["process"])
        captured = capsys.readouterr()
        assert "None" in captured.out


# ──────────────────────────────────────────────────────────────────────
# Subcommand: status
# ──────────────────────────────────────────────────────────────────────


class TestStatus:
    """status subcommand — no additional args."""

    def test_status_routes(self, capsys: pytest.CaptureFixture[str]) -> None:
        """status should print a stub message."""
        main(["status"])
        captured = capsys.readouterr()
        assert "status" in captured.out
        assert "not yet implemented" in captured.out.lower()


# ──────────────────────────────────────────────────────────────────────
# Subcommand: reset
# ──────────────────────────────────────────────────────────────────────


class TestReset:
    """reset subcommand — requires --force."""

    def test_reset_with_force(self, capsys: pytest.CaptureFixture[str]) -> None:
        """reset --force should print a stub message."""
        main(["reset", "--force"])
        captured = capsys.readouterr()
        assert "reset" in captured.out
        assert "not yet implemented" in captured.out.lower()

    def test_reset_requires_force(self) -> None:
        """reset without --force should error."""
        with pytest.raises(SystemExit, match="2"):
            main(["reset"])


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
