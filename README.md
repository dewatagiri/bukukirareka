# BukuKira

A Telegram bot that does bookkeeping for a home-based reseller. Send it a receipt
photo, a voice note, or just type — Gemini reads it, the bot asks "Betul?" (Correct?),
and once confirmed the entry is saved to a Google Sheet. It tracks stock, cost/profit
per item, affiliate commission (TikTok/Shopee), unpaid customers, and sends a weekly
report automatically.

```
Phone (Telegram) ──> bot.py on your PC ──> Gemini (reads photo/voice/text)
                                       └─> Google Sheet (Produk + Transaksi tabs)
```

Built for a Malaysian home-based seller (prompts and replies are in Bahasa Malaysia /
Manglish), but the logic is generic enough to adapt.

## Get started

Setup takes about 30 minutes, once — see **[SETUP.md](SETUP.md)** for the full
step-by-step (Telegram bot token, Gemini API key, Google Sheet + service account,
install, whitelist your user).

For what it's like to actually use day-to-day (in Bahasa Malaysia), see
**[PANDUAN.md](PANDUAN.md)**.

## Files

| File | What it does |
|---|---|
| `bot.py` | Telegram handlers, weekly report scheduling |
| `ai.py` | Gemini prompt and JSON extraction |
| `service.py` | confirm/commit flow, reports — glues AI output to storage |
| `core.py` | pure functions: stock, cost, profit, report math (no I/O, easy to test) |
| `store.py` | `SheetStore` (Google Sheets) and `MemoryStore` (in-memory, used by tests) |
| `test_offline.py` | tests that run with no API keys — `python test_offline.py` |

## Limits (v1)

One user and one shop, no WhatsApp yet, expiry tracked per product not per batch,
the EDIT button can't undo an already-saved row (fix it directly in the Sheet's
Transaksi tab instead), no product photos/catalogue. See SETUP.md for the full list.
