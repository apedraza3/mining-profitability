"""Manual ledger: income and cost lines typed in from pages that have no API.

data/manual_ledger.csv (not in git: it holds the owner's numbers), one row per item:
    entry_id,ts,kind,coin,amount,usd,note
- entry_id: any stable unique id, e.g. "clore-revenue-2026-11"
- ts: YYYY-MM-DD (or full ISO) when it was earned/paid
- kind: earning | payout | fee | sale | purchase | summary | other
  ("summary" = a period total read off a dashboard, not an individual payment)
Rows land in the ledger with source "manual"; editing a row updates it on the next poll.
"""

import csv

from fleet import settings
from fleet.adapters.base import Adapter

KINDS = {"earning", "payout", "fee", "sale", "purchase", "summary", "other"}


def _num(value):
    value = (value or "").strip().replace(",", "")
    if not value:
        return None
    return float(value)


def parse_rows(rows) -> tuple[list[dict], list[str]]:
    entries, errors = [], []
    for i, row in enumerate(rows, start=2):  # line 1 is the header
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        if not row.get("entry_id") or row["entry_id"].startswith("#"):
            continue
        try:
            kind = row.get("kind", "").lower()
            if kind not in KINDS:
                raise ValueError(f"kind must be one of {sorted(KINDS)}")
            if not row.get("ts"):
                raise ValueError("ts is required")
            entries.append({
                "source": "manual", "entry_id": row["entry_id"], "ts": row["ts"], "kind": kind,
                "coin": (row.get("coin") or "").upper() or None,
                "amount": _num(row.get("amount")), "usd": _num(row.get("usd")),
                "detail": {"note": row.get("note") or ""},
            })
        except ValueError as e:
            errors.append(f"line {i}: {e}")
    return entries, errors


class ManualLedgerAdapter(Adapter):
    name = "manual_ledger"
    interval_s = 600

    def is_configured(self) -> bool:
        return settings.MANUAL_LEDGER.exists()

    def poll(self) -> dict:
        with open(settings.MANUAL_LEDGER, newline="", encoding="utf-8-sig") as f:
            entries, errors = parse_rows(csv.DictReader(f))
        return {"machines": [], "ledger": entries,
                "accounts": {"manual_ledger:status": {"rows": len(entries), "errors": errors}}}
