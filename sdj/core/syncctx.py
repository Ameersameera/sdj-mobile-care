"""Per-thread context used while the PC saves an entry that was made on a linked phone.

The phone may have been offline for days, so a few shop rules are applied differently for its entries:

* the entry keeps the business date it was made on (even if the PC has closed that day since);
* a bill is kept even when the PC sold the last piece meanwhile (stock may go below zero);
* an IMEI that the PC already sold is kept on the bill as text only.

Each of these is recorded here so the sync engine can raise an alert for the owner.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date


@dataclass
class PhoneEntry:
    device_id: str
    business_date: date | None = None
    allow_closed_day: bool = False
    closed_days: set = field(default_factory=set)
    negative_stock: list = field(default_factory=list)     # (product name, quantity after)
    imei_conflicts: list = field(default_factory=list)     # (imei text, product name)
    inactive_products: list = field(default_factory=list)  # product names that were hidden on the PC


_local = threading.local()


def current() -> PhoneEntry | None:
    return getattr(_local, "entry", None)


@contextmanager
def phone_entry(entry: PhoneEntry):
    saved = current()
    _local.entry = entry
    try:
        yield entry
    finally:
        _local.entry = saved
