# claude-export-assembler

[![check](https://github.com/aavanian/claude-export-assembler/actions/workflows/check.yml/badge.svg)](https://github.com/aavanian/claude-export-assembler/actions/workflows/check.yml)
[![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)
![Platforms: macOS | Linux](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg)

Stages, verifies and reassembles a claude.ai data export from the
`manifest.json` format that replaced the single `conversations.json` ZIP.

Tools written for the older export format expect a flat folder holding
`conversations.json`, `projects.json` and `users.json`. Exports now arrive as a
manifest pointing at several per-category ZIP parts instead. This script
collects those parts, checks them, and rebuilds the flat layout.

*This is an unofficial tool, not affiliated with or endorsed by Anthropic.*

## Requirements

- [uv](https://docs.astral.sh/uv/) to run the script directly, or Python 3.11+
  (standard library only).
- [`jq`](https://jqlang.org/), used by the command that hands the links to the
  browser.
- macOS (`open`) or Linux (`xdg-open`). Elsewhere the script asks you to open
  the links yourself.
- A browser signed in to claude.ai.

## Usage

```
./claude_export.py <manifest.json> [--assemble] [--downloads-dir DIR] [--dest DIR]
                   [--backup-dir DIR] [--wait SECS] [--yes]
```

1. Request a data export from claude.ai and save its `manifest.json`.
2. Run the script on it. It proposes a command that opens every part's link in
   your browser, and asks before running it.
3. The parts land in the downloads folder. The script moves them into a staging
   directory and verifies them.

Without `--assemble` it stops after staging and verifying the parts. With it,
the flat layout is rebuilt and installed into the backup directory. Any export
already there is archived as a sibling `.zip` first.

| Option | Default | Purpose |
| --- | --- | --- |
| `--assemble` | off | Rebuild the flat layout and install it |
| `--backup-dir DIR` | `$CLAUDE_EXPORT_BACKUP_DIR` | Where `--assemble` installs the export; required for `--assemble` |
| `--downloads-dir DIR` | `~/Downloads` | Where the browser saves the parts |
| `--dest DIR` | `claude-export-*` in the system temp dir | Staging directory |
| `--wait SECS` | `600` | How long to wait for parts to arrive |
| `--yes` | off | Answer every prompt with yes |

Re-running is safe. Parts already staged and unchanged are not verified
again, and links are opened only if some part is neither staged nor in the
downloads folder.

## Why the browser does the downloading

The manifest's `export_url`s are **not** presigned object-store links. They
point at `claude.ai/export/<id>/download/<token>` and are served only to a
logged-in session. Fetched without one they return HTTP 200 and an HTML page —
the marketing site or the app shell, depending on routing — never export data.
No token is spent in that case.

So the script never fetches. It prints a one-liner that pipes the URLs from the
manifest straight into the browser, asks before running it, and then collects
what lands in the downloads folder. URLs are treated as credentials: they are
never printed, logged, or written to any file the script creates.

Routing through a SOCKS proxy makes this worse, not better — a non-residential
exit draws a Cloudflare interstitial (`403`, "Just a moment...") before auth is
even considered.

Note that `HTTP_PROXY`/`HTTPS_PROXY` in the environment will capture these
requests unless `NO_PROXY` covers `claude.ai`; this is easy to mistake for an
expired or rejected link.

## Integrity

The manifest carries **no size, checksum, etag, content-type or expiry** for any
part — only `batch_index`, `category`, `part`, `filename` and the URL. The
script says so at runtime rather than implying verification it cannot do.

What it can check: each part's own ZIP CRCs, plus a recorded sha256 so later
runs confirm staged files are untouched and skip re-verifying them. Parts that
are HTML rather than a ZIP are diagnosed explicitly.

## Layout mapping

| Manifest category | Contents | Legacy target |
| --- | --- | --- |
| `conversations` | `conversations.json` (array) | `conversations.json` |
| `light_metadata` | `users.json` (array) | `users.json` |
| `light_metadata` | `login_history.json` | *no equivalent* — kept as-is |
| `projects` | one object per `projects/<uuid>.json` | gathered into `projects.json` |
| `memories` | `memories/<account>.json` | *no equivalent* — kept as-is |
| `feedback` | `reflections/<account>.json` | *no equivalent* — kept as-is |

Arrays are concatenated in manifest `batch_index` order, so sharded parts merge
correctly if a future export splits a category across several files. Anything
with no legacy equivalent is written alongside at its original path and listed
in the closing summary.

## Development

```
./check.sh
```

Formats and lints with ruff, then runs the end-to-end tests with pytest, all
through uv. With `CI` set it only reports formatting and lint problems instead
of fixing them; that is how the GitHub workflow runs it, on macOS and Linux
with Python 3.11 and 3.14.

## License

[GPL-3.0-or-later](LICENSE).
