"""NASA Exoplanet Archive (TAP) queries: known planets, TOIs, candidate lists."""
import csv
import io
import random
import time
from pathlib import Path

import requests

TAP_URL = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"


def tap(query, timeout=60):
    r = requests.get(TAP_URL, params={"query": query, "format": "json"}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def toi_to_tic(toi_number):
    rows = tap(f"select tid from toi where toipfx={int(toi_number)}")
    return int(rows[0]["tid"]) if rows else None


def known_planets(tic):
    """Confirmed planets around this TIC star."""
    try:
        return tap(
            "select pl_name,pl_orbper,pl_rade,pl_tranmid,disc_facility,disc_year,discoverymethod "
            f"from pscomppars where tic_id='TIC {int(tic)}'"
        )
    except Exception:
        return []


def known_tois(tic):
    """TESS Objects of Interest on this TIC star (any disposition)."""
    try:
        return tap(
            "select toi,tfopwg_disp,pl_orbper,pl_rade,pl_trandep,pl_tranmid "
            f"from toi where tid={int(tic)} order by toi"
        )
    except Exception:
        return []


TFOP_DISPOSITIONS = {
    "PC": "Planet Candidate", "CP": "Confirmed Planet", "KP": "Known Planet",
    "APC": "Ambiguous Planet Candidate", "FP": "False Positive", "FA": "False Alarm",
}


def candidate_tois(n=24, max_tmag=11.5):
    """A random sample of bright, still-unconfirmed TOIs (disposition PC) to recheck."""
    rows = tap(
        "select toi,tid,pl_orbper,pl_rade,pl_trandep,st_tmag,st_dist from toi "
        f"where tfopwg_disp='PC' and st_tmag<{max_tmag} and pl_orbper<15 and pl_orbper>0.5"
    )
    random.shuffle(rows)
    return rows[:n]


def _period_relation(p_found, p_known, tol=0.01):
    """Return 1 for same period, 'n' or '1/n' for harmonics, else None."""
    if not p_known or p_known <= 0:
        return None
    r = p_found / p_known
    for n in (1, 2, 3, 4):
        if abs(r - n) / n < tol:
            return "1" if n == 1 else f"{n}x"
        if n > 1 and abs(1 / r - n) / n < tol:
            return f"1/{n}"
    return None


CTOI_URL = "https://exofop.ipac.caltech.edu/tess/download_ctoi.php?sort=ctoi&output=csv"
CACHE = Path(__file__).resolve().parent.parent / "cache"


def known_ctois(tic):
    """Community TOIs (ExoFOP) on this star. The full list is cached for a day."""
    path = CACHE / "ctoi.csv"
    try:
        if not path.exists() or time.time() - path.stat().st_mtime > 86400:
            r = requests.get(CTOI_URL, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            CACHE.mkdir(exist_ok=True)
            path.write_text(r.text)
        rows = csv.DictReader(io.StringIO(path.read_text()))
        return [{"ctoi": row["CTOI"], "period": float(row["Period (days)"] or 0), "toi": row["Promoted to TOI"],
                 "user": row["User"], "paper": row["Paper"]} for row in rows if row["TIC ID"] == str(int(tic))]
    except Exception:
        return []


def cross_match(period, planets, tois, ctois=()):
    """Match a detected period against confirmed planets, then TOIs; exact periods beat harmonics."""
    for want_exact in (True, False):
        for p in planets:
            rel = _period_relation(period, p.get("pl_orbper"))
            if rel and (rel == "1") == want_exact:
                return {"kind": "confirmed", "name": p["pl_name"], "period": p["pl_orbper"],
                        "relation": rel, "facility": p.get("disc_facility"), "year": p.get("disc_year")}
        for t in tois:
            rel = _period_relation(period, t.get("pl_orbper"))
            if rel and (rel == "1") == want_exact:
                disp = t.get("tfopwg_disp") or ""
                return {"kind": "toi", "name": f"TOI-{t['toi']}", "period": t["pl_orbper"],
                        "relation": rel, "disposition": disp,
                        "disposition_text": TFOP_DISPOSITIONS.get(disp, disp or "Unknown")}
        for c in ctois:
            rel = _period_relation(period, c.get("period"))
            if rel and (rel == "1") == want_exact:
                return {"kind": "ctoi", "name": f"CTOI {c['ctoi']}", "period": c["period"], "relation": rel,
                        "disposition_text": "Community TOI" + (f", promoted to TOI-{c['toi']}" if c["toi"] else "")}
    return {"kind": "none"}
