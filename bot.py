"""BukuKira - Telegram bot. Run:  python bot.py"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, PicklePersistence, filters)

from ai import Extractor
from core import rm
from service import BukuKira
from store import SheetStore

load_dotenv()
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bukukira")

TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Kuala_Lumpur"))
ALLOWED = {int(x) for x in os.getenv("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x}
REPORT_DAY = int(os.getenv("WEEKLY_REPORT_DAY", "0"))  # PTB: 0=Sun, 1=Mon ... 6=Sat
REPORT_TIME = os.getenv("WEEKLY_REPORT_TIME", "21:00")
REMIND_EVERY = int(os.getenv("DEBT_REMINDER_DAYS", "7"))  # nudge at 7, 14, 21... days; 0 = off
REMIND_TIME = os.getenv("DEBT_REMINDER_TIME", "10:00")
STATE_FILE = os.getenv("STATE_FILE", "bukukira_state.pickle")

store = SheetStore(os.getenv("GOOGLE_CREDS_JSON", "service_account.json"), os.environ["SHEET_ID"])
ai = Extractor(os.environ["GEMINI_API_KEY"], os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
               os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite"))
app_logic = BukuKira(store, lambda: datetime.now(TZ))

KEYBOARD = InlineKeyboardMarkup([[
    InlineKeyboardButton("✅ YA", callback_data="ya"),
    InlineKeyboardButton("✏️ EDIT", callback_data="edit"),
    InlineKeyboardButton("❌ BATAL", callback_data="batal"),
]])

HELP = """📒 BukuKira - cara guna

Hantar apa-apa satu:
📸 Gambar resit / screenshot DuitNow / skrin payout TikTok-Shopee
🎙 Voice note
⌨️ Taip

Contoh:
• jual 2 serum kat Kak Lina RM90, belum bayar
• beli stok 10 serum RM25 seunit
• komisen TikTok RM320
• pos J&T RM8.50
• Kak Lina dah bayar / Mira bayar RM50 dulu
• 1 serum pecah / bagi 2 toner sampel
• produk baru Toner Rose kos 18 jual 35
• batal yang tadi
• laporan / laporan minggu ni / stok / hutang

Saya akan tanya "Betul?" - tekan YA, EDIT atau BATAL.

