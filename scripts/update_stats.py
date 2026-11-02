"""Reads the private outreach sheet and writes only aggregate numbers to site/stats.json.

No company names, phones, emails or notes leave the sheet: the output holds counts only.
The Metrike sheet is read without its notes column, for the same reason.

Needs two environment variables (set as GitHub Secrets):
  GOOGLE_SERVICE_ACCOUNT_JSON  full JSON key of the Google service account
  SHEET_ID                     id of the Google Sheet (the part of the URL after /d/)
"""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

GROUPS = {
    "gradjevina": "Građevina",
    "ordinacije": "Ordinacije",
    "auto": "Auto",
}
LEAD_RANGE = "'{name} – Leadovi'!A1:Q3000"
CALL_RANGE = "'{name} – Zvanje'!A1:F3000"
METRIKE_RANGE = "'Metrike'!A1:G300"  # bez kolone Beleške (H), u njoj su imena firmi
OUT = Path(__file__).resolve().parent.parent / "site" / "stats.json"
KATS = ("novo", "nacrt", "ceka", "toplo", "odbili", "nevalidan", "ostalo")


def fetch_ranges(sheet_id: str, ranges: list[str]) -> list[list[list[str]]]:
    import requests
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

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
    if resp.status_code == 400:
        sys.exit(f"Google ne nalazi neki list (možda je preimenovan): {resp.text[:300]}")
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


def kat(status: str) -> str:
    """Faza leada iz kolone Status. Ista pravila kao u privatnom dashboardu."""
    s = str(status or "").strip().lower()
    if s in ("", "novo"):
        return "novo"
    if s.startswith("nacrt"):
        return "nacrt"
    if s.startswith("nevalidan"):
        return "nevalidan"
    if s == "poslato" or s.startswith("follow-up") or "ne javlja" in s:
        return "ceka"
    if s.startswith(("zainteresovan", "odložen")) or "razmisl" in s:
        return "toplo"
    if s.startswith(("odgovorio", "pozvan")):
        return "odbili"
    return "ostalo"


def kanal(row: dict) -> str:
    return "Poziv" if "poziv" in str(row.get("Kanal (Poziv / Mejl / Instagram)", "")).lower() else "Mejl"


def day(v) -> str:
    s = str(v or "").strip()[:10]
    return s if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else ""


def plus_workdays(iso: str, n: int = 3) -> date | None:
    if not iso:
        return None
    d = date.fromisoformat(iso)
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def share(a: int, b: int) -> float:
    return round(a / b, 4) if b else 0.0


def summarize(rows: list[dict], calls: list[dict], today: date) -> dict:
    o = {k: 0 for k in KATS}
    for r in rows:
        o[r["kat"]] += 1
    contacted = [r for r in rows if r["kat"] in ("ceka", "toplo", "odbili")]
    answered = [r for r in rows if r["kat"] in ("toplo", "odbili")]
    first_mail = [r for r in rows if r["status"].lower() == "poslato"]
    due = [plus_workdays(r["kontakt"]) for r in first_mail]
    upcoming = sorted(d for d in due if d and d > today)
    o.update(
        ukupno=len(rows),
        kontaktirano=len(contacted),
        odgovorili=len(answered),
        ceka_followup=len(first_mail),
        follow_up_danas=sum(1 for d in due if d and d <= today),
        sledeci_follow_up=upcoming[0].isoformat() if upcoming else None,
        mejl_kontaktirano=sum(r["kanal"] == "Mejl" for r in contacted),
        mejl_odgovorili=sum(r["kanal"] == "Mejl" for r in answered),
        mejl_toplo=sum(r["kanal"] == "Mejl" and r["kat"] == "toplo" for r in rows),
        poziv_kontaktirano=sum(r["kanal"] == "Poziv" for r in contacted),
        poziv_odgovorili=sum(r["kanal"] == "Poziv" for r in answered),
        poziv_toplo=sum(r["kanal"] == "Poziv" and r["kat"] == "toplo" for r in rows),
        za_zvanje=sum(r["kanal"] == "Poziv" and r["kat"] == "novo" for r in rows),
        na_listi=len(calls),
        pozvano=sum(bool(str(c.get("Ishod", "")).strip()) for c in calls),
    )
    o["stopa_odgovor"] = share(o["odgovorili"], o["kontaktirano"])
    o["stopa_mejl"] = share(o["mejl_odgovorili"], o["mejl_kontaktirano"])
    o["stopa_poziv"] = share(o["poziv_odgovorili"], o["poziv_kontaktirano"])
    return o


