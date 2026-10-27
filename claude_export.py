#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Stage and assemble a claude.ai data export from its manifest.

The export URLs are single-use credentials tied to a logged-in browser
session, so the browser does the fetching: this script opens the links,
collects what lands in the downloads folder, verifies it, and rebuilds the
legacy flat export layout. URLs are never printed or written anywhere.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

BACKUP_DIR_ENV = "CLAUDE_EXPORT_BACKUP_DIR"
STATE_FILE = "download-state.json"
SETTLE_SECONDS = 2

# Extracted paths that map onto the legacy flat layout. A bare filename is a
# JSON array concatenated across parts; a directory name is a tree of one
# object per file, gathered into a single array.
LEGACY_FILES = {"conversations.json": "conversations.json", "users.json": "users.json"}
LEGACY_DIRS = {"projects": "projects.json"}


def log(msg=""):
    print(msg, flush=True)


def die(msg):
    raise SystemExit(f"\nFATAL: {msg}")


def sha256_of(path):
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def zip_is_sound(path):
    """The only integrity signal available: the archive's own CRCs."""
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.testzip() is None
    except (zipfile.BadZipFile, OSError):
        return False


def confirm(prompt, assume_yes):
    if assume_yes:
        log(f"{prompt} [auto-yes]")
        return True
    if not sys.stdin.isatty():
        die(f"{prompt}\nNo terminal to ask on; re-run with --yes to proceed.")
    return input(f"{prompt} [y/N] ").strip().lower() in {"y", "yes"}


def export_name(manifest):
    stamp = datetime.fromisoformat(manifest["created_at"]).astimezone(UTC)
    return f"claude-export-{stamp:%Y-%m-%d-%H-%M-%S}"


def open_links(manifest_path, assume_yes):
    """Hand the single-use URLs straight to the browser, never to the terminal."""
    command = f"jq -r '.data_files[].export_url' {json.dumps(str(manifest_path))} | xargs -n1 open"
    log("The export links need your logged-in browser session. Proposed command:")
    log(f"\n    {command}\n")
    log("It pipes the URLs from the manifest straight into the browser; they are")
    log("never shown here. Each link works once, so run this only when ready.")
    if not confirm("Run it now?", assume_yes):
        log("Skipped. Place the parts in the downloads folder yourself, then re-run.")
        return False
    result = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True)
    if result.returncode != 0:
        die(f"opening the links failed (exit {result.returncode}): {result.stderr.strip()}")
    log("Links handed to the browser.")
    return True


def settled_size(path):
    """Size of a file that has stopped growing, else None."""
    try:
        first = path.stat().st_size
    except OSError:
        return None
    time.sleep(SETTLE_SECONDS)
    try:
        second = path.stat().st_size
    except OSError:
        return None
    return second if first == second and second > 0 else None


def collect(entries, downloads_dir, parts_dir, wait):
    """Move each expected part out of the downloads folder into staging."""
    pending = {e["filename"] for e in entries if not (parts_dir / e["filename"]).exists()}
    if not pending:
        return
    deadline = time.monotonic() + wait
    log(f"\nCollecting {len(pending)} part(s) from {downloads_dir} …")
    announced = set()
    while pending:
        for name in sorted(pending):
            source = downloads_dir / name
            if not source.exists():
                continue
            if settled_size(source) is None:
                if name not in announced:
                    log(f"  [wait] {name} still downloading …")
                    announced.add(name)
                continue
            shutil.move(str(source), parts_dir / name)
            log(f"  [move] {name} -> staging")
            pending.discard(name)
        if pending and time.monotonic() > deadline:
            missing = "\n  ".join(sorted(pending))
            die(
                f"timed out after {wait}s waiting for:\n  {missing}\n"
                f"Download them into {downloads_dir} keeping these exact names, "
                "then re-run."
            )
        if pending:
            time.sleep(1)


