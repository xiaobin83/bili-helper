# Fav Target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--target fav` and `--topic` options to watch-later-recommender, letting LLM choose or create a favorites folder for recommended videos.

**Architecture:** Extend existing BiliAPIClient with favorites CRUD methods. Extend LLM prompt with folder list + optional topic. Fallback matches by partition distribution to folder names. CLI flags route to toview or fav execution paths.

**Tech Stack:** Python 3.12+, bili-core (auth, http_client, signing), httpx, pydantic

---

### Task 1: models.py — Add Folder model, extend RecommendationResult

**Files:**
- Modify: `watch-later-recommender/src/watch_later_recommender/models.py`

- [ ] **Step 1: Add Folder model and extend RecommendationResult**

```python
# Add after PrefsConfig class

class Folder(BaseModel):
    """A B站 favorites folder."""

    model_config = ConfigDict(extra="ignore")

    id: int              # media_id, used in API calls
    fid: int = 0
    mid: int = 0
    attr: int = 0
    title: str           # folder name
    media_count: int = 0 # number of items in folder


class RecommendationResult(BaseModel):
    """LLM recommendation output — N videos with reasons (N=1..10).
    
    When ``target_action`` is ``"create_new"``, a new folder will be
    created with the given ``target_folder`` name and description.
    """

    model_config = ConfigDict(extra="ignore")

    bvids: list[str] = Field(..., min_length=1, max_length=10)
    reasons: list[str] = Field(..., min_length=1, max_length=10)
    surprise_count: int = 0
    target_action: str = "toview"  # "toview" | "add_to_existing" | "create_new"
    target_folder: str = ""        # folder name for existing or new
    folder_description: str = ""   # description when creating new

    def __init__(self, **data):
        super().__init__(**data)
        if len(self.bvids) != len(self.reasons):
            raise ValueError("bvids and reasons must have same length")
```

- [ ] **Step 2: Remove old docstring that says "exactly 5 videos"**

The previous docstring said `exactly 5 videos` — the new one above already replaces it. No extra step needed.

- [ ] **Step 3: Commit**

```bash
git add watch-later-recommender/src/watch_later_recommender/models.py
git commit -m "feat(watch-later-recommender): add Folder model, extend RecommendationResult with target fields"
```

---

### Task 2: api_client.py — Add favorites API methods

**Files:**
- Modify: `watch-later-recommender/src/watch_later_recommender/api_client.py`

- [ ] **Step 1: Add import of Folder model and sign_params**

```python
from bili_core.signing import sign_params
from watch_later_recommender.models import Folder, VideoItem
```

- [ ] **Step 2: Add Wbi-signed GET helper and bili_jct to constructor**

Modify `__init__` to also store `bili_jct` and add a `_signed_get` helper:

```python
def __init__(self, creds: Credentials | None = None) -> None:
    sessdata = creds.sessdata if creds else ""
    bili_jct = creds.bili_jct if creds else ""
    buvid3 = creds.buvid3 if creds else ""
    self._has_auth = bool(creds and creds.sessdata)
    self._bili_jct = bili_jct
    self._client = BiliHTTPClient(
        sessdata=sessdata,
        bili_jct=bili_jct,
        buvid3=buvid3,
        min_interval=2.0,
    )

async def _signed_get(self, path: str, params: dict | None = None) -> dict:
    """GET request with Wbi signature. Requires auth."""
    raw_params = dict(params or {})
    signed = sign_params(raw_params)
    return await self._client.get(f"{BASE_URL}{path}", params=signed)
```

- [ ] **Step 3: Add list_fav_folders()**

