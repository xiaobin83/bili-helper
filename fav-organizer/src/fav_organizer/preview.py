"""Markdown preview formatter for Bilibili favorites organize plan.

Transforms an ``OrganizePlan`` into a structured Markdown document with
seven sections: stats overview, invalid content table, duplicate content
table, classification moves grouped by target folder, new folder list,
empty folder advisory, and a confirmation prompt.
"""

from __future__ import annotations

from collections import defaultdict

from .models import FavoritedItem, Operation, OrganizePlan


def format_preview(plan: OrganizePlan) -> str:
    """Render *plan* as a structured Markdown preview string.

    Parameters
    ----------
    plan:
        The organize plan produced by ``build_plan``.

    Returns
    -------
    str
        Markdown-formatted preview with all seven sections.
    """
    lines: list[str] = []

    # ------------------------------------------------------------------
    # Section 1 — Title + stats summary
    # ------------------------------------------------------------------
    lines.append("# 整理预览\n")
    lines.append("## 概览\n")

    creates = len(plan.folders_to_create)
    total_moves = sum(len(op.resources) for op in plan.moves)
    total_deletions = sum(len(op.resources) for op in plan.deletions)

    lines.append(
        f"共需执行 **{plan.total_operations}** 个操作："
        f"创建 **{creates}** 个文件夹、"
        f"移动 **{total_moves}** 个视频、"
        f"删除 **{total_deletions}** 个失效/重复内容"
    )
    lines.append("")
    lines.append(plan.summary)
    lines.append("")

    # ------------------------------------------------------------------
    # Section 2 — Invalid content table
    # ------------------------------------------------------------------
    invalid_ops = _invalid_deletions(plan.deletions)
    if invalid_ops:
        lines.append("## 失效内容\n")
        lines.append("| 标题 | BV号 | 来源文件夹 |")
        lines.append("|------|------|------------|")
        for op in invalid_ops:
            source_title = op.source.title if op.source else "未知"
            for item in op.resources:
                lines.append(f"| {item.title} | `{item.bvid}` | {source_title} |")
        lines.append("")

    # ------------------------------------------------------------------
    # Section 3 — Duplicate content table
    # ------------------------------------------------------------------
    duplicate_ops = _duplicate_deletions(plan.deletions)
    if duplicate_ops:
        lines.append("## 重复内容\n")
        lines.append("| 标题 | BV号 | 来源文件夹 | 操作 |")
        lines.append("|------|------|------------|------|")
        for op in duplicate_ops:
            source_title = op.source.title if op.source else "默认收藏夹"
            for item in op.resources:
                lines.append(
                    f"| {item.title} | `{item.bvid}` | {source_title} | 删除 |"
                )
        lines.append("")

    # ------------------------------------------------------------------
    # Section 4 — Classification moves grouped by target folder
    # ------------------------------------------------------------------
    if plan.moves:
        lines.append("## 分类移动\n")

        target_groups: dict[str, list[Operation]] = defaultdict(list)
        for op in plan.moves:
            target = (
                op.target
                if isinstance(op.target, str)
                else (op.target.title if op.target else "未分类")
            )
            target_groups[target].append(op)

        for target_title in sorted(target_groups):
            ops = target_groups[target_title]
            item_count = sum(len(op.resources) for op in ops)
            lines.append(f"### → {target_title}（{item_count} 个视频）\n")
            for op in ops:
                source_title = op.source.title if op.source else "未知"
                for item in op.resources:
                    bilibili_url = f"https://www.bilibili.com/video/{item.bvid}"
                    lines.append(
                        f"- [{item.title}]({bilibili_url}) — 来自「{source_title}」"
                    )
            lines.append("")

    # ------------------------------------------------------------------
    # Section 5 — New folders to create
    # ------------------------------------------------------------------
    if plan.folders_to_create:
        lines.append("## 需要创建的文件夹\n")
        for folder_title in plan.folders_to_create:
            lines.append(f"- **{folder_title}**")
        lines.append("")

    # ------------------------------------------------------------------
    # Section 6 — Empty folder advisory
    # ------------------------------------------------------------------
    if plan.empty_folders:
        lines.append("## 空文件夹提醒\n")
        lines.append("以下文件夹整理后将为空，建议手动删除（本次不执行）：\n")
        for folder_title in plan.empty_folders:
            lines.append(f"- {folder_title}")
        lines.append("")

    # ------------------------------------------------------------------
    # Section 7 — Confirmation prompt
    # ------------------------------------------------------------------
    lines.append("---\n")
    lines.append("**是否执行以上操作？(y/n)**")

    return "\n".join(lines)


# ------------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------------


def _invalid_deletions(deletions: list[Operation]) -> list[Operation]:
    """Return deletions whose items are all invalid (attr != 0).

    These correspond to scanner-detected stale/deleted-by-up主 content.
    """
    return [
        op
        for op in deletions
        if op.resources and all(item.attr != 0 for item in op.resources)
    ]


def _duplicate_deletions(deletions: list[Operation]) -> list[Operation]:
    """Return deletions whose items are all valid duplicates (attr == 0).

    These correspond to dedup-detected duplicate content (typically from
    the default favourites folder).
    """
    return [
        op
        for op in deletions
        if op.resources and all(item.attr == 0 for item in op.resources)
    ]