def verify(entries, parts_dir, export_dir):
    """Check every part and record what was seen, so re-runs can skip them."""
    state_path = export_dir / STATE_FILE
    state = json.loads(state_path.read_text()) if state_path.exists() else {"parts": {}}

    log(
        "\nIntegrity: the manifest carries NO size, checksum, etag or content-type.\n"
        "           Each part is checked against its own ZIP CRCs; a sha256 is\n"
        "           recorded so re-runs can confirm the files are untouched."
    )
    verified, reused, total = [], [], 0
    for entry in entries:
        name = entry["filename"]
        target = parts_dir / name
        if not target.exists():
            die(f"{name} is missing from {parts_dir}.")
        known = state["parts"].get(name)
        size = target.stat().st_size

        if known and known["bytes"] == size and known["sha256"] == sha256_of(target):
            log(f"  [ok  ] {name} ({size:,} B, unchanged since last run)")
            reused.append(name)
            total += size
            continue

        head = target.open("rb").read(64).lstrip().lower()
        if head.startswith((b"<!doctype html", b"<html")):
            die(
                f"{name} is a claude.ai web page, not export data ({size:,} B of "
                "HTML).\nThe browser was not signed in when it followed the link; "
                "request a fresh export."
            )
        if not zip_is_sound(target):
            die(f"{name} is not a valid ZIP ({size:,} B). Request a fresh export.")

        digest = sha256_of(target)
        state["parts"][name] = {
            "bytes": size,
            "sha256": digest,
            "category": entry["category"],
            "part": entry["part"],
            "batch_index": entry["batch_index"],
            "verified_at": datetime.now(UTC).isoformat(),
        }
        log(f"  [ok  ] {name} ({size:,} B, ZIP CRCs pass, sha256 {digest[:16]}…)")
        verified.append(name)
        total += size

    state_path.write_text(json.dumps(state, indent=2) + "\n")
    return verified, reused, total


def stage(manifest, manifest_path, export_dir, downloads_dir, wait, assume_yes):
    parts_dir = export_dir / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    entries = sorted(manifest["data_files"], key=lambda e: e["batch_index"])

    log(f"Export created {manifest['created_at']}")
    log(f"{len(entries)} parts declared (total_files={manifest.get('total_files')})")
    log(f"Staging in {export_dir}")

    have = [e["filename"] for e in entries if (parts_dir / e["filename"]).exists()]
    in_downloads = [e["filename"] for e in entries if (downloads_dir / e["filename"]).exists()]
    if len(have) + len(in_downloads) < len(entries):
        open_links(manifest_path, assume_yes)
    else:
        log("\nEvery part is already staged or waiting in the downloads folder;")
        log("not opening any links.")

    collect(entries, downloads_dir, parts_dir, wait)
    verified, reused, total = verify(entries, parts_dir, export_dir)

    resting = export_dir / manifest_path.name
    if manifest_path.resolve() != resting.resolve():
        shutil.move(str(manifest_path), resting)
        log(f"\nManifest moved into {export_dir.name}/")
    return verified, reused, total


def extract(entries, parts_dir, raw_dir):
    """Unpack each part into its own directory, in manifest order.

    Sharded parts of one category carry the same member names, so they must
    not share a directory. Returns (entry, extracted root) pairs.
    """
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    extracted = []
    for entry in entries:
        root = raw_dir / entry["category"] / Path(entry["filename"]).stem
        root.mkdir(parents=True)
        with zipfile.ZipFile(parts_dir / entry["filename"]) as zf:
            for member in zf.namelist():
                if member.startswith("/") or ".." in Path(member).parts:
                    die(f"{entry['filename']} contains an unsafe path: {member}")
            zf.extractall(root)
        extracted.append((entry, root))
    return extracted


def assemble(manifest, export_dir):
    entries = sorted(manifest["data_files"], key=lambda e: e["batch_index"])
    parts_dir = export_dir / "parts"
    raw_dir = export_dir / "raw"
    out_dir = export_dir / export_name(manifest)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    merged = {}
    unmapped = []
    for entry, root in extract(entries, parts_dir, raw_dir):
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            rel = path.relative_to(root)
            top = rel.parts[0]
            if len(rel.parts) == 1 and top in LEGACY_FILES:
                data = json.loads(path.read_text())
                merged.setdefault(LEGACY_FILES[top], []).extend(
                    data if isinstance(data, list) else [data]
                )
            elif top in LEGACY_DIRS:
                data = json.loads(path.read_text())
                merged.setdefault(LEGACY_DIRS[top], []).extend(
                    data if isinstance(data, list) else [data]
                )
            else:
                destination = out_dir / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
                unmapped.append(f"{entry['category']}: {rel}")

    for name, records in sorted(merged.items()):
        (out_dir / name).write_text(json.dumps(records, ensure_ascii=False, indent=2))

    for name in sorted(set(LEGACY_FILES.values()) | set(LEGACY_DIRS.values())):
        if name not in merged:
            log(f"  [note] no data for legacy {name}; not written")

    counts = {name: len(records) for name, records in sorted(merged.items())}
    return out_dir, counts, unmapped