```python
async def list_fav_folders(self, up_mid: int) -> list[Folder]:
    """Fetch all favorites folders for the given user. Requires auth + Wbi signing.

    Args:
        up_mid: The user's numeric mid.

    Returns:
        List of Folder objects. Empty list if no auth or API error.
    """
    if not self._has_auth:
        logger.info("list_fav_folders: skipped (no auth)")
        return []

    try:
        raw = await self._signed_get(
            "/x/v3/fav/folder/created/list-all",
            {"up_mid": up_mid},
        )
        if raw.get("code") != 0:
            logger.warning("list_fav_folders: code=%s", raw.get("code"))
            return []
        data = raw.get("data")
        if not data or not isinstance(data, dict):
            return []
        folder_dicts = data.get("list", []) or []
        return [Folder(**f) for f in folder_dicts]
    except Exception as e:
        logger.warning("list_fav_folders failed: %s", e)
        return []
```

- [ ] **Step 4: Add add_to_fav_folder()**

```python
async def add_to_fav_folder(self, aid: int, add_media_ids: list[int]) -> dict:
    """Add a video to one or more favorites folders. Requires auth.

    Args:
        aid: Video avid to add.
        add_media_ids: Target folder's media_id(s).

    Returns:
        Response dict with ``code`` and ``message`` keys.
    """
    if not self._has_auth:
        return {"code": -1, "message": "未登录，无法添加到收藏夹"}

    try:
        raw = await self._client.post(
            f"{BASE_URL}/x/v3/fav/resource/add",
            data={
                "resources": f"{aid}:2",  # type=2 for video
                "add_media_ids": ",".join(str(m) for m in add_media_ids),
            },
        )
        return {"code": raw.get("code", -1), "message": raw.get("message", "")}
    except Exception as e:
        logger.warning("add_to_fav_folder failed: %s", e)
        return {"code": -1, "message": str(e)}
```

- [ ] **Step 5: Add create_fav_folder()**

```python
async def create_fav_folder(self, name: str, intro: str = "", privacy: int = 0) -> dict:
    """Create a new favorites folder. Requires auth.

    Args:
        name: Folder title (2-6 Chinese characters recommended).
        intro: Optional description.
        privacy: 0 = public, 1 = private.

    Returns:
        Response dict with ``code``, ``message``, and optionally ``data.media_id``.
    """
    if not self._has_auth:
        return {"code": -1, "message": "未登录，无法创建收藏夹"}

    try:
        raw = await self._client.post(
            f"{BASE_URL}/x/v3/fav/folder/add",
            data={"title": name, "intro": intro, "privacy": privacy},
        )
        return {
            "code": raw.get("code", -1),
            "message": raw.get("message", ""),
            "data": raw.get("data") or {},
        }
    except Exception as e:
        logger.warning("create_fav_folder failed: %s", e)
        return {"code": -1, "message": str(e)}
```

- [ ] **Step 6: Commit**

```bash
git add watch-later-recommender/src/watch_later_recommender/api_client.py
git commit -m "feat(watch-later-recommender): add favorites API methods (list, add, create) with Wbi signing"
```

---

### Task 3: recommender.py — Extend prompt, add fav pipeline

**Files:**
- Modify: `watch-later-recommender/src/watch_later_recommender/recommender.py`

- [ ] **Step 1: Add imports and constants**

```python
from watch_later_recommender.models import Folder, PrefsConfig, RecommendationResult, VideoItem
from watch_later_recommender.api_client import BiliAPIClient
```

Add a constant for max folders to include in prompt:

```python
MAX_FOLDERS_IN_PROMPT = 30
```

- [ ] **Step 2: Add fetch_folders() function**

```python
async def fetch_folders(client: BiliAPIClient, up_mid: int) -> list[Folder]:
    """Fetch user's favorites folders for context in LLM prompt."""
    folders = await client.list_fav_folders(up_mid)
    # Sort by media_count descending, keep most relevant
    folders.sort(key=lambda f: f.media_count, reverse=True)
    return folders[:MAX_FOLDERS_IN_PROMPT]
```

- [ ] **Step 3: Update RECOMMENDATION_PROMPT_TEMPLATE**

Replace the template and add a fav variant:

