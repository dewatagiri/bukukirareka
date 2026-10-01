# BukuKira v1 — setup (for Azman)

A Telegram bot for the wife. She sends a receipt photo, voice note or text. Gemini reads it and the bot asks "Betul?". Once she taps YA, the entry is saved to a Google Sheet. It tracks stock, profit per item, affiliate commission and unpaid customers, and sends a report every Sunday night.

```
Phone (Telegram) ──> bot.py on your PC ──> Gemini (reads photo/voice/text)
                                       └─> Google Sheet (Produk + Transaksi tabs)
```

Setup takes about 30 minutes, once.

## 1. Telegram bot token (2 min)
1. In Telegram, open **@BotFather** and send `/newbot`.
2. Name it `BukuKira`. Give it a username such as `bukukira_<something>_bot`.
3. Copy the token into `TELEGRAM_BOT_TOKEN`.

## 2. Gemini API key (2 min)
1. Go to https://aistudio.google.com and choose **Get API key**, then create a key.
2. Copy it into `GEMINI_API_KEY`.

The default model is `gemini-3.8-flash`. For her volume the cost should be tiny. Check current rates on the Gemini pricing page. `gemini-3.5-flash-lite` is cheaper but less accurate on handwritten notas.

## 3. Google Sheet + service account (15 min)
1. https://console.cloud.google.com: create a project named `bukukira`.
2. **APIs & Services → Enable APIs**: turn on **Google Sheets API**.
3. **IAM & Admin → Service Accounts → Create**. No roles are needed. Open the account, go to **Keys → Add key → JSON**, and save the file as `service_account.json` in the bot folder.
4. Create a blank Google Sheet in your Drive, e.g. "BukuKira – [wife's name]".
5. **Share** the Sheet with the service account's email (`...@...iam.gserviceaccount.com`) as **Editor**.
6. Copy the Sheet ID from the URL (`docs.google.com/spreadsheets/d/<THIS PART>/edit`) into `SHEET_ID`.
7. Also share the Sheet with her Google account so she can open it in the Google Sheets app.

On first run the bot creates the **Produk** and **Transaksi** tabs with headers.

## 4. Install and run
```bash
cd bukukira
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # then edit .env
python test_offline.py          # should end with ALL TESTS PASSED
python bot.py
```

## 5. Whitelist her
1. Leave `ALLOWED_USER_IDS` empty at first, run the bot, and have her send `/start`. The bot replies with her Telegram ID.
2. Put her ID (and yours) in `.env`, comma-separated, then restart the bot.

Nobody else can use the bot.

## 6. Keep it running
- **Easiest:** in the bot folder run `powershell -ExecutionPolicy Bypass -File install_autostart.ps1` once. It creates the "BukuKira" task (starts at log on, no window) which runs `run_forever.py`; that restarts the bot 15 s after any crash. Output goes to `bot.log`. To stop it: `Stop-ScheduledTask BukuKira` (and `Unregister-ScheduledTask BukuKira` to remove it).
- **Manual:** your PC must be on. Use Windows Task Scheduler with the trigger "At log on", action `.venv\Scripts\pythonw.exe bot.py`, and "Start in" set to the bot folder.
- **Better (later):** a small VPS (~RM20/month), or run it inside the Hermes setup.
- If the PC is off, her messages wait in Telegram. They're processed when the bot starts again, for up to about 24 hours.

## The Sheet

**Produk** tab. She can edit this directly in the Sheets app:

| Column | Meaning |
|---|---|
| Nama | Product name |
| Nama lain | Other names she uses, comma-separated, e.g. `serum, vit c` |
| Kos seunit | What she pays her friend per unit. The bot updates this as a weighted average when she restocks at a different price |
| Harga jual | Normal selling price. Used when she doesn't say a price |
| Stok awal | Stock she already had before starting the bot |
| Stok semasa | Calculated by the bot. Don't edit |
| Tarikh luput | Nearest expiry date (YYYY-MM-DD). The report warns 60 days before |
| Aktif | Set `TIDAK` to hide a discontinued product |

She can add products three ways:
- Type or say *"produk baru Toner Rose kos 18 jual 35"*.
- Add a row in the Sheet.
- Just sell or restock something new. The bot adds it automatically.

**Transaksi** tab: one row per entry. She can fix mistakes here directly. Stock and reports are recalculated from this tab every time.

## How the numbers work
- **Untung bersih** (net profit) = (sales − cost of the items sold) + affiliate commission − other expenses.
- **Aliran tunai** (cash flow) = real money in minus real money out, including stock purchases. Profit and cash flow will differ. Buying stock upfront shows up here, and that's the point of the report.
- **Stok dalam tangan** = units on hand × cost. This is the cash tied up in stock.
- A sale marked *Belum bayar* counts as profit but not as cash in, until she says the customer has paid.

## Upgrading from v1
Just pull and restart. On start the bot adds three columns to the end of the Transaksi
tab (`Dibayar (RM)`, `Batal`, `Kumpulan`); existing rows stay as they are. Optionally add
the new `DEBT_REMINDER_*` / `STATE_FILE` lines from `.env.example` (the defaults work).

## Limits in v1.1 (candidates for v2)
- One user and one shop. The bot runs in Telegram only; WhatsApp is used just for sending
  payment reminders to customers (a link she taps, not an integration).
- Expiry is tracked per product, not per batch.
- A payment counts as cash in the period of the original sale, not the date the customer paid.
- Undo (`batal yang tadi` / `/batal`) marks the last saved entry `Batal = YA`. Undoing a
  restock doesn't roll back the average cost in the Produk tab; check it by hand.
- No product photos or catalogue.

## Files
- `bot.py`: Telegram handlers, weekly job
- `ai.py`: Gemini prompt and extraction
- `service.py`: confirm/commit flow, reports
- `core.py`: stock, cost, profit and report maths (pure Python)
- `store.py`: Google Sheets (and in-memory for tests)
- `test_offline.py`: tests that run without any API keys
