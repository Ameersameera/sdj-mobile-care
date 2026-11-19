"""One report structure, rendered to screen (Qt), PDF (ReportLab), Excel (OpenPyXL) and CSV."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any


@dataclass
class Col:
    title: str
    kind: str = "text"      # text / int / money / date / datetime / pct
    width: float = 1.0      # relative width for PDF


@dataclass
class Table:
    columns: list[Col]
    rows: list[list[Any]] = field(default_factory=list)
    totals: list[Any] | None = None


@dataclass
class KV:
    label: str
    value: Any
    kind: str = "money"
    strong: bool = False
    tone: str = ""           # "good" / "bad" / "" (colour hint)


@dataclass
class Section:
    title: str
    kvs: list[KV] = field(default_factory=list)
    table: Table | None = None
    note: str = ""
    kv_columns: int = 2


@dataclass
class Report:
    title: str
    subtitle: str = ""
    period: str = ""
    sections: list[Section] = field(default_factory=list)
    landscape: bool = False
    generated_at: datetime = field(default_factory=lambda: datetime.now().replace(microsecond=0))
    meta: dict = field(default_factory=dict)

    # ---------------------------------------------------------- (de)serialisation
    def to_json(self) -> str:
        def enc(o):
            if isinstance(o, Decimal):
                return {"__dec__": str(o)}
            if isinstance(o, datetime):
                return {"__dt__": o.isoformat()}
            if isinstance(o, date):
                return {"__d__": o.isoformat()}
            raise TypeError(type(o))
        return json.dumps(asdict(self), default=enc, ensure_ascii=False)

    @staticmethod
    def from_json(payload: str) -> "Report":
        def dec(o):
            if "__dec__" in o:
                return Decimal(o["__dec__"])
            if "__dt__" in o:
                return datetime.fromisoformat(o["__dt__"])
            if "__d__" in o:
                return date.fromisoformat(o["__d__"])
            return o
        raw = json.loads(payload, object_hook=dec)
        sections = []
        for s in raw.get("sections", []):
            table = None
            if s.get("table"):
                t = s["table"]
                table = Table(columns=[Col(**c) for c in t["columns"]], rows=t["rows"], totals=t.get("totals"))
            sections.append(Section(title=s["title"], kvs=[KV(**k) for k in s.get("kvs", [])], table=table,
                                    note=s.get("note", ""), kv_columns=s.get("kv_columns", 2)))
        return Report(title=raw["title"], subtitle=raw.get("subtitle", ""), period=raw.get("period", ""),
                      sections=sections, landscape=raw.get("landscape", False),
                      generated_at=raw.get("generated_at") or datetime.now(), meta=raw.get("meta", {}))

    def section(self, title: str) -> Section | None:
        return next((s for s in self.sections if s.title == title), None)

    def kv(self, section_title: str, label: str):
        sec = self.section(section_title)
        if sec:
            for k in sec.kvs:
                if k.label == label:
                    return k.value
        return None