```python
RECOMMENDATION_PROMPT_TEMPLATE = """你是一个B站视频推荐助手。请根据用户的偏好配置，从以下候选视频中精选{count}个推荐给用户。

## 用户偏好
{preferences_text}

{folders_section}
{topic_section}
## 推荐要求
1. {surprise_text}的视频应为"惊喜内容"（来自用户偏好分区之外的视频）
2. 每个推荐请给出具体理由（结合用户偏好和视频内容）
3. 拒绝广告/推广内容（rcmd_reason含"广告"、"推广"等关键词的不要选）

## 候选视频列表
{candidates_text}

## 输出格式
请严格按以下JSON格式输出，不要包含其他文字：
{output_format}
务必输出{count}个bvid和{count}个理由，数量必须一致。"""

TOVIEW_OUTPUT_FORMAT = """{{"bvids": ["BV...", "BV...", ...], "reasons": ["理由1", "理由2", ...], "surprise_count": N}}"""

FAV_OUTPUT_FORMAT = """{{"bvids": ["BV...", "BV...", ...], "reasons": ["理由1", "理由2", ...], "surprise_count": N, "target_action": "add_to_existing"|"create_new", "target_folder": "收藏夹名称", "folder_description": "新建收藏夹的简介（仅create_new时需要）"}}"""
```

- [ ] **Step 4: Update build_llm_prompt() signature and implementation**

```python
def build_llm_prompt(
    candidates: list[VideoItem],
    prefs: PrefsConfig | None = None,
    count: int = 5,
    target: str = "toview",
    folders: list[Folder] | None = None,
    topic: str = "",
) -> str:
    """Build the LLM recommendation prompt.

    Args:
        candidates: Deduplicated, filtered candidate list (capped at MAX_LLM_CANDIDATES).
        prefs: User preference config. When None, uses empty defaults.
        count: Number of videos to recommend (default 5, max 10).
        target: "toview" or "fav".
        folders: User's favorites folders (for target="fav").
        topic: Optional topic keyword to amplify in selection.

    Returns:
        Formatted prompt string ready for LLM consumption.
    """
    prefs = prefs or PrefsConfig()

    # Build preferences text
    pref_lines = []
    if prefs.categories:
        for cat in prefs.categories:
            kw = f", 关键词: {cat.keywords}" if cat.keywords else ""
            pref_lines.append(f"- {cat.name} (分区ID: {cat.tids}{kw})")
    if prefs.exclude_categories:
        for cat in prefs.exclude_categories:
            pref_lines.append(f"- 排除: {cat.name} (分区ID: {cat.tids})")
    if prefs.max_duration:
        pref_lines.append(f"- 最大时长: {prefs.max_duration}秒")

    surprise_pct = max(0, min(50, int(prefs.surprise_ratio * 100)))
    surprise_text = f"最多{surprise_pct}%"
    if not pref_lines:
        pref_lines.append("无特定偏好（从所有类型中精选）")
        surprise_text = "0%"

    # Build folders section (for fav target)
    folders_section = ""
    if target == "fav" and folders:
        folder_lines = []
        for f in folders:
            folder_lines.append(f"- {f.title} ({f.media_count} 个视频)")
        folders_section = (
            "## 用户收藏夹\n"
            "用户有以下收藏夹可供选择（名称 | 现有视频数）：\n"
            + "\n".join(folder_lines)
            + "\n"
        )

    # Build topic section
    topic_section = ""
    if topic:
        topic_section = f"## 本次推荐主题\n用户本次特别关注: "{topic}"\n请优先从候选视频中筛选与"{topic}"相关的内容。\n"

    # Build candidate list
    candidate_batch = candidates[:MAX_LLM_CANDIDATES]
    candidate_lines = []
    for i, v in enumerate(candidate_batch, 1):
        dur = f"{v.duration // 60}分{v.duration % 60}秒" if v.duration else "未知时长"
        candidate_lines.append(
            f"{i}. [{v.bvid}] {v.title[:60]}"
            f" | 分区: {v.tname}"
            f" | UP主: {v.owner_name}"
            f" | 播放: {v.view} 点赞: {v.like}"
            f" | 时长: {dur}"
        )

    output_format = FAV_OUTPUT_FORMAT if target == "fav" else TOVIEW_OUTPUT_FORMAT

    return RECOMMENDATION_PROMPT_TEMPLATE.format(
        count=count,
        preferences_text="\n".join(pref_lines),
        folders_section=folders_section,
        topic_section=topic_section,
        surprise_text=surprise_text,
        candidates_text="\n".join(candidate_lines),
        output_format=output_format,
    )
```

