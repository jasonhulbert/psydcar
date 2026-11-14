"""Bounded text comparisons for file review."""

from difflib import unified_diff


def text_diff(before: str, proposed: str) -> str | None:
    # Bound the work for long files or pathological line sequences.
    if len(before) + len(proposed) > 128 * 1024:
        return None
    old_lines = before.splitlines(keepends=True)
    new_lines = proposed.splitlines(keepends=True)
    if len(old_lines) + len(new_lines) > 2500:
        return None
    return "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
        for line in unified_diff(old_lines, new_lines, n=3)
    )

