"""Offline tests: no Telegram, no Gemini, no Google. Run: python test_offline.py"""
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from core import match_product, current_stock, BELUM_BAYAR
from service import BukuKira
from store import MemoryStore

sys.stdout.reconfigure(encoding="utf-8")  # emoji in replies; Windows console defaults to cp1252
TZ = ZoneInfo("Asia/Kuala_Lumpur")
clock = {"now": datetime(2026, 9, 20, 10, 0, tzinfo=TZ)}
s = MemoryStore()
bk = BukuKira(s, lambda: clock["now"])


def run(result, source="teks"):
    reply, pending = bk.prepare(result)
    print("BOT>", reply, "\n")
    if pending:
        print("   ", bk.commit(pending, source), "\n")
    return pending


# 1. she adds products
run({"intent": "produk_baru", "produk_baru": [
    {"nama": "serum vit c", "nama_lain": "serum, vit c", "kos_seunit": 25, "harga_jual": 45, "stok_awal": 5},
    {"nama": "toner rose", "kos_seunit": 18, "harga_jual": 35, "tarikh_luput": "2026-10-30", "stok_awal": 4}]})
assert len(s.products()) == 2

# 2. duplicate product is refused
_, p = bk.prepare({"intent": "produk_baru", "produk_baru": [{"nama": "Serum Vit C"}]})
assert p is None

# 3. restock 10 serum at RM28 -> avg cost (5*25 + 10*28)/15 = 27.0
run({"intent": "rekod", "transaksi": [{"jenis": "Beli Stok", "produk": "serum", "kuantiti": 10, "harga_seunit": 28}]})
serum = match_product("serum", s.products())
assert abs(serum.kos - 27.0) < 1e-6, serum.kos

# 4. sale with no price -> uses list price 45; unpaid
run({"intent": "rekod", "transaksi": [
    {"jenis": "Jualan", "produk": "vit c serum", "kuantiti": 2, "pihak": "Kak Lina", "status_bayaran": "Belum bayar"},
    {"jenis": "Belanja", "kategori": "Pos", "jumlah": 8.5, "pihak": "J&T"}]}, "suara")
t = s.transactions()
sale = [x for x in t if x["Jenis"] == "Jualan"][0]
assert sale["Jumlah (RM)"] == 90 and sale["Kos barang (RM)"] == 54 and sale["Untung (RM)"] == 36, sale
assert sale["Status bayaran"] == BELUM_BAYAR

# 5. affiliate commission
run({"intent": "rekod", "transaksi": [{"jenis": "Komisen Affiliate", "jumlah": 320, "pihak": "TikTok Shop"}]})
assert s.transactions()[-1]["Kategori"] == "TikTok"

# 6. unknown product in a sale -> auto-added
run({"intent": "rekod", "transaksi": [{"jenis": "Jualan", "produk": "Lip Tint Coral", "kuantiti": 1, "jumlah": 22}]})
assert match_product("lip tint coral", s.products()) is not None

# 7. stock numbers: serum 5+10-2 = 13
st = current_stock(s.products(), s.transactions())
assert st["Serum Vit C"] == 13 and st["Toner Rose"] == 4, st

# 8. debt paid
run({"intent": "bayar_hutang", "pihak_bayar": "lina"})
assert not [x for x in s.transactions() if x["Status bayaran"] == BELUM_BAYAR]

# 9. month report: sales 112, cogs 54, gross 58, komisen 320, belanja 8.5 -> net 369.5
rep = bk.report("bulan")
print(rep, "\n")
assert "UNTUNG BERSIH: RM369.50" in rep, rep
assert "Nak luput: Toner Rose" in rep  # expiry within 60 days
assert "Tak bergerak 30 hari: Toner Rose" in rep

# 10. future date from AI is clamped to today; string price parsing
_, p = bk.prepare({"intent": "rekod", "transaksi": [{"jenis": "Belanja", "kategori": "props", "jumlah": "RM1,200", "tarikh": "2027-01-01"}]})
d = p["drafts"][0]
assert d.tarikh == "2026-09-20" and d.jumlah == 1200 and d.kategori == "Lain-lain"

# 11. clarifying question passes through
r, p = bk.prepare({"intent": "tak_faham", "soalan": "Berapa ringgit jumlah resit ni?"})
assert p is None and "Berapa" in r

