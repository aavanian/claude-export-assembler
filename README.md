# claude-downloader

Stages and reassembles a claude.ai data export from the `manifest.json` format
that replaced the single `conversations.json` ZIP.

```
./claude_export.py <manifest.json> [--assemble] [--downloads-dir DIR] [--dest DIR]
                   [--backup-dir DIR] [--wait SECS] [--yes]
```

Without `--assemble` it stages and verifies the parts only. With it, the legacy
flat layout is rebuilt and installed into
`~/Claude Export Backup`, archiving any export already
there as a sibling `.zip`.

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