def install(out_dir, backup_dir, assume_yes):
    """Move the assembled export in, archiving any export already there."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stale = [
        p
        for p in sorted(backup_dir.iterdir())
        if p.is_dir() and p.name != out_dir.name and p.name.startswith("claude-export-")
    ]
    for old in stale:
        archive = backup_dir / f"{old.name}.zip"
        if archive.exists():
            log(f"  [note] {archive.name} already exists; leaving {old.name} in place")
            continue
        if not confirm(f"Zip existing export {old.name} and remove the folder?", assume_yes):
            log(f"  [skip] {old.name} left as-is")
            continue
        shutil.make_archive(str(archive.with_suffix("")), "zip", root_dir=old)
        if not zip_is_sound(archive):
            die(f"archiving {old.name} produced a bad ZIP; nothing was removed")
        shutil.rmtree(old)
        log(f"  [arch] {old.name} -> {archive.name} ({archive.stat().st_size:,} B)")

    final = backup_dir / out_dir.name
    if final.exists():
        shutil.rmtree(final)
    shutil.move(str(out_dir), final)
    log(f"  [put ] {final}")
    return final


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("manifest", type=Path)
    ap.add_argument(
        "--dest",
        type=Path,
        help="staging directory (default: a claude-export-* folder under the system temp dir)",
    )
    ap.add_argument(
        "--downloads-dir",
        type=Path,
        default=Path.home() / "Downloads",
        help="where the browser puts the parts (default: ~/Downloads)",
    )
    ap.add_argument(
        "--wait",
        type=int,
        default=600,
        help="seconds to wait for parts to arrive (default: 600)",
    )
    ap.add_argument("--assemble", action="store_true", help="rebuild the legacy layout")
    ap.add_argument(
        "--backup-dir",
        type=Path,
        default=os.environ.get(BACKUP_DIR_ENV),
        help=f"where --assemble installs the export (default: ${BACKUP_DIR_ENV})",
    )
    ap.add_argument("--yes", action="store_true", help="answer every prompt with yes")
    args = ap.parse_args()
    if args.assemble and args.backup_dir is None:
        ap.error(f"--assemble needs --backup-dir or ${BACKUP_DIR_ENV}")

    if not args.manifest.exists():
        die(f"manifest not found: {args.manifest}")
    manifest = json.loads(args.manifest.read_text())
    export_dir = args.dest or Path(tempfile.gettempdir()) / export_name(manifest)

    verified, reused, total = stage(
        manifest, args.manifest, export_dir, args.downloads_dir, args.wait, args.yes
    )

    counts, unmapped, final = {}, [], None
    if args.assemble:
        log("\nAssembling legacy layout …")
        out_dir, counts, unmapped = assemble(manifest, export_dir)
        for name, n in counts.items():
            log(f"  [make] {name} ({n:,} records)")
        log(f"\nInstalling into {args.backup_dir} …")
        final = install(out_dir, args.backup_dir, args.yes)

    log("\n=== summary ===")
    log(f"parts verified   : {len(verified)}")
    log(f"parts reused     : {len(reused)} (unchanged since a previous run)")
    log(f"bytes staged     : {total:,}")
    log(f"conversations    : {counts.get('conversations.json', 'n/a (not assembled)')}")
    log(f"projects         : {counts.get('projects.json', 'n/a (not assembled)')}")
    log(f"users            : {counts.get('users.json', 'n/a (not assembled)')}")
    if args.assemble:
        log(f"kept alongside   : {len(unmapped)} file(s) with no legacy equivalent")
        for item in unmapped:
            log(f"    {item}")
        log(f"location         : {final}")
    else:
        log(f"staged at        : {export_dir}  (re-run with --assemble to rebuild)")


if __name__ == "__main__":
    main()