Arahan: /laporan /minggu /stok /hutang /batal /bantuan"""


def allowed(update: Update) -> bool:
    u = update.effective_user
    return bool(u and u.id in ALLOWED)


async def deny(update: Update):
    uid = update.effective_user.id if update.effective_user else "?"
    await update.effective_message.reply_text(
        f"Bot ni peribadi. ID Telegram anda: {uid}\n(Minta admin masukkan ID ni dalam ALLOWED_USER_IDS.)")


def debt_buttons(debts: list[dict]) -> InlineKeyboardMarkup | None:
    """One button per customer: opens WhatsApp with a ready reminder. She picks the contact."""
    if not debts:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        f"📲 Ingatkan {d['nama']} ({rm(d['baki'])})", url=app_logic.whatsapp_link(d))]
        for d in debts[:10]])


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await deny(update)
    await update.message.reply_text("Hai! Saya BukuKira, pembantu buku akaun awak.\n\n" + HELP)


async def simple(update: Update, fn):
    if not allowed(update):
        return await deny(update)
    text = await asyncio.to_thread(fn)
    await update.message.reply_text(text)


async def cmd_help(update, ctx):
    await simple(update, lambda: HELP)


async def cmd_laporan(update, ctx):
    await simple(update, lambda: app_logic.report("bulan"))


async def cmd_minggu(update, ctx):
    await simple(update, lambda: app_logic.report("minggu"))


async def cmd_stok(update, ctx):
    await simple(update, app_logic.stock)


async def cmd_hutang(update, ctx):
    if not allowed(update):
        return await deny(update)
    text, debts = await asyncio.to_thread(lambda: (app_logic.debts(), app_logic.debt_list()))
    await update.message.reply_text(text, reply_markup=debt_buttons(debts))


async def cmd_batal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await deny(update)
    reply, pending = await asyncio.to_thread(app_logic.prepare_undo)
    if pending:
        ctx.chat_data.pop("editing", None)
        ctx.chat_data["pending"] = pending
    await update.message.reply_text(reply, reply_markup=KEYBOARD if pending else None)


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await deny(update)
    msg = update.message
    media, mime, text, source = None, None, None, "teks"
    if msg.photo:
        f = await msg.photo[-1].get_file()
        media, mime, source = bytes(await f.download_as_bytearray()), "image/jpeg", "gambar"
        text = msg.caption
    elif msg.document and (msg.document.mime_type or "").startswith("image/"):
        f = await msg.document.get_file()
        media, mime, source = bytes(await f.download_as_bytearray()), msg.document.mime_type, "gambar"
        text = msg.caption
    elif msg.voice or msg.audio:
        a = msg.voice or msg.audio
        f = await a.get_file()
        media, mime, source = bytes(await f.download_as_bytearray()), (a.mime_type or "audio/ogg"), "suara"
    else:
        text = msg.text

    await ctx.bot.send_chat_action(msg.chat_id, "typing")
    editing = ctx.chat_data.get("editing")
    previous = editing["raw"] if editing and "raw" in editing else None
    try:
        products = await asyncio.to_thread(store.products)
        result = await asyncio.to_thread(ai.extract, products, app_logic.today(),
                                         text=text, media=media, mime=mime, previous=previous)
        reply, pending = await asyncio.to_thread(app_logic.prepare, result)
        buttons = None
        if result.get("intent") == "hutang":
            buttons = debt_buttons(await asyncio.to_thread(app_logic.debt_list))
    except Exception:
        log.exception("processing failed")
        return await msg.reply_text("Alamak, ada masalah baca mesej tu. Cuba sekali lagi, atau taip terus.")

    ctx.chat_data.pop("editing", None)
    if pending:
        pending["source"] = editing["source"] if editing and "source" in editing else source
        ctx.chat_data["pending"] = pending
        await msg.reply_text(reply, reply_markup=KEYBOARD)
    else:
        await msg.reply_text(reply, reply_markup=buttons)


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not allowed(update):
        return
    pending = ctx.chat_data.get("pending")
    if not pending:
        return await q.edit_message_reply_markup(None)
    if q.data == "ya":
        ctx.chat_data.pop("pending")
        try:
            out = await asyncio.to_thread(app_logic.commit, pending, pending.get("source", "teks"))
        except Exception:
            log.exception("commit failed")
            ctx.chat_data["pending"] = pending
            return await q.message.reply_text("Tak dapat simpan ke Sheet. Cuba tekan YA sekali lagi sekejap lagi.")
        await q.edit_message_reply_markup(None)
        await q.message.reply_text(out)
    elif q.data == "edit":
        ctx.chat_data.pop("pending")
        ctx.chat_data["editing"] = pending
        await q.edit_message_reply_markup(None)
        await q.message.reply_text("OK, apa yang perlu dibetulkan? Taip atau hantar voice note.\n"
                                   "Contoh: 'harga RM40 bukan RM45' atau 'dia belum bayar'")
    else:
        ctx.chat_data.pop("pending")
        await q.edit_message_reply_markup(None)
        await q.message.reply_text("❌ Dibatalkan, tiada apa disimpan.")


async def weekly_job(ctx: ContextTypes.DEFAULT_TYPE):
    text = await asyncio.to_thread(app_logic.weekly)
    for uid in ALLOWED:
        try:
            await ctx.bot.send_message(uid, text)
        except Exception:
            log.exception("weekly report to %s failed", uid)


async def debt_reminder_job(ctx: ContextTypes.DEFAULT_TYPE):
    due = await asyncio.to_thread(app_logic.due_reminders, REMIND_EVERY)
    if not due:
        return
    lines = ["⏰ Hutang dah lama tertunggak:"]
    lines += [f"  - {d['nama']}: {rm(d['baki'])} ({d['hari']} hari)" for d in due]
    lines.append("\nTekan butang untuk hantar peringatan melalui WhatsApp 👇")
    for uid in ALLOWED:
        try:
            await ctx.bot.send_message(uid, "\n".join(lines), reply_markup=debt_buttons(due))
        except Exception:
            log.exception("debt reminder to %s failed", uid)


def main():
    # keeps a half-confirmed entry (YA/EDIT/BATAL) alive across restarts
    persistence = PicklePersistence(filepath=STATE_FILE)
    application = (Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"])
                   .persistence(persistence).build())
    application.add_handler(CommandHandler(["start", "mula"], cmd_start))
    application.add_handler(CommandHandler(["bantuan", "help"], cmd_help))
    application.add_handler(CommandHandler(["laporan", "report"], cmd_laporan))
    application.add_handler(CommandHandler("minggu", cmd_minggu))
    application.add_handler(CommandHandler("stok", cmd_stok))
    application.add_handler(CommandHandler("hutang", cmd_hutang))
    application.add_handler(CommandHandler(["batal", "undo"], cmd_batal))
    application.add_handler(CallbackQueryHandler(on_button))
    application.add_handler(MessageHandler(
        (filters.PHOTO | filters.VOICE | filters.AUDIO | filters.Document.IMAGE | filters.TEXT)
        & ~filters.COMMAND, on_message))
    hh, mm = (int(x) for x in REPORT_TIME.split(":"))
    application.job_queue.run_daily(weekly_job, time=time(hh, mm, tzinfo=TZ), days=(REPORT_DAY,))
    if REMIND_EVERY > 0:
        hh, mm = (int(x) for x in REMIND_TIME.split(":"))
        application.job_queue.run_daily(debt_reminder_job, time=time(hh, mm, tzinfo=TZ))
    log.info("BukuKira running. Allowed users: %s", ALLOWED or "NONE (set ALLOWED_USER_IDS)")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
