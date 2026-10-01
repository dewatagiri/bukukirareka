"""Storage: Google Sheets (live) and in-memory (tests)."""
from __future__ import annotations

from core import (PRODUK_HEADERS, TRANSAKSI_HEADERS, BELUM_BAYAR, JUALAN,
                  Product, current_stock, live)

PRODUK_TAB, TRANSAKSI_TAB = "Produk", "Transaksi"


class BaseStore:
    def products(self) -> list[Product]: ...
    def transactions(self) -> list[dict]: ...
    def add_product(self, p: Product) -> None: ...
    def set_product_cost(self, p: Product, cost: float) -> None: ...
    def append_transactions(self, rows: list[list]) -> None: ...
    def update_transactions(self, changes: dict[str, dict]) -> None:
        """{txn_id: {column header: new value}} - used for payments and undo."""
    def refresh_stock_column(self) -> None: ...

    def next_id(self) -> str:
        # counts undone rows too, so an ID is never reused
        ids = [str(t.get("ID", "")) for t in self.transactions()]
        n = max([int(i[1:]) for i in ids if i[:1] == "T" and i[1:].isdigit()] or [0])
        return f"T{n + 1:05d}"

    def unpaid_for(self, name: str) -> list[dict]:
        q = name.strip().lower()
        return [t for t in live(self.transactions())
                if t.get("Jenis") == JUALAN and t.get("Status bayaran") == BELUM_BAYAR
                and q and q in str(t.get("Pelanggan/Pihak", "")).lower()]


class MemoryStore(BaseStore):
    def __init__(self):
        self._products: list[dict] = []
        self._txns: list[dict] = []

    def products(self):
        return [Product.from_row(r, i + 2) for i, r in enumerate(self._products) if r.get("Nama")]

    def transactions(self):
        return [dict(t) for t in self._txns]

    def add_product(self, p):
        self._products.append({"Nama": p.nama, "Nama lain": p.nama_lain, "Kos seunit (RM)": p.kos,
                               "Harga jual (RM)": p.harga_jual, "Stok awal": p.stok_awal,
                               "Stok semasa": p.stok_awal, "Tarikh luput": p.tarikh_luput,
                               "Aktif": "YA", "Catatan": ""})

    def set_product_cost(self, p, cost):
        for r in self._products:
            if r["Nama"] == p.nama:
                r["Kos seunit (RM)"] = cost

    def append_transactions(self, rows):
        for row in rows:
            self._txns.append(dict(zip(TRANSAKSI_HEADERS, row)))

    def update_transactions(self, changes):
        for t in self._txns:
            if t["ID"] in changes:
                t.update(changes[t["ID"]])

    def refresh_stock_column(self):
        st = current_stock(self.products(), live(self._txns))
        for r in self._products:
            r["Stok semasa"] = st.get(r["Nama"], 0)


class SheetStore(BaseStore):
    """Google Sheet with two tabs: Produk and Transaksi (created if missing)."""

    def __init__(self, creds_path: str, sheet_id: str):
        import gspread
        gc = gspread.service_account(filename=creds_path)
        self.sh = gc.open_by_key(sheet_id)
        self.wp = self._ensure(PRODUK_TAB, PRODUK_HEADERS)
        self.wt = self._ensure(TRANSAKSI_TAB, TRANSAKSI_HEADERS)

    def _ensure(self, title, headers):
        import gspread
        try:
            ws = self.sh.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = self.sh.add_worksheet(title=title, rows=1000, cols=len(headers))
        if ws.col_count < len(headers):  # v1 Sheet getting the v1.1 columns
            ws.add_cols(len(headers) - ws.col_count)
        if ws.row_values(1) != headers:
            ws.update(range_name="A1", values=[headers])
            ws.format(f"A1:{chr(64 + len(headers))}1", {"textFormat": {"bold": True}})
            ws.freeze(rows=1)
        return ws

    @staticmethod
    def _records(ws) -> list[tuple[int, dict]]:
        vals = ws.get_all_values()
        if not vals:
            return []
        hdr = vals[0]
        return [(i + 2, dict(zip(hdr, r + [""] * (len(hdr) - len(r)))))
                for i, r in enumerate(vals[1:]) if any(c.strip() for c in r)]

    def products(self):
        return [Product.from_row(r, row) for row, r in self._records(self.wp) if r.get("Nama", "").strip()]

    def transactions(self):
        return [r for _, r in self._records(self.wt)]

    def add_product(self, p):
        self.wp.append_row([p.nama, p.nama_lain, p.kos, p.harga_jual, p.stok_awal, p.stok_awal,
                            p.tarikh_luput, "YA", ""], value_input_option="RAW",
                           table_range="A1")

    def set_product_cost(self, p, cost):
        row = p.row or next((q.row for q in self.products() if q.nama == p.nama), None)
        if row:
            self.wp.update_cell(row, 3, cost)

    def append_transactions(self, rows):
        self.wt.append_rows(rows, value_input_option="RAW", table_range="A1")

    def update_transactions(self, changes):
        from gspread.utils import rowcol_to_a1
        data = []
        for row, r in self._records(self.wt):
            for col_name, value in changes.get(r.get("ID"), {}).items():
                col = TRANSAKSI_HEADERS.index(col_name) + 1
                data.append({"range": rowcol_to_a1(row, col), "values": [[value]]})
        if data:  # one API call, however many cells
            self.wt.batch_update(data, value_input_option="RAW")

    def refresh_stock_column(self):
        prods = self.products()
        if not prods:
            return
        st = current_stock(prods, live(self.transactions()))
        col = PRODUK_HEADERS.index("Stok semasa") + 1
        last = max(p.row for p in prods)
        cells = self.wp.range(2, col, last, col)
        by_row = {p.row: st.get(p.nama, 0) for p in prods}
        for c in cells:
            if c.row in by_row:
                c.value = by_row[c.row]
        self.wp.update_cells(cells, value_input_option="RAW")
