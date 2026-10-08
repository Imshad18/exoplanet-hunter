"""End-to-end: resolve star -> download all TESS sectors -> search -> vet -> cross-match."""
import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from . import archive, data, search, vetting

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"

DEFAULTS = {
    "period_min": 0.5,     # days
    "period_max": 20.0,    # days
    "max_planets": 3,
    "sde_threshold": 7.0,  # stop iterating below this
    "detrend_window": 1.0,  # days
    "max_sectors": 0,      # 0 = all
}


class Cancelled(Exception):
    pass


def _r(x, n=6):
    return [round(float(v), n) for v in x]


def _decimate_max(x, y, n=4000):
    """Keep peaks when shrinking a periodogram for plotting."""
    if len(x) <= n:
        return _r(x), _r(y, 3)
    k = int(np.ceil(len(x) / n))
    m = len(x) // k * k
    yy = y[:m].reshape(-1, k)
    idx = np.argmax(yy, axis=1) + np.arange(yy.shape[0]) * k
    return _r(x[idx]), _r(y[idx], 3)


def _fold_plot(t, f, e, P, t0, dur):
    ph_h = vetting.phase_days(t, P, t0) * 24
    win = max(3 * dur * 24, 6.0)
    sel = np.abs(ph_h) < win
    x, y = ph_h[sel], f[sel]
    o = np.argsort(x)
    x, y = x[o], y[o]
    nb = 80
    edges = np.linspace(-win, win, nb + 1)
    which = np.digitize(x, edges) - 1
    bx, by = [], []
    for b in range(nb):
        m = which == b
        if m.sum() >= 3:
            bx.append(x[m].mean())
            by.append(np.median(y[m]))
    if len(x) > 6000:
        pick = np.sort(np.random.default_rng(0).choice(len(x), 6000, replace=False))
        x, y = x[pick], y[pick]
    return {"x": _r(x, 4), "y": _r(y, 6), "bx": _r(bx, 4), "by": _r(by, 6)}


def _binned_fold(t, f, P, t0, dur, center=0.0, nb=40):
    ph_h = vetting.phase_days(t, P, t0 + center) * 24
    win = max(3 * dur * 24, 6.0)
    edges = np.linspace(-win, win, nb + 1)
    which = np.digitize(ph_h, edges) - 1
    bx, by = [], []
    for b in range(nb):
        m = which == b
        if m.sum() >= 3:
            bx.append(ph_h[m].mean())
            by.append(np.median(f[m]))
    return {"x": _r(bx, 4), "y": _r(by, 6)}


