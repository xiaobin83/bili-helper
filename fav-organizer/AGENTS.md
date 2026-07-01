# fav-organizer — favorites management

5 subcommands: `classify`, `plan`, `execute`, `delete-empty`, `list`.
All state files stored under `~/.bili-helper/fav-organizer/` (not in project tree).

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
└── fav_organizer/
    ├── main.py           # CLI entry + command handlers (732 LOC)
    ├── fav_api.py        # Model-returning wrapper around bili-core FavClient
    ├── scanner.py        # Invalid content scanner
    ├── dedup.py          # Duplicate detection
    ├── planner.py        # Plan generation from classification
    ├── executor.py       # Plan execution (execute_plan + execute_plan_file)
    ├── pipeline.py       # Multi-batch state management & cleanup
    ├── models.py         # Pydantic models (Folder, FavoritedItem, …)
    ├── state_manager.py  # State file I/O under ~/.bili-helper/fav-organizer/
    ├── preview.py        # Markdown preview generation (2 formats)
    ├── confirm.py        # Interactive confirmation
    ├── video_api.py      # Video info API with disk-backed 30-day cache
    └── tests/            # 14 files, 274+ cases
```

## TEST COVERAGE

Good: 14 test files, 274 test cases, pytest-cov configured.
All pre-existing stub `test_placeholder.py` and orphan `.pyc` files removed.

## OBSERVATIONS

- `main.py`: 732 lines (still ~3x over 250 LOC guideline, but improved from 1025)
- `fav_api.py`: Thin model-returning wrapper around `bili_core.fav.FavClient`