# 12. stock adjustment: 1 serum damaged -> stock 12, loss at cost RM27 hits profit (not cash)
run({"intent": "rekod", "transaksi": [{"jenis": "Pelarasan Stok", "kategori": "Rosak", "produk": "serum", "kuantiti": 1}]})
st = current_stock(s.products(), s.transactions())
assert st["Serum Vit C"] == 12, st
rep = bk.report("bulan")
assert "UNTUNG BERSIH: RM342.50" in rep and "Stok rosak/sampel/hilang: RM27.00" in rep, rep

# 13. adjustment the other way: found 2 extra toner -> stock 6, no money impact
run({"intent": "rekod", "transaksi": [{"jenis": "Pelarasan Stok", "kategori": "Tambah", "produk": "toner", "kuantiti": 2}]})
assert current_stock(s.products(), s.transactions())["Toner Rose"] == 6
assert "UNTUNG BERSIH: RM342.50" in bk.report("bulan")

# 14. partial payment, applied oldest sale first
run({"intent": "rekod", "transaksi": [
    {"jenis": "Jualan", "produk": "toner", "kuantiti": 1, "pihak": "Mira", "status_bayaran": "Belum bayar", "tarikh": "2026-09-15"},
    {"jenis": "Jualan", "produk": "toner", "kuantiti": 2, "pihak": "Mira", "status_bayaran": "Belum bayar", "tarikh": "2026-09-18"}]})
run({"intent": "bayar_hutang", "pihak_bayar": "mira", "jumlah_bayar": 50})
mira = [x for x in s.transactions() if x["Pelanggan/Pihak"] == "Mira"]
assert mira[0]["Status bayaran"] != BELUM_BAYAR, mira[0]
assert mira[1]["Status bayaran"] == BELUM_BAYAR and mira[1]["Dibayar (RM)"] == 15, mira[1]
assert "Mira: RM55.00" in bk.debts(), bk.debts()
run({"intent": "bayar_hutang", "pihak_bayar": "mira", "jumlah_bayar": 55})
assert "Tiada siapa berhutang" in bk.debts()

# 15. undo last entry: marked cancelled, ignored everywhere, IDs keep counting
before = bk.report("bulan")
n_before = len(s.transactions())
run({"intent": "rekod", "transaksi": [
    {"jenis": "Belanja", "kategori": "Pos", "jumlah": 10},
    {"jenis": "Jualan", "produk": "serum", "kuantiti": 3}]})
r, p = bk.prepare({"intent": "batal_terakhir"})
assert p and len(p["ids"]) == 2 and "Pos" in r, r
bk.commit(p, "teks")
assert bk.report("bulan") == before
assert current_stock(s.products(), [t for t in s.transactions() if t.get("Batal") != "YA"])["Serum Vit C"] == 12
assert [t["Batal"] for t in s.transactions()[-2:]] == ["YA", "YA"]
assert s.next_id() == f"T{n_before + 3:05d}"
# undo again -> offers the batch before that (the two Mira sales)
_, p = bk.prepare({"intent": "batal_terakhir"})
assert [t["Pelanggan/Pihak"] for t in s.transactions() if t["ID"] in p["ids"]] == ["Mira", "Mira"]

# 16. debt reminders: nudge at 7, 14, 21... days, with a WhatsApp link (her customers use WA)
run({"intent": "rekod", "transaksi": [
    {"jenis": "Jualan", "produk": "serum", "kuantiti": 1, "pihak": "Kak Ida", "status_bayaran": "Belum bayar"}]})
clock["now"] = datetime(2026, 9, 26, 10, 0, tzinfo=TZ)
assert bk.due_reminders() == []  # 6 days
clock["now"] = datetime(2026, 9, 27, 10, 0, tzinfo=TZ)
due = bk.due_reminders()
assert [d["nama"] for d in due] == ["Kak Ida"] and due[0]["hari"] == 7, due
msg = bk.reminder_text(due[0])
assert "Kak Ida" in msg and "RM45.00" in msg, msg
link = bk.whatsapp_link(due[0])
assert link.startswith("https://wa.me/?text=") and "Kak%20Ida" in link, link
assert "(7 hari)" in bk.debts()

print(bk.debts())
print("\nALL TESTS PASSED")
