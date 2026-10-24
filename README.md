# MR Design outreach

Javna stranica sa zbirnim brojevima outreach-a. Podaci dolaze iz privatne Google tabele.
Na stranicu ne izlaze imena firmi, kontakti ni beleške, samo brojevi.

- `site/` stranica koju GitHub Pages objavljuje
- `scripts/update_stats.py` cita tabelu preko Google servisnog naloga i upisuje `site/stats.json`
- `.github/workflows/update.yml` pokrece skriptu svakog sata i objavljuje stranicu

Potrebni GitHub Secrets: `GOOGLE_SERVICE_ACCOUNT_JSON` i `SHEET_ID`.
