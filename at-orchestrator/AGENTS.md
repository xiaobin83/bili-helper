# at-orchestrator — @-mention orchestration

Newest, most complex skill. Monitors B站 @-mentions and replies, classifies intent via LLM, dispatches to other skills, posts replies.

## CLI

6 subcommands: `fetch`, `process`, `skill-prompt`, `reply`, `status`, `reset`.

## PIPELINE

```
fetch (cursor-paginated from B站 API)
  → process (classify intent → dispatch skill → generate reply)
    → reply (post comment or send PM back)
```

State machine persisted in SQLite (`task` table with `TaskStatus` enum).

## STRUCTURE

```
src/at_orchestrator/
├── main.py         # CLI entry (482 lines, 6 subcommands)
├── constants.py    # DB path, SKILL_CLI_MAP (skill→CLI command mapping)
├── models.py       # Pydantic models + TaskStatus enum
├── db.py           # SQLite database layer (411 lines)
├── fetcher.py      # B站 message fetcher (cursor-based pagination)
├── classifier.py   # LLM classification prompt builder (457 lines)
├── dispatcher.py   # Subprocess skill execution
├── processor.py    # Main orchestration logic (403 lines)
└── replier.py      # Reply routing (comment vs PM)
tests/              # 15 files, 313 test functions, 5,414 LOC
```

## DB LIFECYCLE

`Task` rows: `pending` → `classifying` → `dispatched` → `replying` → `done`.
Each step persists state; supports crash recovery via `status` and `reset`.

## KEY DESIGN DECISIONS

- **Cursor-based pagination** in fetcher (not offset/page) — follows B站 API pattern
- **LLM called twice per message**: once for intent classification, once for skill response generation
- **Skills dispatched as subprocesses** (not Python imports) — isolation, each skill has its own deps
- **SKILL_CLI_MAP** in `constants.py` maps intent → CLI command to run

## ANTI-PATTERNS

- 14 of 19 repo-wide `# type: ignore[...]` are here — type narrowing issues with `Optional[AtFetcher]`, loosely-typed `client` parameter
- `processor.py:324,390`: broad `except Exception:` (session check, LLM parse fallback)
- `main.py`: 6x `type: ignore[union-attr]` on `fetcher` object (not narrowed after Optional check)
