# fav-organizer — favorites management

Largest skill (1,025-line CLI). 5 subcommands: `classify`, `plan`, `execute`, `delete-empty`, `list`.

## PIPELINE

```
classify → (Agent classifies each item via LLM) → plan → execute
```

- `classify`: Scans a folder, builds prompt for LLM to categorize each item
- `plan`: Generates an operation plan from classification results
- `execute`: Applies the plan (destructive — requires confirmation)

## STRUCTURE

```
src/
├── main.py           # CLI entry (1025 lines — needs splitting)
├── fav_api.py        # Model-returning wrapper around bili-core FavClient
├── scanner.py        # Invalid content scanner
├── dedup.py          # Duplicate detection
├── planner.py        # Plan generation from classification
├── executor.py       # Plan execution
├── models.py         # Pydantic models (Folder, FavoritedItem)
├── state_manager.py  # State file I/O
├── preview.py        # Markdown preview generation
├── confirm.py        # Interactive confirmation
└── tests/            # 15 files, 310+ cases
```

## NON-STANDARD: FLAT `src/` PACKAGE

Unlike all other skills (which use `src/<package_name>/` namespace), this skill installs as flat `import src` via `pyproject.toml: packages = ["src"]`.
- All internal imports: `from src.models import ...`, `from src.fav_api import ...`
- **Do not follow this pattern** for new skills — use `src/<package_name>/` instead.

## TEST COVERAGE

Good: 15 test files, 310+ test cases, pytest-cov configured.
Has `test_placeholder.py` (stub) and orphan `.pyc` for 3 deleted test files.

## ANTI-PATTERNS

- `main.py`: 1025 lines (4x over 250 LOC guideline)
- `fav_api.py`: Legacy pre-bili-core client, now wraps `bili_core.fav.FavClient`
