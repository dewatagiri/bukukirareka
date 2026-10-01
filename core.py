"""BukuKira core logic: pure functions, no Telegram / Sheets / AI here.

Types of transaction (column "Jenis"):
  Jualan            - product sale (income, reduces stock)
  Komisen Affiliate - TikTok/Shopee payout (income)
  Beli Stok         - stock bought from supplier (cash out, adds stock)
  Belanja           - other costs (postage, packaging, props, ads...)
  Pelarasan Stok    - stock that left without a sale (damaged, samples, own use, lost),
                      or "Tambah" for stock that came back / was found. Losses count at
                      cost against profit, never against cash (that was paid at Beli Stok).

A row with Batal = "YA" was undone: it stays in the Sheet but every calculation skips it.
"""
from __future__ import annotations

import difflib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import quote

JUALAN, KOMISEN, BELI_STOK, BELANJA = "Jualan", "Komisen Affiliate", "Beli Stok", "Belanja"
PELARASAN = "Pelarasan Stok"
JENIS_ALL = [JUALAN, KOMISEN, BELI_STOK, BELANJA, PELARASAN]
KATEGORI_BELANJA = ["Pos", "Pembungkusan", "Kandungan/Props", "Iklan/Boost", "Lain-lain"]
STOK_KELUAR = ["Rosak", "Sampel", "Guna sendiri", "Hilang"]  # stock out, loss at cost
STOK_MASUK = "Tambah"                                         # stock back in, no money
KATEGORI_PELARASAN = STOK_KELUAR + [STOK_MASUK]
DAH_BAYAR, BELUM_BAYAR = "Dah bayar", "Belum bayar"

PRODUK_HEADERS = ["Nama", "Nama lain", "Kos seunit (RM)", "Harga jual (RM)",
                  "Stok awal", "Stok semasa", "Tarikh luput", "Aktif", "Catatan"]
TRANSAKSI_HEADERS = ["ID", "Tarikh", "Jenis", "Kategori", "Produk", "Kuantiti",
                     "Harga seunit (RM)", "Jumlah (RM)", "Kos barang (RM)", "Untung (RM)",
                     "Pelanggan/Pihak", "Status bayaran", "Sumber", "Catatan", "Direkod pada",
                     # v1.1: appended at the end so existing Sheets keep their columns
                     "Dibayar (RM)", "Batal", "Kumpulan"]