- [ ] **Step 5: Add determine_fav_target() for fallback mode**

```python
def determine_fav_target(
    selected_bvids: list[str],
    candidates: list[VideoItem],
    folders: list[Folder],
    prefs: PrefsConfig,
) -> tuple[str, str, str]:
    """Fallback: determine target folder from selected videos.

    Stats partition distribution of selected videos, matches against
    pref categories, then looks for a folder whose name contains the
    matching category name. Falls back to creating a new folder.

    Args:
        selected_bvids: List of selected video bvids.
        candidates: Full candidate list (for looking up VideoItem by bvid).
        folders: User's favorites folders.
        prefs: User preference config.

    Returns:
        Tuple of (action, folder_name, folder_description).
        action is "add_to_existing" or "create_new".
    """
    # Find the VideoItems for selected bvids
    lookup = {v.bvid: v for v in candidates}
    selected = [lookup[b] for b in selected_bvids if b in lookup]

    # Count partition occurrences
    tid_counts: dict[int, int] = {}
    for v in selected:
        tid_counts[v.tid] = tid_counts.get(v.tid, 0) + 1

    if not tid_counts:
        return "add_to_existing", "默认收藏夹", ""

    # Find dominant tid
    dominant_tid = max(tid_counts, key=tid_counts.get)  # type: ignore[arg-type]

    # Find matching category name from prefs
    match_name = ""
    for cat in prefs.categories:
        if dominant_tid in cat.tids:
            match_name = cat.name
            break

    if not match_name:
        # Fallback: use the first selected video's tname
        match_name = selected[0].tname if selected else "默认收藏夹"

    # Try to find an existing folder whose name contains match_name
    for f in folders:
        if match_name in f.title or f.title in match_name:
            return "add_to_existing", f.title, ""

    # No match found — create new
    new_name = f"{match_name}精选"
    new_desc = f"由智能推荐自动创建的{match_name}精选收藏夹"
    return "create_new", new_name, new_desc
```

- [ ] **Step 6: Update add_recommendations() to support fav target**

Replace the function with:

