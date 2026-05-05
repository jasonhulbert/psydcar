from pathlib import Path

from psydecar.scanner import SkipReason, scan_files, should_include_file


def test_scan_files_skips_binary_oversized_and_ignored_directories(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "keep.md").write_text("# Keep\n", encoding="utf-8")
    (tmp_path / "docs" / "skip.bin").write_bytes(b"\0\1\2")
    (tmp_path / "docs" / "large.txt").write_text("x" * 128, encoding="utf-8")
    (tmp_path / "docs" / "image.png").write_bytes(b"png")
    (tmp_path / "docs" / "node_modules").mkdir()
    (tmp_path / "docs" / "node_modules" / "package.md").write_text(
        "# ignored\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / ".git").mkdir()
    (tmp_path / "docs" / ".git" / "config").write_text("[core]\n", encoding="utf-8")

    candidates = list(scan_files(tmp_path / "docs", max_file_size_bytes=64))

    assert [candidate.relative_path for candidate in candidates] == [Path("keep.md")]
    assert candidates[0].extension == ".md"
    assert candidates[0].size_bytes == len("# Keep\n")


def test_should_include_file_reports_binary_skip_reason(tmp_path):
    binary_path = tmp_path / "data.txt"
    binary_path.write_bytes(b"hello\0world")

    result = should_include_file(binary_path)

    assert result.included is False
    assert result.reason == SkipReason.BINARY


def test_should_include_file_reports_oversized_skip_reason(tmp_path):
    oversized_path = tmp_path / "large.md"
    oversized_path.write_text("abcdef", encoding="utf-8")

    result = should_include_file(oversized_path, max_file_size_bytes=5)

    assert result.included is False
    assert result.reason == SkipReason.OVERSIZED


def test_should_include_file_reports_extension_skip_reason(tmp_path):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"not really a png")

    result = should_include_file(image_path)

    assert result.included is False
    assert result.reason == SkipReason.EXTENSION