def num(v, default=0.0) -> float:
    """Parse '45', 'RM45.50', '1,200' -> float. Blank -> default."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^0-9.\-]", "", str(v).replace(",", ""))
    try:
        return float(s) if s not in ("", "-", ".") else default
    except ValueError:
        return default


def rm(x: float) -> str:
    return f"RM{x:,.2f}"


def parse_date(s) -> date | None:
    try:
        return date.fromisoformat(str(s).strip()[:10])
    except ValueError:
        return None


def live(txns: list[dict]) -> list[dict]:
    """Rows that count: everything except undone (Batal = YA) rows."""
    return [t for t in txns if str(t.get("Batal", "")).strip().upper() != "YA"]


# ---------------------------------------------------------------- products
@dataclass
class Product:
    nama: str
    nama_lain: str = ""
    kos: float = 0.0
    harga_jual: float = 0.0
    stok_awal: float = 0.0
    tarikh_luput: str = ""
    aktif: bool = True
    row: int | None = None  # sheet row number (1-based) if known

    @classmethod
    def from_row(cls, r: dict, row: int | None = None) -> "Product":
        return cls(
            nama=str(r.get("Nama", "")).strip(),
            nama_lain=str(r.get("Nama lain", "")).strip(),
            kos=num(r.get("Kos seunit (RM)")),
            harga_jual=num(r.get("Harga jual (RM)")),
            stok_awal=num(r.get("Stok awal")),
            tarikh_luput=str(r.get("Tarikh luput", "")).strip(),
            aktif=str(r.get("Aktif", "YA")).strip().upper() not in ("TIDAK", "NO", "N", "T"),
            row=row,
        )

    def names(self) -> list[str]:
        out = [self.nama] + [a.strip() for a in self.nama_lain.split(",") if a.strip()]
        return [n.lower() for n in out if n]


def match_product(name: str | None, products: list[Product]) -> Product | None:
    """Exact name/alias match first, then substring, then fuzzy."""
    if not name:
        return None
    q = name.strip().lower()
    for p in products:
        if q in p.names():
            return p
    for p in products:
        if any(q in n or n in q for n in p.names()):
            return p
    lookup = {n: p for p in products for n in p.names()}
    hit = difflib.get_close_matches(q, list(lookup), n=1, cutoff=0.75)
    return lookup[hit[0]] if hit else None


def current_stock(products: list[Product], txns: list[dict]) -> dict[str, float]:
    stock = {p.nama: p.stok_awal for p in products}
    for t in txns:
        prod = str(t.get("Produk", "")).strip()
        if not prod or prod not in stock:
            continue
        q = num(t.get("Kuantiti"))
        if t.get("Jenis") == BELI_STOK:
            stock[prod] += q
        elif t.get("Jenis") == JUALAN:
            stock[prod] -= q
        elif t.get("Jenis") == PELARASAN:
            stock[prod] += q if t.get("Kategori") == STOK_MASUK else -q
    return stock


def new_average_cost(stock_before: float, cost_before: float, qty_in: float, cost_in: float) -> float:
    """Weighted average cost after a restock."""
    base = max(stock_before, 0)
    if base + qty_in <= 0:
        return cost_in
    return round((base * cost_before + qty_in * cost_in) / (base + qty_in), 4)


# ------------------------------------------------------------ transactions
@dataclass
class Draft:
    """One line the AI extracted, completed and validated in Python."""
    jenis: str
    kategori: str = ""
    produk: str = ""
    kuantiti: float = 0.0
    harga_seunit: float = 0.0
    jumlah: float = 0.0
    pihak: str = ""
    status_bayaran: str = ""
    tarikh: str = ""
    catatan: str = ""
    produk_baru: bool = False  # product not in list yet -> will be added on confirm
    kos_barang: float = 0.0
    warnings: list[str] = field(default_factory=list)


def complete_draft(raw: dict, products: list[Product], today: date) -> Draft:
    jenis = raw.get("jenis") or ""
    if jenis not in JENIS_ALL:
        jl = jenis.lower()
        jenis = (JUALAN if "jual" in jl else KOMISEN if ("komisen" in jl or "affiliate" in jl)
                 else PELARASAN if ("pelarasan" in jl or "rosak" in jl or "sampel" in jl)
                 else BELI_STOK if "stok" in jl else BELANJA)
    d = Draft(jenis=jenis)
    d.kategori = raw.get("kategori") or ""
    d.pihak = (raw.get("pihak") or "").strip()
    d.catatan = (raw.get("catatan") or "").strip()
    td = parse_date(raw.get("tarikh"))
    d.tarikh = (td if td and td <= today else today).isoformat()
    q = num(raw.get("kuantiti"), 0)
    h = num(raw.get("harga_seunit"), 0)
    j = num(raw.get("jumlah"), 0)

    if jenis in (JUALAN, BELI_STOK):
        p = match_product(raw.get("produk"), products)
        if p:
            d.produk = p.nama
        else:
            d.produk = (raw.get("produk") or "").strip().title()
            d.produk_baru = bool(d.produk)
            if not d.produk:
                d.warnings.append("Nama produk tak dapat dikenal pasti.")
        q = q or 1
        if not h and not j:  # fall back to list price / cost
            if p:
                h = p.harga_jual if jenis == JUALAN else p.kos
            if not h:
                d.warnings.append("Harga tak disebut.")
        if not h and j:
            h = round(j / q, 2)
        if not j:
            j = round(q * h, 2)
        d.kuantiti, d.harga_seunit, d.jumlah = q, h, j
        if jenis == JUALAN:
            d.kategori = "Jualan produk"
            d.status_bayaran = raw.get("status_bayaran") or DAH_BAYAR
            if d.status_bayaran not in (DAH_BAYAR, BELUM_BAYAR):
                d.status_bayaran = BELUM_BAYAR if "belum" in d.status_bayaran.lower() else DAH_BAYAR
            cost = p.kos if p else 0.0
            d.kos_barang = round(cost * q, 2)
            if not p or not p.kos:
                d.warnings.append("Kos seunit produk ni belum ada - untung dikira RM0 kos. Isi dalam Sheet (tab Produk).")
        else:
            d.kategori = "Stok"
    elif jenis == PELARASAN:
        p = match_product(raw.get("produk"), products)
        d.produk = p.nama if p else (raw.get("produk") or "").strip().title()
        if not p:
            d.warnings.append("Produk ni tiada dalam senarai - stok tak akan berubah.")
        d.kategori = next((k for k in KATEGORI_PELARASAN
                           if k.lower() == d.kategori.strip().lower()), "Rosak")
        d.kuantiti = abs(q) or 1
        if d.kategori != STOK_MASUK:  # valued at cost: what the loss actually cost her
            d.harga_seunit = p.kos if p else 0.0
            d.jumlah = d.kos_barang = round(d.kuantiti * d.harga_seunit, 2)
    else:
        d.jumlah = j or round(q * h, 2)
        if not d.jumlah:
            d.warnings.append("Jumlah RM tak jelas.")
        if jenis == KOMISEN:
            d.kategori = d.kategori if d.kategori in ("TikTok", "Shopee", "Lain") else (
                "TikTok" if "tiktok" in (d.pihak + d.catatan).lower() else
                "Shopee" if "shopee" in (d.pihak + d.catatan).lower() else "Lain")
        elif d.kategori not in KATEGORI_BELANJA:
            d.kategori = "Lain-lain"
    return d


def draft_to_row(d: Draft, txn_id: str, source: str, now_iso: str, batch: str = "") -> list:
    """batch = ID of the first row saved in the same confirm; undo removes a whole batch."""
    untung = round(d.jumlah - d.kos_barang, 2) if d.jenis == JUALAN else ""
    return [txn_id, d.tarikh, d.jenis, d.kategori, d.produk,
            d.kuantiti or "", d.harga_seunit or "", d.jumlah,
            d.kos_barang if d.jenis in (JUALAN, PELARASAN) else "", untung,
            d.pihak, d.status_bayaran, source, d.catatan, now_iso,
            "", "", batch]


def describe_draft(d: Draft) -> str:
    if d.jenis == JUALAN:
        s = f"🛍 JUALAN: {d.kuantiti:g} x {d.produk} @ {rm(d.harga_seunit)} = {rm(d.jumlah)}"
        s += f"\n   Untung anggaran: {rm(d.jumlah - d.kos_barang)}"
        if d.pihak:
            s += f"\n   Pelanggan: {d.pihak}"
        s += f"\n   Bayaran: {d.status_bayaran}"
    elif d.jenis == BELI_STOK:
        s = f"📦 BELI STOK: {d.kuantiti:g} x {d.produk} @ {rm(d.harga_seunit)} = {rm(d.jumlah)}"
        if d.pihak:
            s += f"\n   Dari: {d.pihak}"
    elif d.jenis == PELARASAN:
        arah = "+" if d.kategori == STOK_MASUK else "-"
        s = f"🔧 PELARASAN STOK ({d.kategori}): {arah}{d.kuantiti:g} {d.produk}"
        if d.jumlah:
            s += f"\n   Nilai kos: {rm(d.jumlah)} (ditolak dari untung)"
    elif d.jenis == KOMISEN:
        s = f"💰 KOMISEN AFFILIATE ({d.kategori}): {rm(d.jumlah)}"
    else:
        s = f"🧾 BELANJA ({d.kategori}): {rm(d.jumlah)}"
        if d.pihak:
            s += f" - {d.pihak}"
    s += f"\n   Tarikh: {d.tarikh}"
    if d.catatan:
        s += f"\n   Nota: {d.catatan}"
    if d.produk_baru:
        s += f"\n   ✨ '{d.produk}' produk baru - akan ditambah ke senarai produk."
    for w in d.warnings:
        s += f"\n   ⚠️ {w}"
    return s


def describe_row(t: dict) -> str:
    """One saved Transaksi row, short (used when offering to undo it)."""
    jenis, j = t.get("Jenis"), rm(num(t.get("Jumlah (RM)")))
    if jenis in (JUALAN, BELI_STOK):
        s = f"{jenis}: {num(t.get('Kuantiti')):g} x {t.get('Produk')} = {j}"
    elif jenis == PELARASAN:
        s = f"{jenis} ({t.get('Kategori')}): {num(t.get('Kuantiti')):g} {t.get('Produk')}"
    else:
        s = f"{jenis} ({t.get('Kategori')}): {j}"
    if t.get("Pelanggan/Pihak"):
        s += f" - {t.get('Pelanggan/Pihak')}"
    return f"{t.get('ID')} {t.get('Tarikh')} {s}"


def last_batch(txns: list[dict]) -> list[dict]:
    """Rows saved by the most recent confirm that hasn't been undone.
    Rows from before v1.1 have no Kumpulan, so each counts as its own batch."""
    rows = live(txns)
    if not rows:
        return []
    last = max(rows, key=lambda t: num(str(t.get("ID", "")).lstrip("T")))
    key = last.get("Kumpulan") or last.get("ID")
    return [t for t in rows if (t.get("Kumpulan") or t.get("ID")) == key]


# ----------------------------------------------------------------- reports
def _in_range(t: dict, start: date, end: date) -> bool:
    d = parse_date(t.get("Tarikh"))
    return bool(d and start <= d <= end)


def period_summary(txns: list[dict], start: date, end: date) -> dict:
    s = defaultdict(float)
    belanja = defaultdict(float)
    for t in txns:
        if not _in_range(t, start, end):
            continue
        j, jenis = num(t.get("Jumlah (RM)")), t.get("Jenis")
        if jenis == JUALAN:
            s["jualan"] += j
            s["kos_barang"] += num(t.get("Kos barang (RM)"))
            if t.get("Status bayaran") != BELUM_BAYAR:
                s["tunai_masuk"] += j
            else:  # part-paid so far
                s["tunai_masuk"] += num(t.get("Dibayar (RM)"))
        elif jenis == KOMISEN:
            s["komisen"] += j
            s["tunai_masuk"] += j
        elif jenis == BELI_STOK:
            s["beli_stok"] += j
            s["tunai_keluar"] += j
        elif jenis == BELANJA:
            belanja[t.get("Kategori") or "Lain-lain"] += j
            s["belanja"] += j
            s["tunai_keluar"] += j
        elif jenis == PELARASAN and t.get("Kategori") != STOK_MASUK:
            s["stok_hapus"] += j
    s["untung_jualan"] = s["jualan"] - s["kos_barang"]
    s["untung_bersih"] = s["untung_jualan"] + s["komisen"] - s["belanja"] - s["stok_hapus"]
    s["tunai_bersih"] = s["tunai_masuk"] - s["tunai_keluar"]
    return {"s": dict(s), "belanja": dict(belanja)}


def balance(t: dict) -> float:
    """What is still owed on one sale row."""
    return round(num(t.get("Jumlah (RM)")) - num(t.get("Dibayar (RM)")), 2)


def unpaid(txns: list[dict]) -> dict[str, float]:
    out = defaultdict(float)
    for t in txns:
        if t.get("Jenis") == JUALAN and t.get("Status bayaran") == BELUM_BAYAR:
            out[t.get("Pelanggan/Pihak") or "(tiada nama)"] += balance(t)
    return dict(out)


def debt_list(txns: list[dict], today: date) -> list[dict]:
    """One entry per customer who owes: name, balance, age in days of the oldest
    unpaid sale, and the rows. Biggest balance first."""
    by_name: dict[str, list[dict]] = defaultdict(list)
    for t in txns:
        if t.get("Jenis") == JUALAN and t.get("Status bayaran") == BELUM_BAYAR:
            by_name[t.get("Pelanggan/Pihak") or "(tiada nama)"].append(t)
    out = []
    for nama, rows in by_name.items():
        dates = [d for d in (parse_date(t.get("Tarikh")) for t in rows) if d]
        out.append({"nama": nama, "baki": round(sum(balance(t) for t in rows), 2),
                    "hari": (today - min(dates)).days if dates else 0, "rows": rows})
    return sorted(out, key=lambda x: -x["baki"])


def allocate_payment(rows: list[dict], amount: float | None) -> tuple[list[tuple], float]:
    """Apply a payment to unpaid sale rows, oldest first.
    Returns ([(txn_id, dibayar_total, status), ...], amount left over).
    amount None = pays everything owed."""
    rows = sorted(rows, key=lambda t: (str(t.get("Tarikh")), str(t.get("ID"))))
    left = sum(balance(t) for t in rows) if amount is None else amount
    updates = []
    for t in rows:
        if left <= 0:
            break
        owed = balance(t)
        pay = min(owed, left)
        left = round(left - pay, 2)
        paid_total = round(num(t.get("Dibayar (RM)")) + pay, 2)
        updates.append((t["ID"], paid_total, DAH_BAYAR if pay >= owed else BELUM_BAYAR))
    return updates, max(left, 0.0)


def reminder_text(debt: dict) -> str:
    """Friendly BM payment reminder she can send to the customer."""
    items = []
    for t in debt["rows"]:
        d = parse_date(t.get("Tarikh"))
        items.append(f"{num(t.get('Kuantiti')):g} {t.get('Produk')}" + (f" ({d.strftime('%d/%m')})" if d else ""))
    return (f"Hai {debt['nama']} 😊 Sekadar peringatan mesra, ada baki {rm(debt['baki'])} "
            f"untuk {', '.join(items)} yang belum dijelaskan. Boleh bayar bila senang ya. "
            f"Terima kasih! 🙏")


def whatsapp_link(text: str) -> str:
    """She runs the bot in Telegram, but her customers are on WhatsApp: this opens
    WhatsApp's contact picker with the reminder pre-filled."""
    return "https://wa.me/?text=" + quote(text, safe="")