```python
async def add_recommendations(
    client: BiliAPIClient,
    recommendations: list[dict[str, Any]],
    target: str = "toview",
    toview_count: int = 0,
    target_folder: str = "",
    target_action: str = "add_to_existing",
    folders: list[Folder] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Phase 6: Add recommendations to target (toview or favorites folder).

    Args:
        client: Authenticated BiliAPIClient.
        recommendations: List of dicts with bvid, aid, title, reason.
        target: "toview" or "fav".
        toview_count: Current items in watch-later (for target="toview").
        target_folder: Name of target folder (for target="fav").
        target_action: "add_to_existing" or "create_new" (for target="fav").
        folders: Full folder list (for looking up media_id by name).
        dry_run: When True, skip actual API calls.

    Returns:
        Dict with keys: success, added, failed, message.
    """
    result: dict[str, Any] = {
        "success": False,
        "added": 0,
        "failed": [],
        "message": "",
    }

    if dry_run:
        result["success"] = True
        msg = f"干跑模式完成，推荐了 {len(recommendations)} 个视频"
        if target == "fav":
            msg += f"，目标收藏夹: {target_folder}"
        result["message"] = msg
        return result

    if target == "toview":
        return await _add_to_toview(client, recommendations, toview_count)
    else:
        return await _add_to_fav(client, recommendations, target_action, target_folder, folders)


async def _add_to_toview(
    client: BiliAPIClient,
    recommendations: list[dict[str, Any]],
    toview_count: int,
) -> dict[str, Any]:
    """Add recommendations to watch-later list."""
    result: dict[str, Any] = {
        "success": False, "added": 0, "failed": [], "message": "",
    }

    if toview_count >= TOVIEW_WARN_THRESHOLD:
        result["message"] = (
            f"稍后再看列表空间不足（{toview_count}/{TOVIEW_CAPACITY_LIMIT}），"
            f"请先清理后再试"
        )
        return result

    for rec in recommendations:
        resp = await client.add_to_toview(rec["aid"])
        if resp.get("code") == 0:
            rec["status"] = "added"
            result["added"] += 1
        elif resp.get("code") == 90001:
            result["message"] = "稍后再看列表已满，部分视频未能添加"
            break
        else:
            rec["status"] = "failed"
            result["failed"].append(rec["bvid"])

    result["success"] = result["added"] > 0
    if not result["message"]:
        parts = [f"已将 {result['added']} 个视频添加到稍后再看"]
        if result["failed"]:
            parts.append(f"，{len(result['failed'])} 个失败")
        result["message"] = "".join(parts)
    return result


async def _add_to_fav(
    client: BiliAPIClient,
    recommendations: list[dict[str, Any]],
    target_action: str,
    target_folder: str,
    folders: list[Folder] | None,
) -> dict[str, Any]:
    """Add recommendations to a favorites folder (create new if needed)."""
    result: dict[str, Any] = {
        "success": False, "added": 0, "failed": [], "message": "",
    }

    # Resolve media_id
    media_id: int | None = None
    if target_action == "create_new":
        resp = await client.create_fav_folder(
            name=target_folder,
            intro=f"由智能推荐自动创建的收藏夹: {target_folder}",
        )
        if resp.get("code") != 0:
            result["message"] = f"创建收藏夹失败: {resp.get('message', '')}"
            return result
        data = resp.get("data") or {}
        media_id = data.get("media_id")
        if not media_id:
            result["message"] = "创建收藏夹后未获取到 media_id"
            return result
        result["message"] = f"已创建新收藏夹「{target_folder}」"
    else:
        # Find media_id by folder name
        if folders:
            for f in folders:
                if f.title == target_folder:
                    media_id = f.id
                    break
        if not media_id:
            result["message"] = f"未找到名为「{target_folder}」的收藏夹"
            return result

    # Add videos one by one
    for rec in recommendations:
        resp = await client.add_to_fav_folder(rec["aid"], [media_id])
        if resp.get("code") == 0:
            rec["status"] = "added"
            result["added"] += 1
        else:
            rec["status"] = "failed"
            result["failed"].append(rec["bvid"])

    result["success"] = result["added"] > 0
    suffix = f"到收藏夹「{target_folder}」"
    parts = [f"已将 {result['added']} 个视频添加{suffix}"]
    if result["failed"]:
        parts.append(f"，{len(result['failed'])} 个失败")
    result["message"] = "".join(parts)
    return result
```

- [ ] **Step 7: Commit**

```bash
git add watch-later-recommender/src/watch_later_recommender/recommender.py
git commit -m "feat(watch-later-recommender): extend pipeline with fav target, folder prompt, topic injection, fallback matching"
```

---

### Task 4: main.py — Add --target and --topic CLI args, route execution

**Files:**
- Modify: `watch-later-recommender/src/watch_later_recommender/main.py`

- [ ] **Step 1: Add new imports**

```python
from watch_later_recommender.models import Folder
from watch_later_recommender.recommender import (
    TOVIEW_CAPACITY_LIMIT,
    TOVIEW_WARN_THRESHOLD,
    add_recommendations,
    build_llm_prompt,
    determine_fav_target,
    fetch_candidates,
    fetch_folders,
    fallback_selection,
    parse_llm_result,
)
```

- [ ] **Step 2: Add --target and --topic arguments**

