"""Business flow between the AI result and the store. No Telegram code here."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import core
from core import (BELI_STOK, JUALAN, PELARASAN, STOK_MASUK, DAH_BAYAR, Product,
                  allocate_payment, complete_draft, current_stock, debt_list, describe_draft,
                  describe_row, draft_to_row, format_report, format_stock, last_batch, live,
                  new_average_cost, num, rm)
from store import BaseStore


class BukuKira:
    def __init__(self, store: BaseStore, now_fn):
        self.store = store
        self.now_fn = now_fn  # returns tz-aware datetime in Malaysia time

    def today(self) -> date:
        return self.now_fn().date()

    def txns(self) -> list[dict]:
        """Transactions that count (undone rows left out)."""
        return live(self.store.transactions())

    # ------------------------------------------------------------ step 1
    def prepare(self, result: dict) -> tuple[str, dict | None]:
        """Return (reply text, pending action or None). Pending needs YA to commit."""
        intent = result.get("intent", "tak_faham")
        products = self.store.products()
        if result.get("soalan") and intent in ("rekod", "produk_baru", "tak_faham") and not (
                result.get("transaksi") or result.get("produk_baru")):
            return "🤔 " + result["soalan"], None

        if intent == "laporan":
            return self.report(result.get("tempoh") or "bulan"), None
        if intent == "stok":
            return self.stock(), None
        if intent == "hutang":
            return self.debts(), None
        if intent == "batal_terakhir":
            return self.prepare_undo()

        if intent == "rekod" and result.get("transaksi"):
            drafts = [complete_draft(t, products, self.today()) for t in result["transaksi"]]
            stock = current_stock(products, self.txns())
            for d in drafts:
                keluar = d.jenis == JUALAN or (d.jenis == PELARASAN and d.kategori != STOK_MASUK)
                if keluar and not d.produk_baru and d.kuantiti > stock.get(d.produk, 0):
                    d.warnings.append(f"Stok dalam rekod cuma {stock.get(d.produk, 0):g} unit.")
            msg = "Saya faham begini:\n\n" + "\n\n".join(describe_draft(d) for d in drafts)
            if result.get("transkrip"):
                msg = f"🎙 \"{result['transkrip']}\"\n\n" + msg
            if result.get("soalan"):
                msg += f"\n\n🤔 {result['soalan']}"
            return msg + "\n\nBetul?", {"type": "rekod", "drafts": drafts, "raw": result}

        if intent == "produk_baru" and result.get("produk_baru"):
            existing = {n for p in products for n in p.names()}
            new, skipped = [], []
            for r in result["produk_baru"]:
                nama = (r.get("nama") or "").strip().title()
                if not nama:
                    continue
                if nama.lower() in existing:
                    skipped.append(nama)
                    continue
                new.append(Product(nama=nama, nama_lain=r.get("nama_lain") or "",
                                   kos=num(r.get("kos_seunit")), harga_jual=num(r.get("harga_jual")),
                                   stok_awal=num(r.get("stok_awal")),
                                   tarikh_luput=r.get("tarikh_luput") or ""))
            if not new:
                return ("Produk tu dah ada dalam senarai: " + ", ".join(skipped) +
                        ". Nak tukar harga? Edit terus dalam Sheet (tab Produk)."), None
            lines = ["Tambah produk baru:"]
            for p in new:
                lines.append(f"  ✨ {p.nama} - kos {rm(p.kos)}, jual {rm(p.harga_jual)}, "
                             f"stok awal {p.stok_awal:g}" + (f", luput {p.tarikh_luput}" if p.tarikh_luput else ""))
                if not p.kos or not p.harga_jual:
                    lines.append("     ⚠️ Kos/harga jual belum ada - boleh isi kemudian dalam Sheet.")
            if skipped:
                lines.append("(Dah ada, tak ditambah: " + ", ".join(skipped) + ")")
            return "\n".join(lines) + "\n\nBetul?", {"type": "produk_baru", "products": new, "raw": result}

        if intent == "bayar_hutang":
            return self.prepare_payment(result)

        return ("Maaf, saya tak pasti apa nak rekod. Cuba hantar gambar resit, voice note, "
                "atau taip contoh: 'jual 2 serum RM90'. Taip /bantuan untuk panduan."), None

    def prepare_payment(self, result: dict) -> tuple[str, dict | None]:
        name = result.get("pihak_bayar") or ""
        rows = self.store.unpaid_for(name)
        if not rows:
            return f"Tak jumpa hutang atas nama '{name}'. Taip 'hutang' untuk tengok senarai.", None
        who = rows[0].get("Pelanggan/Pihak")
        owed = round(sum(core.balance(t) for t in rows), 2)
        amount = num(result.get("jumlah_bayar"), 0) or None
        updates, extra = allocate_payment(rows, amount)
        by_id = {t["ID"]: t for t in rows}
        if amount is None or amount >= owed:
            lines = [f"{who} dah bayar semua {rm(owed)}? Rekod ni akan ditanda 'Dah bayar':"]
        else:
            lines = [f"{who} bayar {rm(amount)} daripada {rm(owed)}. "
                     f"Baki lepas ni: {rm(owed - amount)}", "Dikira dari jualan paling lama:"]
        for tid, paid, status in updates:
            t = by_id[tid]
            tag = "selesai" if status == DAH_BAYAR else f"dibayar {rm(paid)} / {rm(num(t['Jumlah (RM)']))}"
            lines.append(f"  - {t['Tarikh']}: {t['Produk']} x{num(t['Kuantiti']):g} ({tag})")
        if extra:
            lines.append(f"⚠️ Lebih {rm(extra)} dari jumlah hutang - lebihan tak direkod.")
        notes = {t["ID"]: str(t.get("Catatan") or "") for t in rows}
        return "\n".join(lines) + "\n\nBetul?", {"type": "bayar_hutang", "updates": updates,
                                                 "notes": notes, "raw": result}

    def prepare_undo(self) -> tuple[str, dict | None]:
        rows = last_batch(self.store.transactions())
        if not rows:
            return "Tiada rekod untuk dibatalkan.", None
        lines = ["Batalkan rekod terakhir ni?"] + [f"  - {describe_row(t)}" for t in rows]
        if any(t.get("Jenis") == BELI_STOK for t in rows):
            lines.append("⚠️ Kos seunit purata dalam tab Produk tak diubah balik - semak kalau perlu.")
        lines.append("(Rekod kekal dalam Sheet, ditanda Batal = YA, dan tak dikira lagi.)")
        return "\n".join(lines) + "\n\nBetul?", {"type": "batal", "ids": [t["ID"] for t in rows]}

    # ------------------------------------------------------------ step 2
    def commit(self, pending: dict, source: str) -> str:
        now_iso = self.now_fn().strftime("%Y-%m-%d %H:%M")
        if pending["type"] == "produk_baru":
            for p in pending["products"]:
                self.store.add_product(p)
            self.store.refresh_stock_column()
            return f"✅ {len(pending['products'])} produk ditambah."

        if pending["type"] == "bayar_hutang":
            changes = {}
            for tid, paid, status in pending["updates"]:
                note = (f"[dibayar {self.today().isoformat()}]" if status == DAH_BAYAR
                        else f"[bayar sebahagian {self.today().isoformat()}]")
                changes[tid] = {"Dibayar (RM)": paid, "Status bayaran": status,
                                "Catatan": (pending["notes"].get(tid, "") + " " + note).strip()}
            self.store.update_transactions(changes)
            return "✅ Bayaran direkod."

        if pending["type"] == "batal":
            self.store.update_transactions({tid: {"Batal": "YA"} for tid in pending["ids"]})
            self.store.refresh_stock_column()
            return f"↩️ {len(pending['ids'])} rekod dibatalkan."

        drafts = pending["drafts"]
        products = {p.nama: p for p in self.store.products()}
        added = []
        for d in drafts:
            if d.produk_baru and d.produk and d.produk not in products:
                p = Product(nama=d.produk,
                            kos=d.harga_seunit if d.jenis == BELI_STOK else 0.0,
                            harga_jual=d.harga_seunit if d.jenis == JUALAN else 0.0,
                            # sold before it was ever recorded -> she had it on hand
                            stok_awal=d.kuantiti if d.jenis == JUALAN else 0.0)
                self.store.add_product(p)
                products[p.nama] = p
                added.append(p.nama)
        # restock -> weighted average cost
        stock = current_stock(list(products.values()), self.txns())
        for d in drafts:
            if d.jenis == BELI_STOK and d.produk in products and d.produk not in added:
                p = products[d.produk]
                new_cost = new_average_cost(stock.get(p.nama, 0), p.kos, d.kuantiti, d.harga_seunit)
                if abs(new_cost - p.kos) > 1e-6:
                    self.store.set_product_cost(p, new_cost)
                    p.kos = new_cost
                stock[p.nama] = stock.get(p.nama, 0) + d.kuantiti
        first = self.store.next_id()
        n0 = int(first[1:])
        rows = [draft_to_row(d, f"T{n0 + i:05d}", source, now_iso, batch=first)
                for i, d in enumerate(drafts)]
        self.store.append_transactions(rows)
        self.store.refresh_stock_column()
        msg = f"✅ Direkod ({len(rows)})."
        if added:
            msg += "\n✨ Produk baru ditambah: " + ", ".join(added)
        return msg

    # ------------------------------------------------------------ reports
    def report(self, tempoh: str = "bulan") -> str:
        t = self.today()
        if tempoh == "minggu":
            start, end, title = t - timedelta(days=t.weekday()), t, "LAPORAN MINGGU INI"
        elif tempoh == "bulan_lepas":
            end = t.replace(day=1) - timedelta(days=1)
            start, title = end.replace(day=1), "LAPORAN BULAN LEPAS"
        else:
            start, end, title = t.replace(day=1), t, "LAPORAN BULAN INI"
        return format_report(title, self.txns(), self.store.products(), start, end, t)

    def weekly(self) -> str:
        t = self.today()
        start = t - timedelta(days=6)
        return format_report("RINGKASAN MINGGUAN", self.txns(), self.store.products(), start, t, t)

    def stock(self) -> str:
        return format_stock(self.store.products(), self.txns())

    # ------------------------------------------------------------ debts
    def debt_list(self) -> list[dict]:
        return debt_list(self.txns(), self.today())

    def debts(self) -> str:
        dl = self.debt_list()
        if not dl:
            return "🎉 Tiada siapa berhutang."
        lines = [f"⏳ BELUM BAYAR: {rm(sum(d['baki'] for d in dl))}"]
        lines += [f"  - {d['nama']}: {rm(d['baki'])} ({d['hari']} hari)" for d in dl]
        return "\n".join(lines)

    def due_reminders(self, every_days: int = 7) -> list[dict]:
        """Debts to nudge today: oldest unpaid sale is 7, 14, 21... days old.
        Spaced out so the daily check doesn't nag her (or the customer) every day."""
        return [d for d in self.debt_list() if d["hari"] > 0 and d["hari"] % every_days == 0]

    @staticmethod
    def reminder_text(debt: dict) -> str:
        return core.reminder_text(debt)

    @staticmethod
    def whatsapp_link(debt: dict) -> str:
        return core.whatsapp_link(core.reminder_text(debt))
