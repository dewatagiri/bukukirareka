"""Gemini: turn a photo / voice note / text into structured JSON."""
from __future__ import annotations

import json
import re
from datetime import date

from google import genai
from google.genai import types

PROMPT = """You are the bookkeeping assistant for a small home-based seller in Malaysia.
She (1) resells beauty/skincare products that she buys upfront from a friend, and
(2) earns affiliate commission from TikTok Shop / Shopee.
She talks in Bahasa Malaysia, Manglish or English. Today is {today}.

Her current product list (name | other names | cost | selling price):
{products}

Read the input (photo of a receipt / payment screenshot / payout screen, a voice note, or text)
and return ONLY a JSON object with these keys:

{{
  "intent": one of
     "rekod"        - record one or more transactions
     "produk_baru"  - add new product(s) to the product list
     "bayar_hutang" - a customer has now paid what they owed (all of it, or part)
     "batal_terakhir" - undo the entry she just saved ("batal yang tadi", "salah, padam
                      yang last", "undo")
     "laporan"      - she asks for a report / summary
     "stok"         - she asks about stock
     "hutang"       - she asks who still owes her
     "tak_faham"    - not related / cannot read
  "transkrip": for voice notes, what she said (original language); else "",
  "transaksi": [  // for intent "rekod"
     {{
       "jenis": "Jualan" | "Komisen Affiliate" | "Beli Stok" | "Belanja" | "Pelarasan Stok",
       "kategori": for Belanja one of "Pos","Pembungkusan","Kandungan/Props","Iklan/Boost","Lain-lain";
                   for Komisen Affiliate one of "TikTok","Shopee","Lain";
                   for Pelarasan Stok one of "Rosak","Sampel","Guna sendiri","Hilang","Tambah"; else "",
       "produk": product name - use the EXACT name from the list if it matches (any alias,
                 typo or shortening), otherwise the name as she said it; "" if not a product line,
       "kuantiti": number or null,
       "harga_seunit": price per unit in RM or null,
       "jumlah": total RM or null,
       "pihak": customer / supplier / platform name, or "",
       "status_bayaran": for Jualan: "Dah bayar" or "Belum bayar" (words like "belum bayar",
                 "hutang", "nanti bayar", "COD belum" = Belum bayar; default "Dah bayar"),
       "tarikh": "YYYY-MM-DD" if a date is shown/said (semalam = yesterday), else null,
       "catatan": short note, else ""
     }}
  ],
  "produk_baru": [  // for intent "produk_baru"
     {{"nama": "", "nama_lain": "comma-separated aliases or ''", "kos_seunit": number|null,
       "harga_jual": number|null, "stok_awal": number|null, "tarikh_luput": "YYYY-MM-DD"|null}}
  ],
  "pihak_bayar": for "bayar_hutang": the customer's name, else "",
  "jumlah_bayar": for "bayar_hutang": RM amount paid if she says one ("Mira bayar RM50 dulu"),
                  null if she just says they paid / paid everything,
  "tempoh": for "laporan": "minggu" | "bulan" | "bulan_lepas" (default "bulan"),
  "soalan": if something essential is missing or unreadable, ONE short question in BM, else ""
}}

Rules:
- "jual"/"sold"/customer transfer screenshot for a product = Jualan.
- "beli stok"/"restock"/payment to her friend/supplier for products = Beli Stok.
- Commission / payout / "komisen" / "affiliate" / TikTok or Shopee payout screen = Komisen Affiliate.
- Postage (J&T, PosLaju, Ninja Van, Shopee Xpress), boxes, bubble wrap, props, ring light,
  samples for content, TikTok/FB ads or boost = Belanja with the right kategori.
- Her OWN stock leaving without a sale = Pelarasan Stok: damaged/pecah/bocor = "Rosak",
  given free or opened for a review/content = "Sampel", she used it herself = "Guna sendiri",
  missing = "Hilang". Extra stock she finds (recount, sample came back unused) = "Tambah".
  No money amount needed; only produk and kuantiti. A customer returning a bought item
  for a refund is NOT handled yet: use intent "tak_faham" and set soalan to suggest she
  undoes the sale ("batal yang tadi") or fixes it in the Sheet.
  (Buying samples/props with money is still Belanja.)
- A receipt with several products = one transaksi line per product.
- One message can hold several transactions, e.g. "jual 2 serum dan 1 toner, Kak Lina belum bayar".
- Never invent amounts. If a number is not visible or said, use null.
- Amounts are in RM. "RM45", "45 ringgit", "45 hengget" = 45.
"""

EDIT_PROMPT = """This is the DRAFT you extracted earlier:
{draft}

She now sends a correction (text or voice). Apply the correction to the draft and return the
FULL corrected JSON in the same format (same keys). Keep everything she did not change."""


def _products_text(products) -> str:
    if not products:
        return "(no products yet)"
    return "\n".join(f"- {p.nama} | {p.nama_lain or '-'} | {p.kos:g} | {p.harga_jual:g}"
                     for p in products if p.aktif)


def _parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


class Extractor:
    def __init__(self, api_key: str, model: str):
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def extract(self, products, today: date, *, text: str | None = None,
                media: bytes | None = None, mime: str | None = None,
                previous: dict | None = None) -> dict:
        parts: list = [PROMPT.format(today=today.isoformat(), products=_products_text(products))]
        if previous is not None:
            parts.append(EDIT_PROMPT.format(draft=json.dumps(previous, ensure_ascii=False)))
        if media:
            parts.append(types.Part.from_bytes(data=media, mime_type=mime))
        if text:
            parts.append(f"Her message: {text}")
        resp = self.client.models.generate_content(
            model=self.model,
            contents=parts,
            config=types.GenerateContentConfig(temperature=0,
                                               response_mime_type="application/json"),
        )
        return _parse_json(resp.text)
