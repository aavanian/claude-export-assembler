"""End-to-end tests driving claude_export.py as a command.

Every part is placed in the downloads folder beforehand, so the script never
proposes opening links unless a test asks for that path explicitly.
"""

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "claude_export.py"
CREATED_AT = "2026-01-02T03:04:05+00:00"
EXPORT_NAME = "claude-export-2026-01-02-03-04-05"


def make_part(downloads, filename, members):
    with zipfile.ZipFile(downloads / filename, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, json.dumps(content))


def write_manifest(path, parts):
    entries = [
        {
            "batch_index": i,
            "category": category,
            "part": 1,
            "filename": filename,
            "export_url": f"https://claude.invalid/export/x/download/{i}",
        }
        for i, (category, filename) in enumerate(parts)
    ]
    manifest = {"created_at": CREATED_AT, "total_files": len(entries), "data_files": entries}
    path.write_text(json.dumps(manifest))
    return path


def run(*args, stdin=subprocess.DEVNULL, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
        stdin=stdin,
        env=env,
    )


@pytest.fixture
def dirs(tmp_path):
    names = ("downloads", "staging", "backup")
    paths = {name: tmp_path / name for name in names}
    for path in paths.values():
        path.mkdir()
    return paths


@pytest.fixture
def full_export(tmp_path, dirs):
    make_part(dirs["downloads"], "conv-1.zip", {"conversations.json": [{"uuid": "c1"}]})
    make_part(dirs["downloads"], "conv-2.zip", {"conversations.json": [{"uuid": "c2"}]})
    make_part(dirs["downloads"], "meta.zip", {"users.json": [{"uuid": "u1"}]})
    make_part(
        dirs["downloads"],
        "projects.zip",
        {"projects/p1.json": {"uuid": "p1"}, "projects/p2.json": {"uuid": "p2"}},
    )
    make_part(dirs["downloads"], "memories.zip", {"memories/acct.json": {"m": 1}})
    return write_manifest(
        tmp_path / "manifest.json",
        [
            ("conversations", "conv-1.zip"),
            ("conversations", "conv-2.zip"),
            ("light_metadata", "meta.zip"),
            ("projects", "projects.zip"),
            ("memories", "memories.zip"),
        ],
    )


def stage_args(manifest, dirs):
    return [manifest, "--dest", dirs["staging"], "--downloads-dir", dirs["downloads"], "--wait", 5]


def test_assemble_rebuilds_legacy_layout(full_export, dirs):
    result = run(*stage_args(full_export, dirs), "--assemble", "--backup-dir", dirs["backup"])
    assert result.returncode == 0, result.stderr

    out = dirs["backup"] / EXPORT_NAME
    conversations = json.loads((out / "conversations.json").read_text())
    assert [c["uuid"] for c in conversations] == ["c1", "c2"]
    assert json.loads((out / "users.json").read_text()) == [{"uuid": "u1"}]
    projects = json.loads((out / "projects.json").read_text())
    assert sorted(p["uuid"] for p in projects) == ["p1", "p2"]
    assert json.loads((out / "memories/acct.json").read_text()) == {"m": 1}


def test_rerun_reuses_verified_parts(full_export, dirs):
    assert run(*stage_args(full_export, dirs)).returncode == 0
    moved_manifest = dirs["staging"] / full_export.name

    result = run(*stage_args(moved_manifest, dirs))

    assert result.returncode == 0, result.stderr
    assert "parts reused     : 5" in result.stdout


def test_html_part_is_rejected(tmp_path, dirs):
    (dirs["downloads"] / "conv-1.zip").write_text("<!DOCTYPE html><html></html>")
    manifest = write_manifest(tmp_path / "manifest.json", [("conversations", "conv-1.zip")])

    result = run(*stage_args(manifest, dirs))

    assert result.returncode != 0
    assert "web page, not export data" in result.stderr


def test_previous_export_is_archived(full_export, dirs):
    old = dirs["backup"] / "claude-export-2025-01-01-00-00-00"
    old.mkdir()
    (old / "conversations.json").write_text("[]")

    result = run(
        *stage_args(full_export, dirs), "--assemble", "--backup-dir", dirs["backup"], "--yes"
    )

    assert result.returncode == 0, result.stderr
    assert not old.exists()
    assert zipfile.ZipFile(old.with_suffix(".zip")).namelist() == ["conversations.json"]


def test_assemble_requires_a_backup_dir(full_export, dirs):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_EXPORT_BACKUP_DIR"}

    result = run(*stage_args(full_export, dirs), "--assemble", env=env)

    assert result.returncode != 0
    assert "--backup-dir" in result.stderr
    assert not any(dirs["staging"].iterdir()), "nothing should be staged before failing"


def test_backup_dir_from_environment(full_export, dirs):
    env = {**os.environ, "CLAUDE_EXPORT_BACKUP_DIR": str(dirs["backup"])}

    result = run(*stage_args(full_export, dirs), "--assemble", env=env)

    assert result.returncode == 0, result.stderr
    assert (dirs["backup"] / EXPORT_NAME / "conversations.json").exists()


def test_missing_parts_propose_opener_without_revealing_urls(tmp_path, dirs):
    manifest = write_manifest(tmp_path / "manifest.json", [("conversations", "conv-1.zip")])
    opener = {"darwin": "open", "linux": "xdg-open"}[sys.platform]

    result = run(*stage_args(manifest, dirs))

    assert result.returncode != 0, "no terminal to confirm on, so it must stop"
    assert f"| xargs -n1 {opener}" in result.stdout
    assert "claude.invalid" not in result.stdout + result.stderr
