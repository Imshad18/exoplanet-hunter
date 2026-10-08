"""Measurements and false-positive tests for a transit candidate.

Each test returns {"name", "status": pass|warn|fail|info, "value", "detail"}.
These are the standard automated checks (in the spirit of the TESS/Kepler
DV reports and robovetters). Passing them makes a *strong candidate*; actual
confirmation still needs follow-up (radial velocities, imaging, ground photometry).
"""
import numpy as np

from .data import btjd_to_year
from .search import robust_std

G = 6.674e-11
MSUN, RSUN, AU = 1.989e30, 6.957e8, 1.496e11
REARTH_PER_RSUN = 109.2


def phase_days(t, period, t0):
    return ((t - t0 + 0.5 * period) % period) - 0.5 * period


def _wmean(f, e):
    w = 1 / e ** 2
    return np.sum(w * f) / np.sum(w), 1 / np.sqrt(np.sum(w))


def depth_at(t, f, e, period, t0, duration, center=0.0, beta=1.0):
    """Weighted in-transit depth vs nearby out-of-transit level, with its 1-sigma error.

    `center` shifts the window in phase (days), e.g. period/2 for a secondary eclipse.
    `beta` inflates the error for correlated (red) noise.
    """
    ph = phase_days(t, period, t0 + center)
    inn = np.abs(ph) < duration / 2 * 0.8  # transit core, avoids ingress/egress
    out = (np.abs(ph) > duration) & (np.abs(ph) < max(3 * duration, 0.25))
    if inn.sum() < 3 or out.sum() < 10:
        return np.nan, np.nan, int(inn.sum())
    fi, ei = _wmean(f[inn], e[inn])
    fo, eo = _wmean(f[out], e[out])
    return float(fo - fi), float(np.hypot(ei, eo) * beta), int(inn.sum())


def red_noise_beta(t, f, e, duration, mask):
    """Ratio of real scatter at the transit timescale to the white-noise expectation."""
    from .search import bin_lc
    tt, ff, ee = t[~mask], f[~mask], e[~mask]
    if len(tt) < 200:
        return 1.0
    tb, fb, eb = bin_lc(tt, ff, ee, duration)
    good = eb > 0
    if good.sum() < 20:
        return 1.0
    ratio = robust_std((fb[good] - 1) / eb[good])
    return float(np.clip(ratio, 1.0, 5.0))


def _test(name, status, value, detail):
    return {"name": name, "status": status, "value": value, "detail": detail}


def per_transit(t, f, e, period, t0, duration, beta=1.0):
    """Depth of each individual transit event with decent coverage."""
    epoch = np.round((t - t0) / period).astype(int)
    ph = t - (t0 + epoch * period)
    out = []
    for ep in np.unique(epoch[np.abs(ph) < duration / 2]):
        sel = epoch == ep
        inn = sel & (np.abs(ph) < duration / 2 * 0.8)
        oot = sel & (np.abs(ph) > duration) & (np.abs(ph) < max(3 * duration, 0.25))
        if inn.sum() < 3 or oot.sum() < 6:
            continue
        cad = np.median(np.diff(t[sel]))
        if inn.sum() < 0.5 * duration * 0.8 / cad:
            continue
        fi, ei = _wmean(f[inn], e[inn])
        fo, eo = _wmean(f[oot], e[oot])
        tc = t0 + ep * period
        out.append({"epoch": int(ep), "t": float(tc), "year": float(btjd_to_year(tc)),
                    "depth": float(fo - fi), "err": float(np.hypot(ei, eo) * beta)})
    return out


def shape_test(t, f, e, period, t0, duration, nbins=120):
    """Box transit vs smooth sinusoid (2 harmonics) on the full phase curve.

    Returns (delta_bic, oot_amp_ratio): delta_bic > 0 favours a transit; oot_amp_ratio is
    the out-of-transit modulation amplitude relative to the transit depth.
    """
    ph = (t - t0) / period % 1.0
    ph[ph > 0.5] -= 1.0
    edges = np.linspace(-0.5, 0.5, nbins + 1)
    idx = np.clip(np.digitize(ph, edges) - 1, 0, nbins - 1)
    w = 1 / e ** 2
    sw = np.bincount(idx, w, nbins)
    good = sw > 0
    fb = np.bincount(idx, w * f, nbins)[good] / sw[good]
    eb = 1 / np.sqrt(sw[good])
    x = ((edges[:-1] + edges[1:]) / 2)[good]
    box = (np.abs(x) < duration / period / 2).astype(float)
    if box.sum() == 0:
        box[np.argmin(np.abs(x))] = 1.0

    def chi2(cols):
        A = np.vstack(cols).T / eb[:, None]
        coef, *_ = np.linalg.lstsq(A, fb / eb, rcond=None)
        return float(np.sum((A @ coef - fb / eb) ** 2)), coef

    one = np.ones_like(x)
    c_box, coef_box = chi2([one, box])
    sines = [one] + [g(2 * np.pi * k * x) for k in (1, 2) for g in (np.cos, np.sin)]
    c_sin, _ = chi2(sines)
    n = len(x)
    bic_box, bic_sin = c_box + 2 * np.log(n), c_sin + 5 * np.log(n)
    oot = np.abs(x) > duration / period
    if oot.sum() > 10:
        A = np.vstack([one[oot], np.cos(2 * np.pi * x[oot]), np.sin(2 * np.pi * x[oot]),
                       np.cos(4 * np.pi * x[oot]), np.sin(4 * np.pi * x[oot])]).T
        coef, *_ = np.linalg.lstsq(A, fb[oot], rcond=None)
        amp = float(np.ptp(A @ coef))
    else:
        amp = 0.0
    depth = max(-coef_box[1], 1e-12)
    return float(bic_sin - bic_box), amp / depth