def stock_insights(products: list[Product], txns: list[dict], today: date,
                   slow_days: int = 30, expiry_days: int = 60) -> dict:
    stock = current_stock(products, txns)
    since = today - timedelta(days=slow_days)
    sold_recent = defaultdict(float)
    last_sale: dict[str, date] = {}
    for t in txns:
        if t.get("Jenis") != JUALAN:
            continue
        d = parse_date(t.get("Tarikh"))
        prod = t.get("Produk", "")
        if d and d >= since:
            sold_recent[prod] += num(t.get("Kuantiti"))
        if d and (prod not in last_sale or d > last_sale[prod]):
            last_sale[prod] = d
    active = [p for p in products if p.aktif]
    value = sum(max(stock.get(p.nama, 0), 0) * p.kos for p in active)
    fast = sorted(((p.nama, sold_recent[p.nama]) for p in active if sold_recent[p.nama] > 0),
                  key=lambda x: -x[1])[:3]
    slow = [(p.nama, stock[p.nama]) for p in active
            if stock.get(p.nama, 0) > 0 and (p.nama not in last_sale or last_sale[p.nama] < since)]
    low = [(p.nama, stock[p.nama]) for p in active
           if p.nama in dict(fast) and stock.get(p.nama, 0) <= max(2, sold_recent[p.nama] / 4)]
    expiring = []
    for p in active:
        ed = parse_date(p.tarikh_luput)
        if ed and stock.get(p.nama, 0) > 0 and ed <= today + timedelta(days=expiry_days):
            expiring.append((p.nama, ed.isoformat(), stock[p.nama]))
    return {"stock": stock, "value": value, "fast": fast, "slow": slow, "low": low, "expiring": expiring}