def weeks(metrike: list[dict]) -> list[dict]:
    def num(r: dict, k: str) -> int:
        m = re.search(r"-?\d+", str(r.get(k, "")).replace(".", ""))
        return int(m.group()) if m else 0

    out = []
    for r in metrike:
        label = str(r.get("Nedelja", "")).strip()
        if not label:
            continue
        grupa = "sve"
        for g, name in GROUPS.items():
            if name[:6].lower() in label.lower():
                grupa = g
        nedelja = re.split(r"\s[(–-]\s?|\s\(", label)[0].strip()
        nedelja = re.sub(r"\d{4}$", "", nedelja)
        poslato, odgovori = num(r, "Poslate poruke"), num(r, "Odgovori")
        out.append({
            "nedelja": nedelja,
            "grupa": grupa,
            "poslato": poslato,
            "odgovori": odgovori,
            "prihvatili": num(r, "Prihvatili da vide sajt"),
            "pozitivni": num(r, "Pozitivni utisci"),
            "pozivi": num(r, "Zakazani pozivi"),
            "prodaje": num(r, "Prodaje"),
            "stopa": share(odgovori, poslato),
        })
    return out


def activity(rows: list[dict]) -> list[dict]:
    days = sorted({d for r in rows for d in (r["dodato"], r["kontakt"] if r["kat"] in ("ceka", "toplo", "odbili") else "") if d})
    out = []
    for g in ("sve", *GROUPS):
        rs = [r for r in rows if g == "sve" or r["grupa"] == g]
        for d in days:
            out.append({
                "dan": d,
                "grupa": g,
                "dodato": sum(r["dodato"] == d for r in rs),
                "kontaktirano": sum(r["kontakt"] == d and r["kat"] in ("ceka", "toplo", "odbili") for r in rs),
            })
    return out


def build(leads: dict[str, list[dict]], calls: dict[str, list[dict]], metrike: list[dict], today: date) -> dict:
    rows = []
    for g, rs in leads.items():
        for r in rs:
            if not str(r.get("Firma", "")).strip():
                continue
            status = str(r.get("Status", "")).strip()
            rows.append({
                "grupa": g,
                "status": status,
                "kat": kat(status),
                "kanal": kanal(r),
                "dodato": day(r.get("Datum dodavanja")),
                "kontakt": day(r.get("Poslednji kontakt")),
            })
    call_rows = {g: [c for c in cs if str(c.get("Firma", "")).strip()] for g, cs in calls.items()}
    groups = {"sve": summarize(rows, [c for cs in call_rows.values() for c in cs], today)}
    for g in GROUPS:
        groups[g] = summarize([r for r in rows if r["grupa"] == g], call_rows.get(g, []), today)
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "today": today.isoformat(),
        "names": {"sve": "Sve niše", **GROUPS},
        "groups": groups,
        "nedelje": weeks(metrike),
        "aktivnost": activity(rows),
    }


def main() -> None:
    sheet_id = os.environ.get("SHEET_ID", "").strip()
    if not sheet_id:
        sys.exit("Nedostaje secret SHEET_ID.")
    ranges = [LEAD_RANGE.format(name=n) for n in GROUPS.values()]
    ranges += [CALL_RANGE.format(name=n) for n in GROUPS.values()]
    ranges.append(METRIKE_RANGE)
    values = fetch_ranges(sheet_id, ranges)
    k = len(GROUPS)
    leads = {g: to_rows(values[i]) for i, g in enumerate(GROUPS)}
    calls = {g: to_rows(values[k + i]) for i, g in enumerate(GROUPS)}
    metrike = to_rows(values[2 * k])
    today = datetime.now(ZoneInfo("Europe/Belgrade")).date()
    stats = build(leads, calls, metrike, today)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Upisano: {OUT} ({stats['groups']['sve']['ukupno']} leadova)")


if __name__ == "__main__":
    main()
