# bili-core — shared library

**All skills depend on this. Do not duplicate.**

## WHAT IT PROVIDES

| Module | Key exports | Lines |
|--------|-------------|-------|
| `auth` | `get_credentials()`, `Credentials`, `login_flow()` | 396 |
| `http_client` | `BiliHTTPClient`, `DEFAULT_HEADERS` | 200 |
| `signing` | `sign_params()`, `clear_cache()` | 165 |
| `errors` | `AuthError`, `CSRFError`, `RateLimitError`, `BiliAPIError` | 72 |
| `api_base` | `BaseBiliClient` (`_get`, `_signed_get`, `_post`) | 87 |
| `fav` | `FavClient` (list/create/delete/move/copy/clean) | 346 |
| `search` | `SearchClient` | 88 |

## IMPORT PATTERN

```python
from bili_core.auth import get_credentials
from bili_core.http_client import BiliHTTPClient
from bili_core.signing import sign_params
from bili_core.errors import AuthError
```

## KEY BEHAVIORS

- `get_credentials()`: `.auth.json` → env vars → QR code login (auto-saves)
- `BiliHTTPClient`: httpx with Chrome 131 headers, 2s rate-limit, 412/429 auto-retry (3x, 120s), `-101`/`-111` → exception
- `sign_params()`: fetches mixin key (24h cache), returns `{"w_rid": ..., "wts": ...}`
- `FavClient` requires `BiliHTTPClient` + signing callable injected

## DEPENDENCY FLOW

```
fav-organizer → bili-core
dyn-publisher → bili-core
video-analyzer → bili-core
watch-later-recommender → bili-core
at-orchestrator → bili-core
```

## TEST GAPS

No tests for: `api_base.py`, `fav.py`, `search.py`, `__init__.py`.

## ANTI-PATTERNS

- `auth.py:356`: broad `except Exception: pass` (mid lookup, "best-effort")
- `http_client.py:182`: broad `except Exception` with logging (acceptable)
- `http_client.py:158,160`: `# type: ignore[arg-type]` on `kwargs: object`
