"""Pure money calculations shared by the UI (live totals) and the services (saving).

Using one function for both guarantees that what the cashier sees is exactly
what gets stored.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

from ..db.base import D, q2, q4

HUNDRED = Decimal("100")


def allocate(total, weights: list) -> list[Decimal]:
    """Split *total* across *weights* proportionally; shares sum exactly to total (largest remainder)."""
    total = q2(total)
    weights = [D(w) for w in weights]
    n = len(weights)
    if n == 0:
        return []
    wsum = sum(weights)
    if total == 0 or wsum <= 0:
        return [Decimal("0.00")] * n
    paise_total = int(total * 100)
    raw = [Decimal(paise_total) * w / wsum for w in weights]
    floors = [int(r.to_integral_value(rounding=ROUND_FLOOR)) for r in raw]
    remainder = paise_total - sum(floors)
    order = sorted(range(n), key=lambda i: (raw[i] - floors[i]), reverse=True)
    for i in order[:remainder]:
        floors[i] += 1
    return [(Decimal(p) / 100).quantize(Decimal("0.01")) for p in floors]


def split_tax(amount, rate, inclusive: bool) -> tuple[Decimal, Decimal, Decimal]:
    """Return (taxable, gst, total) for an amount at *rate* percent."""
    amount, rate = q2(amount), D(rate)
    if rate <= 0:
        return amount, Decimal("0.00"), amount
    if inclusive:
        taxable = q2(amount * HUNDRED / (HUNDRED + rate))
        return taxable, amount - taxable, amount
    gst = q2(amount * rate / HUNDRED)
    return amount, gst, amount + gst


@dataclass
class LineIn:
    quantity: int
    unit_price: Decimal
    discount: Decimal = Decimal("0")
    gst_rate: Decimal = Decimal("0")


@dataclass
class LineOut:
    gross: Decimal
    item_discount: Decimal
    bill_share: Decimal
    gross_ex_tax: Decimal
    discount_ex_tax: Decimal
    taxable: Decimal
    gst: Decimal
    line_total: Decimal


@dataclass
class BillOut:
    lines: list[LineOut] = field(default_factory=list)
    gross: Decimal = Decimal("0.00")
    item_discount: Decimal = Decimal("0.00")
    bill_discount: Decimal = Decimal("0.00")
    gross_ex_tax: Decimal = Decimal("0.00")
    discount_ex_tax: Decimal = Decimal("0.00")
    taxable: Decimal = Decimal("0.00")
    gst: Decimal = Decimal("0.00")
    total_before_round: Decimal = Decimal("0.00")
    round_off: Decimal = Decimal("0.00")
    grand_total: Decimal = Decimal("0.00")

    @property
    def cgst(self) -> Decimal:
        return q2(self.gst / 2)

    @property
    def sgst(self) -> Decimal:
        return self.gst - self.cgst


def compute_bill(lines: list[LineIn], bill_discount=0, *, gst_enabled: bool = False,
                 inclusive: bool = True, round_off: bool = True) -> BillOut:
    out = BillOut()
    grosses = [q2(D(l.quantity) * D(l.unit_price)) for l in lines]
    item_discs = [q2(l.discount) for l in lines]
    nets = [g - d for g, d in zip(grosses, item_discs)]
    bill_discount = q2(bill_discount)
    shares = allocate(bill_discount, [max(n, Decimal("0")) for n in nets])
    for l, g, idisc, share in zip(lines, grosses, item_discs, shares):
        rate = D(l.gst_rate) if gst_enabled else Decimal("0")
        amount = g - idisc - share
        taxable, gst, total = split_tax(amount, rate, inclusive)
        if rate > 0 and inclusive:
            g_ex = q2(g * HUNDRED / (HUNDRED + rate))
        else:
            g_ex = g
        d_ex = g_ex - taxable
        out.lines.append(LineOut(g, idisc, share, g_ex, d_ex, taxable, gst, total))
    out.gross = sum((x.gross for x in out.lines), Decimal("0.00"))
    out.item_discount = sum((x.item_discount for x in out.lines), Decimal("0.00"))
    out.bill_discount = bill_discount
    out.gross_ex_tax = sum((x.gross_ex_tax for x in out.lines), Decimal("0.00"))
    out.discount_ex_tax = sum((x.discount_ex_tax for x in out.lines), Decimal("0.00"))
    out.taxable = sum((x.taxable for x in out.lines), Decimal("0.00"))
    out.gst = sum((x.gst for x in out.lines), Decimal("0.00"))
    out.total_before_round = sum((x.line_total for x in out.lines), Decimal("0.00"))
    if round_off:
        out.grand_total = out.total_before_round.quantize(Decimal("1"), rounding=ROUND_HALF_UP).quantize(Decimal("0.01"))
    else:
        out.grand_total = out.total_before_round
    out.round_off = out.grand_total - out.total_before_round
    return out


def weighted_average(old_qty: int, old_avg, in_qty: int, in_cost) -> Decimal:
    old_avg, in_cost = D(old_avg), D(in_cost)
    if in_qty <= 0:
        return q4(old_avg)
    if old_qty <= 0:
        return q4(in_cost)
    return q4((Decimal(old_qty) * old_avg + Decimal(in_qty) * in_cost) / Decimal(old_qty + in_qty))


AMOUNT_UNITS = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven",
                "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
AMOUNT_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two(n: int) -> str:
    if n < 20:
        return AMOUNT_UNITS[n]
    return (AMOUNT_TENS[n // 10] + (" " + AMOUNT_UNITS[n % 10] if n % 10 else "")).strip()


def _three(n: int) -> str:
    h, r = divmod(n, 100)
    parts = []
    if h:
        parts.append(AMOUNT_UNITS[h] + " Hundred")
    if r:
        parts.append(_two(r))
    return " ".join(parts)


def amount_in_words(amount) -> str:
    """Indian numbering: 1,25,050.50 -> 'Rupees One Lakh Twenty Five Thousand Fifty and Fifty Paise Only'."""
    amount = q2(amount)
    negative = amount < 0
    amount = abs(amount)
    rupees = int(amount)
    paise = int((amount - rupees) * 100)
    if rupees == 0:
        words = "Zero"
    else:
        crore, rest = divmod(rupees, 10_000_000)
        lakh, rest = divmod(rest, 100_000)
        thousand, rest = divmod(rest, 1000)
        parts = []
        if crore:
            parts.append(_three(crore) + " Crore" if crore < 1000 else amount_in_words(crore).replace("Rupees ", "").replace(" Only", "") + " Crore")
        if lakh:
            parts.append(_two(lakh) + " Lakh")
        if thousand:
            parts.append(_two(thousand) + " Thousand")
        if rest:
            parts.append(_three(rest))
        words = " ".join(parts)
    text = f"Rupees {words}"
    if paise:
        text += f" and {_two(paise)} Paise"
    text += " Only"
    return ("Minus " + text) if negative else text