def physical(star, period, depth, duration):
    """Planet radius, orbit and temperature from transit + stellar parameters."""
    R, M, Teff = star["radius"], star["mass"], star["teff"]
    dil = 1.0 + (star.get("contratio") or 0.0)  # flux from nearby stars dilutes the dip
    true_depth = max(depth, 0) * dil
    rp_re = R * np.sqrt(true_depth) * REARTH_PER_RSUN
    a = (G * M * MSUN * (period * 86400) ** 2 / (4 * np.pi ** 2)) ** (1 / 3)
    a_rs = a / (R * RSUN)
    t_exp = period / np.pi * np.arcsin(min(1.0, (1 + np.sqrt(true_depth)) / a_rs))
    teq = Teff * np.sqrt(1 / (2 * a_rs)) * (1 - 0.3) ** 0.25
    insol = R ** 2 * (Teff / 5772) ** 4 / (a / AU) ** 2
    return {"rp_re": float(rp_re), "rp_rj": float(rp_re / 11.21), "a_au": float(a / AU),
            "a_rs": float(a_rs), "teq_k": float(teq), "insolation": float(insol),
            "expected_duration_h": float(t_exp * 24), "true_depth": float(true_depth)}


def run_tests(cand, star, season_rows, transits, n_seasons_with_data):
    P = cand["period"]
    tests = []

    s = cand["sde"]
    tests.append(_test("Signal Detection Efficiency", "pass" if s >= 9 else "warn" if s >= 7 else "fail",
                       f"{s:.1f}", "Peak height above periodogram noise (TESS/TLS threshold ≈ 9)."))

    snr = cand["snr"]
    tests.append(_test("Transit SNR", "pass" if snr >= 7.1 else "warn" if snr >= 5 else "fail",
                       f"{snr:.1f}", "Combined depth significance across all transits (Kepler threshold 7.1)."))

    n = len(transits)
    tests.append(_test("Number of transits", "pass" if n >= 3 else "warn" if n == 2 else "fail",
                       str(n), "Individual transit events with good data coverage."))

    if n >= 2:
        snr2 = np.array([(x["depth"] / x["err"]) ** 2 if x["depth"] > 0 else 0 for x in transits])
        share = float(snr2.max() / snr2.sum()) if snr2.sum() > 0 else 1.0
        tests.append(_test("Single-event dominance", "pass" if share < 0.5 else "warn" if share < 0.75 else "fail",
                           f"{share * 100:.0f}%",
                           "Share of total signal from the strongest single transit; high = one artifact faking a periodic signal."))

    do, eo, de, ee = cand["depth_odd"], cand["err_odd"], cand["depth_even"], cand["err_even"]
    if np.isfinite(do) and np.isfinite(de):
        sig = abs(do - de) / np.hypot(eo, ee)
        tests.append(_test("Odd/even depth", "pass" if sig < 3 else "fail", f"{sig:.1f}σ",
                           f"Odd {do * 1e6:.0f} vs even {de * 1e6:.0f} ppm. A mismatch means an eclipsing binary at 2× the period."))

    ds, es = cand["depth_secondary"], cand["err_secondary"]
    if np.isfinite(ds):
        sig = ds / es
        # A planet's secondary eclipse (reflected/thermal light) is far shallower than its transit
        status = "pass" if sig < 3 else ("warn" if ds < 0.1 * cand["depth"] else "fail")
        tests.append(_test("Secondary eclipse", status, f"{sig:.1f}σ",
                           f"Dip at phase 0.5: {ds * 1e6:.0f} ± {es * 1e6:.0f} ppm. A clear one points to a stellar companion "
                           "(hot Jupiters can show a tiny one)."))

    dbic, oot = cand["shape_dbic"], cand["oot_ratio"]
    status = "fail" if dbic < 0 else "warn" if (dbic < 10 or oot > 0.3) else "pass"
    tests.append(_test("Transit shape vs variability", status, f"ΔBIC {dbic:.0f}",
                       f"Box-shaped dip vs smooth stellar variability (positive favours a transit). "
                       f"Out-of-transit modulation is {oot * 100:.0f}% of the depth"
                       + (" — ellipsoidal binary or spots?" if oot > 0.3 else ".")))

    rp = cand["physical"]["rp_re"]
    tests.append(_test("Planet radius", "pass" if rp < 15 else "warn" if rp < 25 else "fail",
                       f"{rp:.2f} R⊕",
                       "Above ~2 Jupiter radii (≈25 R⊕) the object is a star, not a planet."))

    ratio = cand["duration_h"] / max(cand["physical"]["expected_duration_h"], 1e-3)
    tests.append(_test("Duration vs stellar density",
                       "pass" if 0.15 < ratio < 1.6 else "warn" if ratio < 2.5 else "fail",
                       f"{ratio:.2f}×",
                       f"Observed {cand['duration_h']:.2f} h vs {cand['physical']['expected_duration_h']:.2f} h expected "
                       "for a central transit of this star. Much longer = wrong star or blended binary."))

    if season_rows:
        with_tr = [r for r in season_rows if r["n_in"] > 0 and np.isfinite(r["depth"])]
        detected = [r for r in with_tr if r["snr"] >= 3]
        if len(with_tr) >= 2:
            d = np.array([r["depth"] for r in with_tr])
            e = np.array([r["err"] for r in with_tr])
            w = 1 / e ** 2
            mean = np.sum(w * d) / np.sum(w)
            dev = float(np.max(np.abs(d - mean) / e))
            status = "pass" if dev < 3 and len(detected) == len(with_tr) else "warn" if dev < 4 else "fail"
            tests.append(_test("Recheck: every observing season", status,
                               f"{len(detected)}/{len(with_tr)} seasons",
                               f"Same ephemeris folded independently in each season; max depth deviation {dev:.1f}σ."))
        else:
            tests.append(_test("Recheck: every observing season", "warn", f"{len(with_tr)} season",
                               "Only one observing season available — can't confirm the signal persists across years."))
        recovered = [r for r in season_rows if r.get("indep_period")]
        if recovered:
            ok = [r for r in recovered if abs(r["indep_period"] - P) < max(3 * r["indep_res"], 1e-4 * P)]
            tests.append(_test("Independent period recovery",
                               "pass" if len(ok) == len(recovered) else "warn" if ok else "fail",
                               f"{len(ok)}/{len(recovered)}",
                               "Each season searched on its own: does it find the same period without help?"))

    near = [x for x in (13.7, 27.4, 6.85, 1.0, 0.5) if abs(P / x - 1) < 0.015]
    tests.append(_test("Instrumental period check", "warn" if near else "pass",
                       f"{P:.4f} d", "Close to a TESS orbit / momentum-dump or 1-day alias." if near
                       else "Not near TESS orbital (13.7 d) or diurnal aliases."))

    if cand.get("alias_ratio", 0) > 0.9:
        tests.append(_test("Ephemeris uniqueness", "warn", f"{cand['alias_ratio']:.2f}",
                           f"Alternate period {cand['alias_period']:.5f} d fits almost as well — the cycle count "
                           "across the multi-year gap is ambiguous; more data will pin it down."))

    c = star.get("contratio")
    if c is not None:
        tests.append(_test("Light contamination", "pass" if c < 0.1 else "warn", f"{c * 100:.1f}%",
                           "Flux from neighbouring stars in the TESS aperture (TIC). High values mean the dip "
                           "may come from a different star — centroid/pixel analysis needed."))
    return tests