```python
parser.add_argument(
    "--target",
    type=str,
    default="toview",
    choices=["toview", "fav"],
    help="推荐目标: toview(稍后再看,默认) / fav(收藏夹)",
)
parser.add_argument(
    "--topic",
    type=str,
    default="",
    help="临时偏好主题（可选），LLM 会优先筛选相关视频",
)
```

Update the argument parser description:

```python
parser = argparse.ArgumentParser(
    description="B站 智能推荐 — 从热门/排行/推荐中精选视频，可添加到稍后再看或收藏夹",
)
```

- [ ] **Step 3: Route _main() based on --target**

Replace the execution section (from `# Get toview list count` to end of `_main()`) with:

```python
        # Route based on target
        if args.target == "fav":
            return await _run_fav_flow(args, client, candidates, counts, prefs, creds)
        else:
            return await _run_toview_flow(args, client, candidates, counts, prefs)

    except Exception as e:
        print(f"❌ 运行错误: {e}")
        return 1


async def _run_toview_flow(
    args: argparse.Namespace,
    client: BiliAPIClient,
    candidates: list,
    counts: dict,
    prefs: PrefsConfig,
) -> int:
    """Execute toview target flow (existing behavior)."""
    # Build prompt
    prompt = build_llm_prompt(candidates, prefs, count=args.count, target="toview", topic=args.topic)
    print(f"📝 LLM Prompt ({len(prompt)} chars):")
    print(prompt)
    print()
    print("=" * 60)
    print()

    # For dry-run, show what would be added
    if args.dry_run:
        result = fallback_selection(candidates, count=args.count)
        _print_results(candidates, counts, result.bvids, result.reasons)
        return 0

    # Get toview list count for capacity check
    toview_list = await client.fetch_toview_list()
    toview_count = len(toview_list)

    if toview_count >= TOVIEW_WARN_THRESHOLD:
        print(
            f"❌ 稍后再看列表空间不足（{toview_count}/{TOVIEW_CAPACITY_LIMIT}），"
            f"请先清理后再试"
        )
        return 4

    result = fallback_selection(candidates, count=args.count)

    rec_dicts = [
        {"bvid": bvid, "aid": next((v.aid for v in candidates if v.bvid == bvid), 0), "title": "", "reason": reason}
        for bvid, reason in zip(result.bvids, result.reasons)
    ]
    add_result = await add_recommendations(
        client, rec_dicts, target="toview", toview_count=toview_count, dry_run=args.dry_run
    )

    _print_results(candidates, counts, result.bvids, result.reasons)
    _print_message(add_result)
    return 0 if add_result.get("success") or add_result.get("added") else 4


async def _run_fav_flow(
    args: argparse.Namespace,
    client: BiliAPIClient,
    candidates: list,
    counts: dict,
    prefs: PrefsConfig,
    creds: Credentials | None,
) -> int:
    """Execute fav target flow."""
    # Get user mid
    # bili-core currently doesn't expose mid; fallback to fetching folders without it
    # Actually list_fav_folders needs up_mid. Get it from the user's space.
    # For now, we fetch folders without mid and try to get it from the API.

    # Fetch folders for context
    mid = creds.mid if creds and hasattr(creds, "mid") and creds.mid else 0
    folders: list[Folder] = []
    if mid:
        folders = await fetch_folders(client, mid)

    # Build prompt with folder context
    prompt = build_llm_prompt(
        candidates, prefs, count=args.count,
        target="fav", folders=folders, topic=args.topic,
    )
    print(f"📝 LLM Prompt ({len(prompt)} chars):")
    print(prompt)
    print()
    print("=" * 60)
    print()

    # Fallback selection for CLI mode
    result = fallback_selection(candidates, count=args.count)

    # Determine target folder (fallback)
    target_action, target_name, target_desc = determine_fav_target(
        result.bvids, candidates, folders, prefs,
    )

    if args.dry_run:
        _print_results(candidates, counts, result.bvids, result.reasons)
        action_label = "新建" if target_action == "create_new" else "已有"
        print(f"📁 目标收藏夹: {action_label}「{target_name}」")
        return 0

    rec_dicts = [
        {"bvid": bvid, "aid": next((v.aid for v in candidates if v.bvid == bvid), 0), "title": "", "reason": reason}
        for bvid, reason in zip(result.bvids, result.reasons)
    ]
    add_result = await add_recommendations(
        client, rec_dicts,
        target="fav",
        target_action=target_action,
        target_folder=target_name,
        folders=folders,
        dry_run=args.dry_run,
    )

    _print_results(candidates, counts, result.bvids, result.reasons)
    _print_message(add_result)
    return 0 if add_result.get("success") or add_result.get("added") else 4


def _print_message(add_result: dict) -> None:
    """Print result message."""
    if add_result.get("success"):
        print(f"✅ {add_result.get('message', '操作成功')}")
    else:
        if add_result.get("message"):
            print(f"⚠️ {add_result['message']}")
```