def format_report(title: str, txns: list[dict], products: list[Product],
                  start: date, end: date, today: date) -> str:
    ps = period_summary(txns, start, end)
    s, bel = ps["s"], ps["belanja"]
    g = lambda k: s.get(k, 0.0)
    lines = [f"📊 {title} ({start.strftime('%d/%m')} - {end.strftime('%d/%m/%Y')})", ""]
    lines += ["JUALAN PRODUK",
              f"  Jualan: {rm(g('jualan'))}",
              f"  Kos barang dijual: {rm(g('kos_barang'))}",
              f"  Untung jualan: {rm(g('untung_jualan'))}", "",
              "AFFILIATE",
              f"  Komisen: {rm(g('komisen'))}", ""]
    lines.append(f"BELANJA LAIN: {rm(g('belanja'))}")
    for k, v in sorted(bel.items(), key=lambda x: -x[1]):
        lines.append(f"  - {k}: {rm(v)}")
    if g("stok_hapus"):
        lines.append(f"Stok rosak/sampel/hilang: {rm(g('stok_hapus'))} (nilai kos)")
    lines += ["", f"✅ UNTUNG BERSIH: {rm(g('untung_bersih'))}", "",
              "ALIRAN TUNAI (duit sebenar masuk/keluar)",
              f"  Masuk: {rm(g('tunai_masuk'))}",
              f"  Keluar (termasuk beli stok {rm(g('beli_stok'))}): {rm(g('tunai_keluar'))}",
              f"  Bersih: {rm(g('tunai_bersih'))}"]
    up = unpaid(txns)
    if up:
        lines += ["", f"⏳ BELUM BAYAR: {rm(sum(up.values()))}"]
        lines += [f"  - {k}: {rm(v)}" for k, v in sorted(up.items(), key=lambda x: -x[1])]
    si = stock_insights(products, txns, today)
    if products:
        lines += ["", f"📦 STOK DALAM TANGAN: {rm(si['value'])} (duit terikat dalam stok)"]
        if si["fast"]:
            lines.append("  Laku cepat (30 hari): " + ", ".join(f"{n} ({q:g})" for n, q in si["fast"]))
        if si["low"]:
            lines.append("  Stok nak habis: " + ", ".join(
                f"{n} ({'HABIS' if q <= 0 else f'{q:g} tinggal'})" for n, q in si["low"]))
        if si["slow"]:
            lines.append("  Tak bergerak 30 hari: " + ", ".join(f"{n} ({q:g} unit)" for n, q in si["slow"]))
        if si["expiring"]:
            lines.append("  ⚠️ Nak luput: " + ", ".join(f"{n} ({d}, {q:g} unit)" for n, d, q in si["expiring"]))
    return "\n".join(lines)


def format_stock(products: list[Product], txns: list[dict]) -> str:
    stock = current_stock(products, txns)
    act = [p for p in products if p.aktif]
    if not act:
        return "Belum ada produk. Taip contoh: produk baru Serum Vit C kos 25 jual 45"
    lines = ["📦 STOK SEMASA"]
    for p in act:
        lines.append(f"  {p.nama}: {stock.get(p.nama, 0):g} unit  (kos {rm(p.kos)}, jual {rm(p.harga_jual)})")
    return "\n".join(lines)
