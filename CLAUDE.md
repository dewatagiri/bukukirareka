# BukuKira

Telegram bookkeeping bot for a home-based reseller (beauty/skincare + TikTok/Shopee
affiliate commission). She sends a receipt photo, voice note or text; Gemini extracts
a draft transaction; she confirms with YA/EDIT/BATAL; confirmed entries are appended
to a Google Sheet. See `SETUP.md` (setup steps) and `PANDUAN.md` (Bahasa Malaysia user
guide) for the full picture.

## Files
- `bot.py` — Telegram handlers, weekly job scheduling
- `ai.py` — Gemini prompt and JSON extraction
- `service.py` — confirm/commit flow, reports (glues AI output to store)
- `core.py` — pure functions: stock, cost, profit, report math (no I/O)
- `store.py` — `SheetStore` (Google Sheets) and `MemoryStore` (in-memory, used by tests)
- `test_offline.py` — tests with no API keys needed (`MemoryStore` + fixed clock)

## Environment status (this machine, as of 2026-09-26)
- `.venv` created, `requirements.txt` installed.
- `test_offline.py` passes.
- `.env` filled in (token, Gemini key, Sheet ID, `service_account.json` path,
  `ALLOWED_USER_IDS` already has two Telegram IDs set).
- `service_account.json` in place; Sheet shared with the service account as Editor.
- `bot.py` runs cleanly (Telegram polling + Sheets + weekly-report scheduler all start).
- `.gitignore` added for `.env`, `service_account.json`, `.venv/`.
- Not yet a git repo.

## Gotchas hit during setup (Windows)
- `python -m venv .venv` reported an `ensurepip` error but the venv and pip were
  actually fine — don't assume venv creation failed from that message alone.
- `pip install` crashed with `OSError: ... This drive is locked by BitLocker ...
  'D:\Python314\Scripts'`. Root cause: a stale `D:\Python314\Scripts` entry in the
  system `PATH` (from an old Python install) that pip's post-install
  "is this on PATH" check tries to `Path.resolve()`. All packages had actually
  installed by that point — the check itself is cosmetic. Fix: install with
  `--no-warn-script-location` to skip that check.
- Notepad silently saved `.env` as `.env.txt` (no recognized extension). Watch for
  this if `.env` seems to vanish after manual edits — check for a `.env.txt` sibling.
- `SheetStore` errors are the fastest way to diagnose `.env`/Sheets setup problems:
  404 = wrong `SHEET_ID`, 403 = Sheet not shared with the service account's
  `client_email` (found inside `service_account.json`).

## Running
```
.venv\Scripts\python.exe bot.py
```
Runs in foreground (polling); no separate server/daemon step. For always-on use,
see SETUP.md §6 (Windows Task Scheduler at logon, or a VPS later).

## Limits (v1, see SETUP.md for the full list)
One user/shop, no WhatsApp, expiry tracked per product not per batch, EDIT button
can't undo an already-saved row (fix directly in the Sheet's Transaksi tab instead).