- [ ] **Step 4: Update _print_results to show target info when fav**

Add to `_print_results`:
```python
def _print_results(
    candidates: list,
    counts: dict,
    selected_bvids: list[str],
    reasons: list[str],
    target_info: str | None = None,
) -> None:
    # ... existing content ...
    if target_info:
        print(f"📁 {target_info}")
        print()
```

(Also update callers to pass `target_info` when applicable.)

- [ ] **Step 5: Handle creds.mid — add mid field to Credentials or fetch folders differently**

Since `Credentials` from bili-core may not have `mid`, add a helper to get current user mid:

```python
async def _get_current_mid(client: BiliAPIClient) -> int:
    """Get current user's mid from their space."""
    try:
        raw = await client._client.get(
            f"{BASE_URL}/x/web-interface/nav"
        )
        data = raw.get("data") or {}
        return data.get("mid") or data.get("mid_plus") or 0
    except Exception:
        return 0
```

This requires `_client` to be accessible. Since `_client` is a private attribute, we can either make it accessible or call this inside `BiliAPIClient` as a method.

Better: Add `get_current_mid()` to `BiliAPIClient`:

```python
# In api_client.py
async def get_current_mid(self) -> int:
    """Get current user's numeric mid. Requires auth."""
    if not self._has_auth:
        return 0
    try:
        raw = await self._client.get(f"{BASE_URL}/x/web-interface/nav")
        data = raw.get("data") or {}
        return data.get("mid") or data.get("mid_plus") or 0
    except Exception as e:
        logger.warning("get_current_mid failed: %s", e)
        return 0
```

- [ ] **Step 6: Commit**

```bash
git add watch-later-recommender/src/watch_later_recommender/main.py \
      watch-later-recommender/src/watch_later_recommender/api_client.py
git commit -m "feat(watch-later-recommender): add --target fav and --topic CLI args, route execution to fav flow"
```

---

### Task 5: Verify — LSP diagnostics and dry-run test

- [ ] **Step 1: Check LSP diagnostics**

```bash
cd watch-later-recommender && uv run pyright src/
```

Expected: No new errors (pre-existing bili-core import errors are OK).

- [ ] **Step 2: Dry-run toview (should work as before)**

```bash
cd watch-later-recommender && uv run watch-later-recommender --dry-run --count 3
```

Expected: Recommends 3 videos, prints results, no actual add.

- [ ] **Step 3: Dry-run fav**

```bash
cd watch-later-recommender && uv run watch-later-recommender --dry-run --target fav --count 3
```

Expected: Recommends 3 videos, prints target folder info, no actual add.

- [ ] **Step 4: Dry-run fav with topic**

```bash
cd watch-later-recommender && uv run watch-later-recommender --dry-run --target fav --topic "拳击" --count 3
```

Expected: Prompt contains "拳击" topic section, recommends 3 videos, no actual add.

- [ ] **Step 5: Commit any fixes**

```bash
git add -A && git commit -m "fix: address lint and runtime issues from fav-target implementation"
```