def verdict(tests, match):
    fails = [t for t in tests if t["status"] == "fail"]
    warns = [t for t in tests if t["status"] == "warn"]
    score = max(0, 100 - 30 * len(fails) - 8 * len(warns))
    if match["kind"] == "confirmed":
        label, tone = "Known planet recovered", "known"
        text = f"Matches confirmed planet {match['name']} — the pipeline independently re-found it."
    elif match["kind"] == "toi" and match.get("disposition") in ("FP", "FA"):
        label, tone = "Known false positive", "fail"
        text = f"Matches {match['name']}, already flagged {match['disposition_text']} by TESS follow-up."
    elif fails:
        label, tone = "Likely false positive", "fail"
        text = "Failed: " + ", ".join(t["name"] for t in fails) + "."
    elif match["kind"] == "toi":
        label, tone = "Known TOI recovered", "known"
        text = (f"Matches {match['name']} ({match['disposition_text']}). "
                + ("All vetting tests pass." if not warns else f"{len(warns)} warning(s)."))
    elif len(warns) <= 1:
        label, tone = "Strong new candidate", "new"
        text = ("Not in the NASA Exoplanet Archive and passes automated vetting. Next step: "
                "check ExoFOP and request follow-up (pixel-level centroid, ground photometry, RV).")
    else:
        label, tone = "Weak new candidate", "warn"
        text = "Not in the archive, but " + ", ".join(t["name"] for t in warns) + " need a closer look."
    return {"label": label, "tone": tone, "text": text, "score": score,
            "n_fail": len(fails), "n_warn": len(warns)}