def run(target, options=None, progress=None, cancel=None):
    opts = {**DEFAULTS, **(options or {})}
    t_start = time.time()
    log_lines = []

    def step(name, pct, msg):
        log_lines.append({"t": round(time.time() - t_start, 1), "step": name, "msg": msg})
        if progress:
            progress(name, pct, msg)
        if cancel and cancel():
            raise Cancelled()

    # 1. Target ---------------------------------------------------------------
    step("resolve", 2, f"Resolving '{target}' in the TESS Input Catalog…")
    tic = data.resolve_target(target)
    step("resolve", 5, f"Target is TIC {tic}. Fetching stellar parameters…")
    star = data.stellar_params(tic)
    star["input"] = target
    planets = archive.known_planets(tic)
    tois = archive.known_tois(tic)
    known_msg = []
    if planets:
        known_msg.append(f"{len(planets)} confirmed planet(s): " + ", ".join(p["pl_name"] for p in planets))
    if tois:
        known_msg.append(f"{len(tois)} TOI(s): " + ", ".join(f"TOI-{t['toi']}" for t in tois))
    step("resolve", 8, "NASA Exoplanet Archive: " + ("; ".join(known_msg) if known_msg else "nothing known about this star."))

    # 2. Data -----------------------------------------------------------------
    step("download", 10, "Searching MAST for TESS light curves (SPOC, TESS-SPOC, QLP)…")
    sr, picks = data.find_lightcurves(tic, opts["max_sectors"])
    if not picks:
        raise ValueError(f"No TESS light curves found for TIC {tic}")
    step("download", 12, f"{len(picks)} sector(s) available: " + ", ".join(f"S{p['sector']}" for p in picks))
    chunks = []
    for k, p in enumerate(picks):
        step("download", 12 + 33 * k / len(picks),
             f"Downloading sector {p['sector']} ({p['author']}, {int(p['exptime'])} s cadence)…")
        try:
            d = data.download_sector(sr, p)
        except Exception as exc:  # corrupted cache file, MAST hiccup, ...
            step("download", 12 + 33 * k / len(picks), f"  ⚠ sector {p['sector']} failed: {exc}")
            continue
        if d is not None:
            chunks.append({**p, "t": d[0], "f": d[1], "e": d[2]})
    if not chunks:
        raise ValueError("All downloads failed")
    seasons = data.group_seasons(chunks)
    step("download", 45, f"Got {len(chunks)} sectors in {len(seasons)} observing season(s): "
         + " | ".join(s["label"] for s in seasons))

    # 3. Clean & detrend --------------------------------------------------------
    step("detrend", 48, "Removing scattered-light ramps, detrending stellar variability, clipping flares…")
    T = np.concatenate([c["t"] for c in chunks])
    FR = np.concatenate([c["f"] for c in chunks])
    E = np.concatenate([c["e"] for c in chunks])
    SEA = np.concatenate([np.full(len(c["t"]), c["season"]) for c in chunks])
    SEC = np.concatenate([np.full(len(c["t"]), c["sector"]) for c in chunks])
    o = np.argsort(T)
    T, FR, E, SEA, SEC = T[o], FR[o], E[o], SEA[o], SEC[o]
    keep = search.trim_edges(T)
    T, FR, E, SEA, SEC = T[keep], FR[keep], E[keep], SEA[keep], SEC[keep]

    def clean(mask=None):
        F = search.detrend(T, FR, opts["detrend_window"], mask=mask)
        ok = np.isfinite(F)
        ok[ok] &= search.clip_upper(F[ok])
        return F, ok

    F, OK = clean()
    # Rescale per-sector errors to the actual scatter (pipeline errors are often off)
    for s in np.unique(SEC):
        m = (SEC == s) & OK
        if m.sum() > 50:
            E[SEC == s] *= search.robust_std(F[m]) / np.median(E[m])
    step("detrend", 52, f"{OK.sum():,} good data points spanning {T[-1] - T[0]:.0f} days "
         f"({data.btjd_to_year(T[0]):.1f}–{data.btjd_to_year(T[-1]):.1f}).")

    # 4. Iterative search --------------------------------------------------------
    found = []
    periodograms = []
    planet_mask = np.zeros(len(T), bool)
    for it in range(int(opts["max_planets"])):
        base_pct = 55 + 40 * it / opts["max_planets"]
        span = 40 / opts["max_planets"]
        step("search", base_pct, f"Search #{it + 1}: stacked BLS across {len(seasons)} season(s), "
             f"periods {opts['period_min']}–{opts['period_max']} d…")
        use = OK & ~planet_mask
        season_data = [(T[use & (SEA == s["id"])], F[use & (SEA == s["id"])], E[use & (SEA == s["id"])])
                       for s in seasons]
        season_data = [sd for sd in season_data if len(sd[0]) > 50]
        try:
            periods, power, best = search.stacked_search(season_data, opts["period_min"], opts["period_max"])
        except ValueError as exc:
            step("search", base_pct, f"Stopping: {exc}")
            break
        px, py = _decimate_max(periods, power)
        periodograms.append({"iteration": it + 1, "period": px, "power": py,
                             "best": best["period"], "sde": round(best["sde"], 2)})
        step("search", base_pct + span * 0.3,
             f"Strongest peak P = {best['period']:.5f} d, SDE = {best['sde']:.1f}")
        if best["sde"] < opts["sde_threshold"]:
            step("search", base_pct + span * 0.3,
                 f"SDE below threshold {opts['sde_threshold']} — no further significant signals.")
            break

        step("refine", base_pct + span * 0.4, "Refining ephemeris coherently across all seasons…")
        ref = search.refine(T[use], F[use], E[use], best["period"], best["dlnp"])
        P, T0, dur = ref["period"], ref["t0"], ref["duration"]

        # Re-detrend with this transit masked so the filter doesn't eat into the dip
        tmask = search.transit_mask(T, P, T0, dur, 1.5)
        F2, OK2 = clean(planet_mask | tmask)
        use2 = OK2 & ~planet_mask
        ref = search.refine(T[use2], F2[use2], E[use2], P, ref["period_err"] / P * 3)
        P, T0, dur = ref["period"], ref["t0"], ref["duration"]
        tmask = search.transit_mask(T, P, T0, dur, 1.5)
        t, f, e, sea = T[use2], F2[use2], E[use2], SEA[use2]

        step("vet", base_pct + span * 0.6, f"Vetting P = {P:.5f} d: odd/even, secondary eclipse, "
             "per-transit, per-season rechecks…")
        beta = vetting.red_noise_beta(t, f, e, dur, tmask[use2])
        depth, derr, n_in = vetting.depth_at(t, f, e, P, T0, dur, beta=beta)
        if not np.isfinite(depth) or depth <= 0:
            step("vet", base_pct + span, "Signal has no measurable dip after re-detrending — discarded.")
            planet_mask |= tmask
            continue
        ep = np.round((t - T0) / P).astype(int)
        odd = ep % 2 == 1
        do, eo, _ = vetting.depth_at(t[odd], f[odd], e[odd], P, T0, dur, beta=beta)
        de, ee, _ = vetting.depth_at(t[~odd], f[~odd], e[~odd], P, T0, dur, beta=beta)
        ds, es, _ = vetting.depth_at(t, f, e, P, T0, dur, center=P / 2, beta=beta)
        transits = vetting.per_transit(t, f, e, P, T0, dur, beta=beta)

        season_rows = []
        for s in seasons:
            m = sea == s["id"]
            if m.sum() < 50:
                continue
            d_s, e_s, n_s = vetting.depth_at(t[m], f[m], e[m], P, T0, dur, beta=beta)
            row = {"season": s["id"], "label": s["label"], "years": s["years"], "n_in": n_s,
                   "depth": d_s, "err": e_s,
                   "snr": float(d_s / e_s) if np.isfinite(d_s) and e_s > 0 else float("nan"),
                   "n_transits": sum(1 for x in transits if s["t0"] - 1 <= x["t"] <= s["t1"] + 1),
                   "fold": _binned_fold(t[m], f[m], P, T0, dur)}
            lp = search.local_period(t[m], f[m], e[m], P)
            if lp:
                row["indep_period"], row["indep_res"] = lp["period"], lp["resolution"]
            season_rows.append(row)

        cand = {
            "id": len(found) + 1, "period": P, "period_err": ref["period_err"], "t0": T0,
            "t0_bjd": T0 + data.BTJD_OFFSET, "duration_h": dur * 24, "depth": depth, "depth_err": derr,
            "depth_ppm": depth * 1e6, "snr": depth / derr, "sde": best["sde"], "red_noise_beta": beta,
            "depth_odd": do, "err_odd": eo, "depth_even": de, "err_even": ee,
            "depth_secondary": ds, "err_secondary": es,
            "alias_ratio": ref["alias_ratio"], "alias_period": ref["alias_period"],
        }
        cand["shape_dbic"], cand["oot_ratio"] = vetting.shape_test(t, f, e, P, T0, dur)
        cand["physical"] = vetting.physical(star, P, depth, dur)
        cand["rp_err"] = cand["physical"]["rp_re"] * 0.5 * derr / depth
        cand["tests"] = vetting.run_tests(cand, star, season_rows, transits, len(seasons))
        cand["match"] = archive.cross_match(P, planets, tois)
        cand["verdict"] = vetting.verdict(cand["tests"], cand["match"])
        cand["transits"] = transits
        cand["seasons"] = season_rows
        cand["plots"] = {
            "fold": _fold_plot(t, f, e, P, T0, dur),
            "odd": _binned_fold(t[odd], f[odd], P, T0, dur),
            "even": _binned_fold(t[~odd], f[~odd], P, T0, dur),
            "secondary": _binned_fold(t, f, P, T0, dur, center=P / 2),
            "zoom": {"period": _r(ref["zoom_periods"][::max(1, len(ref["zoom_periods"]) // 2000)], 7),
                     "power": _r(ref["zoom_power"][::max(1, len(ref["zoom_power"]) // 2000)], 3)},
        }
        found.append(cand)
        v = cand["verdict"]
        step("vet", base_pct + span,
             f"Candidate {cand['id']}: P = {P:.5f} d, depth {depth * 1e6:.0f} ppm, "
             f"Rp ≈ {cand['physical']['rp_re']:.2f} R⊕, SNR {cand['snr']:.1f} → {v['label']}")
        planet_mask |= tmask

    # 5. Package ---------------------------------------------------------------
    step("report", 97, "Building report…")
    disp = OK
    tb, fb, _ = search.bin_lc(T[disp], FR[disp], E[disp], 30 / 1440)
    tf, ff, _ = search.bin_lc(T[disp], F[disp], E[disp], 30 / 1440)
    secb = np.interp(tb, T, SEC).round().astype(int)
    result = {
        "version": 1,
        "created": datetime.now().isoformat(timespec="seconds"),
        "target": star,
        "options": opts,
        "known": {"planets": planets, "tois": tois},
        "data": {
            "sectors": [{"sector": c["sector"], "author": c["author"], "exptime": c["exptime"],
                         "season": c["season"], "points": int(len(c["t"])),
                         "year": round(float(data.btjd_to_year(c["t"][0])), 2)} for c in chunks],
            "seasons": seasons,
            "baseline_days": float(T[-1] - T[0]),
            "points": int(OK.sum()),
        },
        "plots": {
            "raw": {"t": _r(tb, 4), "f": _r(fb, 6), "sector": secb.tolist()},
            "flat": {"t": _r(tf, 4), "f": _r(ff, 6)},
            "periodograms": periodograms,
        },
        "candidates": found,
        "runtime_s": round(time.time() - t_start, 1),
        "log": log_lines,
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    name = f"TIC{tic}_{datetime.now():%Y%m%d_%H%M%S}.json"
    with open(RESULTS_DIR / name, "w") as fh:
        json.dump(_clean(result), fh)
    result["file"] = name
    step("done", 100, f"Done in {result['runtime_s']} s — {len(found)} candidate(s). Saved {name}")
    return result


def _clean(o):
    """JSON-safe: numpy -> python, NaN/inf -> None."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        o = float(o)
        return o if np.isfinite(o) else None
    if isinstance(o, np.ndarray):
        return _clean(o.tolist())
    if isinstance(o, np.bool_):
        return bool(o)
    return o
