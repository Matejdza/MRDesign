"""Reads the private outreach sheet and writes only aggregate numbers to site/stats.json.

No company names, phones, emails or notes leave the sheet: the output holds counts only.

Needs two environment variables (set as GitHub Secrets):
  GOOGLE_SERVICE_ACCOUNT_JSON  full JSON key of the Google service account
  SHEET_ID                     id of the Google Sheet (the part of the URL after /d/)
"""

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account

LEAD_SHEETS = {
    "gradjevina": "'Građevina – Leadovi'!A1:R2000",
    "ordinacije": "'Ordinacije – Leadovi'!A1:R2000",
}
CALL_SHEETS = {
    "gradjevina": "'Građevina – Zvanje'!A1:F2000",
    "ordinacije": "'Ordinacije – Zvanje'!A1:F2000",
}
GROUP_NAMES = {"gradjevina": "Građevina", "ordinacije": "Ordinacije"}
OUT = Path(__file__).resolve().parent.parent / "site" / "stats.json"


def fetch_ranges(sheet_id: str, ranges: list[str]) -> list[list[list[str]]]:
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        sys.exit("Nedostaje secret GOOGLE_SERVICE_ACCOUNT_JSON.")
    creds = service_account.Credentials.from_service_account_info(
        json.loads(raw),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    creds.refresh(Request())
    resp = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values:batchGet",
        params=[("ranges", r) for r in ranges],
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=30,
    )
    if resp.status_code == 403:
        sys.exit("Google odbija pristup: podeli tabelu sa mejlom servisnog naloga (Viewer).")
    resp.raise_for_status()
    return [vr.get("values", []) for vr in resp.json().get("valueRanges", [])]


def to_rows(values: list[list[str]]) -> list[dict]:
    if not values:
        return []
    head = [str(h).strip() for h in values[0]]
    rows = []
    for r in values[1:]:
        if not any(str(c).strip() for c in r):
            continue
        rows.append({h: (r[i] if i < len(r) else "") for i, h in enumerate(head)})
    return rows


def status_of(row: dict) -> str:
    return str(row.get("Status", "")).strip()


def plus_workdays(iso: str, n: int = 3) -> date | None:
    try:
        d = date.fromisoformat(str(iso).strip()[:10])
    except ValueError:
        return None
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def funnel(rows: list[dict], today: date) -> dict:
    st = [status_of(r).lower() for r in rows]
    poslato = [r for r in rows if status_of(r).lower() == "poslato"]
    due = [plus_workdays(r.get("Poslednji kontakt", "")) for r in poslato]
    novo = sum(s in ("novo", "") for s in st)
    nacrti = sum(s == "nacrt spreman" for s in st)
    kontaktirano = len(st) - novo - nacrti
    odgovorili = sum(s.startswith(("odgovorio", "zainteresovan")) for s in st)
    zainteresovani = sum(s.startswith("zainteresovan") for s in st)
    return {
        "ukupno": len(st),
        "kontaktirano": kontaktirano,
        "odgovorili": odgovorili,
        "zainteresovani": zainteresovani,
        "bez_odgovora": len(poslato),
        "follow_up_danas": sum(d is not None and d <= today for d in due),
        "nacrti": nacrti,
        "novo": novo,
    }


def build(leads: dict[str, list[dict]], calls: dict[str, list[dict]]) -> dict:
    today = datetime.now(ZoneInfo("Europe/Belgrade")).date()
    all_rows = [r for rows in leads.values() for r in rows if r.get("Firma")]
    groups = {"sve": funnel(all_rows, today)}
    for g, rows in leads.items():
        groups[g] = funnel([r for r in rows if r.get("Firma")], today)

    statuses: dict[str, dict] = {}
    for g, rows in leads.items():
        for r in rows:
            if not r.get("Firma"):
                continue
            s = status_of(r) or "Bez statusa"
            d = statuses.setdefault(s, {"status": s, "gradjevina": 0, "ordinacije": 0, "ukupno": 0})
            d[g] += 1
            d["ukupno"] += 1

    pozivi = {}
    for g, rows in calls.items():
        rows = [r for r in rows if r.get("Firma")]
        pozivi[g] = {
            "na_listi": len(rows),
            "pozvano": sum(bool(str(r.get("Ishod", "")).strip()) for r in rows),
        }
    pozivi["sve"] = {k: sum(p[k] for p in pozivi.values()) for k in ("na_listi", "pozvano")}

    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "groups": groups,
        "statusi": sorted(statuses.values(), key=lambda d: -d["ukupno"]),
        "pozivi": pozivi,
    }


def main() -> None:
    sheet_id = os.environ.get("SHEET_ID", "").strip()
    if not sheet_id:
        sys.exit("Nedostaje secret SHEET_ID.")
    keys = list(LEAD_SHEETS) + [f"call_{k}" for k in CALL_SHEETS]
    ranges = list(LEAD_SHEETS.values()) + list(CALL_SHEETS.values())
    data = dict(zip(keys, fetch_ranges(sheet_id, ranges)))
    leads = {g: to_rows(data[g]) for g in LEAD_SHEETS}
    calls = {g: to_rows(data[f"call_{g}"]) for g in CALL_SHEETS}
    stats = build(leads, calls)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Upisano: {OUT} ({stats['groups']['sve']['ukupno']} leadova)")


if __name__ == "__main__":
    main()
