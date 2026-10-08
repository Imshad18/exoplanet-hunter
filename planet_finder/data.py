"""Target resolution, stellar parameters (TIC) and TESS light-curve download."""
import re
import warnings

import numpy as np

from . import archive

warnings.filterwarnings("ignore")

BTJD_OFFSET = 2457000.0
# Preference order for light-curve products when a sector has several.
AUTHOR_RANK = {"SPOC": 0, "TESS-SPOC": 1, "QLP": 2}


def btjd_to_year(t):
    return 2000.0 + (np.asarray(t) + BTJD_OFFSET - 2451545.0) / 365.25


def _val(row, key):
    try:
        v = row[key]
        if np.ma.is_masked(v) or v is None:
            return None
        v = float(v)
        return None if not np.isfinite(v) else v
    except Exception:
        return None


def resolve_target(text):
    """Turn 'TIC 123', '123', 'TOI-700', or a star name into a TIC id."""
    t = text.strip()
    m = re.fullmatch(r"(?:TIC)?[\s-]*(\d+)", t, re.I)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"TOI[\s-]*(\d+)(?:\.\d+)?", t, re.I)
    if m:
        tic = archive.toi_to_tic(m.group(1))
        if tic is None:
            raise ValueError(f"{t} not found in the NASA Exoplanet Archive TOI table")
        return tic
    from astroquery.mast import Catalogs
    rows = Catalogs.query_object(t, catalog="TIC", radius=0.003)
    if len(rows) == 0:
        raise ValueError(f"Could not resolve '{t}' to a TESS Input Catalog star")
    rows.sort("dstArcSec")
    return int(rows[0]["ID"])


def stellar_params(tic):
    """Stellar parameters from the TESS Input Catalog, with solar fallbacks."""
    from astroquery.mast import Catalogs
    rows = Catalogs.query_criteria(catalog="Tic", ID=int(tic))
    s = {"tic": int(tic)}
    if len(rows):
        r = rows[0]
        for out, key in [("ra", "ra"), ("dec", "dec"), ("tmag", "Tmag"), ("teff", "Teff"),
                         ("logg", "logg"), ("radius", "rad"), ("mass", "mass"), ("rho", "rho"),
                         ("dist_pc", "d"), ("contratio", "contratio"), ("vmag", "Vmag")]:
            s[out] = _val(r, key)
        for key in ("GAIA", "HIP", "TYC", "TWOMASS"):
            try:
                v = r[key]
                if not np.ma.is_masked(v) and str(v).strip() not in ("", "--"):
                    s[key.lower()] = str(v)
            except Exception:
                pass
    s["assumed"] = []
    if not s.get("radius"):
        s["radius"] = 1.0
        s["assumed"].append("radius")
    if not s.get("mass"):
        # crude main-sequence mass-radius relation
        s["mass"] = float(np.clip(s["radius"] ** 1.25, 0.08, 3.0))
        s["assumed"].append("mass")
    if not s.get("teff"):
        s["teff"] = 5772.0
        s["assumed"].append("teff")
    s["dist_ly"] = s["dist_pc"] * 3.26156 if s.get("dist_pc") else None
    return s


def _sector_of(mission):
    m = re.search(r"Sector\s*(\d+)", str(mission))
    return int(m.group(1)) if m else -1


def find_lightcurves(tic, max_sectors=0):
    """Search MAST and pick the best light-curve product per sector."""
    import lightkurve as lk
    sr = lk.search_lightcurve(f"TIC {tic}", mission="TESS")
    if len(sr) == 0:
        return sr, []
    tbl = sr.table
    best = {}
    for i in range(len(sr)):
        author = str(tbl["author"][i])
        if author not in AUTHOR_RANK:
            continue
        target = str(tbl["target_name"][i]).strip()
        if target.isdigit() and int(target) != int(tic):
            continue
        exptime = float(np.asarray(tbl["exptime"][i]))
        sector = _sector_of(tbl["mission"][i])
        # 120 s beats 20 s (same photons, smaller files); shorter FFI cadence beats longer
        rank = (AUTHOR_RANK[author], 0 if exptime == 120 else 1, exptime)
        if sector not in best or rank < best[sector][0]:
            best[sector] = (rank, i, author, exptime)
    picks = [{"sector": s, "index": v[1], "author": v[2], "exptime": v[3]}
             for s, v in sorted(best.items())]
    if max_sectors and len(picks) > max_sectors:
        # keep an even spread across the whole baseline (that's what makes rechecks meaningful)
        idx = np.unique(np.linspace(0, len(picks) - 1, max_sectors).round().astype(int))
        picks = [picks[i] for i in idx]
    return sr, picks


def download_sector(sr, pick):
    lc = sr[pick["index"]].download(quality_bitmask="default")
    if lc is None:
        return None
    lc = lc.remove_nans().normalize()
    t = np.asarray(lc.time.value, float)
    f = np.asarray(lc.flux.value, float)
    e = np.asarray(lc.flux_err.value, float)
    ok = np.isfinite(t) & np.isfinite(f)
    t, f, e = t[ok], f[ok], e[ok]
    if len(t) < 100:
        return None
    bad = ~np.isfinite(e) | (e <= 0)
    if bad.any():
        e[bad] = 1.4826 * np.median(np.abs(np.diff(f))) / np.sqrt(2)
    return t, f, e


def group_seasons(chunks, gap_days=150.0):
    """Group sectors into observing seasons separated by long gaps (e.g. 2018, 2020, 2025)."""
    chunks = sorted(chunks, key=lambda c: c["t"][0])
    seasons, cur = [], []
    for c in chunks:
        if cur and c["t"][0] - cur[-1]["t"][-1] > gap_days:
            seasons.append(cur)
            cur = []
        cur.append(c)
    if cur:
        seasons.append(cur)
    out = []
    for i, s in enumerate(seasons):
        y0 = int(btjd_to_year(s[0]["t"][0]))
        y1 = int(btjd_to_year(s[-1]["t"][-1]))
        years = f"{y0}" if y0 == y1 else f"{y0}–{y1}"
        secs = [c["sector"] for c in s]
        for c in s:
            c["season"] = i
        out.append({"id": i, "years": years, "sectors": secs,
                    "label": f"{years} · S{secs[0]}" + (f"–S{secs[-1]}" if len(secs) > 1 else ""),
                    "t0": float(s[0]["t"][0]), "t1": float(s[-1]["t"][-1])})
    return out
