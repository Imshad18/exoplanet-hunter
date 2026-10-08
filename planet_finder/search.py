"""Detrending and Box Least Squares transit search.

Search strategy
---------------
TESS revisits a star every year or two, so the full baseline can span 7+ years with
huge gaps. A coherent BLS over that baseline needs an impractically fine period grid,
so discovery is done *incoherently*: each observing season is searched on a common
period grid and the per-season log-likelihoods are summed. A real planet adds power
at the same period in every season; a one-off artifact does not. The best peak is then
refined *coherently* across all seasons to get a precise ephemeris.
"""
import numpy as np
from astropy.timeseries import BoxLeastSquares

DURATIONS_H = np.array([0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.5, 8.0])


def detrend(t, f, window=1.0, mask=None, gap=0.5):
    """Divide out a time-windowed running median (robust to transits; masked points ignored)."""
    use = np.ones(len(t), bool) if mask is None else ~mask
    trend = np.full(len(t), np.nan)
    breaks = np.concatenate([[0], np.where(np.diff(t) > gap)[0] + 1, [len(t)]])
    step = window / 3.0
    for s, e in zip(breaks[:-1], breaks[1:]):
        ts, fs, us = t[s:e], f[s:e], use[s:e]
        if e - s < 10:
            continue
        knots = np.arange(ts[0], ts[-1] + step, step)
        if len(knots) < 2:
            knots = np.array([ts[0], ts[-1]])
        lo = np.searchsorted(ts, knots - window / 2)
        hi = np.searchsorted(ts, knots + window / 2)
        kv = np.full(len(knots), np.nan)
        for k in range(len(knots)):
            seg = fs[lo[k]:hi[k]][us[lo[k]:hi[k]]]
            if len(seg) >= 5:
                kv[k] = np.median(seg)
        good = np.isfinite(kv)
        if good.sum() == 0:
            continue
        trend[s:e] = np.interp(ts, knots[good], kv[good])
    return f / trend


def trim_edges(t, after_gap=1.0, trim=0.25):
    """Mask the first hours after each data gap, where TESS scattered-light ramps live."""
    keep = np.ones(len(t), bool)
    starts = np.concatenate([[0], np.where(np.diff(t) > after_gap)[0] + 1])
    for s in starts:
        keep &= ~((t >= t[s]) & (t < t[s] + trim))
    return keep


def robust_std(x):
    return 1.4826 * np.median(np.abs(x - np.median(x)))


def clip_upper(f, sigma=3.0):
    return f < 1 + sigma * robust_std(f)


def bin_lc(t, f, e, size):
    if len(t) == 0:
        return t, f, e
    idx = np.floor((t - t[0]) / size).astype(np.int64)
    _, inv, n = np.unique(idx, return_inverse=True, return_counts=True)
    tb = np.bincount(inv, t) / n
    fb = np.bincount(inv, f) / n
    eb = np.sqrt(np.bincount(inv, e * e)) / n
    return tb, fb, eb


def log_period_grid(pmin, pmax, baseline, dur_min, oversample=2.0, cap=150_000):
    """Log-spaced grid fine enough that phase drift across `baseline` stays < dur_min/oversample."""
    dlnp = dur_min / (oversample * baseline)
    n = int(np.log(pmax / pmin) / dlnp) + 1
    n = int(np.clip(n, 2000, cap))
    return np.exp(np.linspace(np.log(pmin), np.log(pmax), n))


def _flatten_power(power, block=400):
    """Subtract a running median so long periods (more freedom) don't dominate."""
    n = len(power)
    nb = max(1, n // block)
    centers, meds = [], []
    for i in range(nb):
        s, e = i * n // nb, (i + 1) * n // nb
        centers.append((s + e) / 2)
        meds.append(np.median(power[s:e]))
    trend = np.interp(np.arange(n), centers, meds)
    return power - trend


def sde(power):
    p = power[np.isfinite(power)]
    sd = np.std(p)
    return float((p.max() - np.mean(p)) / sd) if sd > 0 else 0.0


def stacked_search(seasons, pmin, pmax, bin_size=10 / 1440):
    """Incoherent BLS: sum per-season log-likelihood on one period grid.

    `seasons` is a list of (t, f, e) arrays. Returns periods, flattened power, best params.
    """
    binned = [bin_lc(t, f, e, bin_size) for t, f, e in seasons if len(t) > 50]
    longest = max(b[0][-1] - b[0][0] for b in binned)
    pmax = min(pmax, longest / 2.0)
    if pmax <= pmin:
        raise ValueError("Not enough contiguous data to search this period range")
    durs = DURATIONS_H[DURATIONS_H / 24 < pmin * 0.3] / 24
    periods = log_period_grid(pmin, pmax, longest, durs.min())
    total = np.zeros(len(periods))
    for t, f, e in binned:
        base = t[-1] - t[0]
        if base < 2 * pmin:
            continue
        res = BoxLeastSquares(t, f, dy=e).power(periods, durs, objective="likelihood")
        p = np.where(np.asarray(res.depth) > 0, np.asarray(res.power), 0.0)
        p[periods > base / 2] = 0.0  # a season must cover >=2 transits to contribute
        total += p
    flat = _flatten_power(total)
    i = int(np.argmax(flat))
    return periods, flat, {"period": float(periods[i]), "sde": sde(flat),
                           "dlnp": float(np.log(periods[1] / periods[0]))}


def refine(t, f, e, p0, dlnp_coarse, bin_size=5 / 1440):
    """Coherent BLS across all seasons in a narrow window around p0."""
    tb, fb, eb = bin_lc(t, f, e, bin_size)
    baseline = tb[-1] - tb[0]
    durs = DURATIONS_H[DURATIONS_H / 24 < p0 * 0.3] / 24
    half = max(4 * dlnp_coarse * p0, 1e-4 * p0)
    step = durs.min() * p0 / (3 * baseline)
    n = int(np.clip(2 * half / step, 200, 30_000))
    periods = np.linspace(p0 - half, p0 + half, n)
    bls = BoxLeastSquares(tb, fb, dy=eb)
    res = bls.power(periods, durs, objective="likelihood")
    power = np.where(np.asarray(res.depth) > 0, np.asarray(res.power), 0.0)
    i = int(np.argmax(power))
    P, dur, T0 = float(res.period[i]), float(res.duration[i]), float(res.transit_time[i])

    # Alias check: is there a separate peak almost as strong (cycle-count ambiguity across gaps)?
    resolution = dur * P / baseline
    far = np.abs(periods - P) > 3 * resolution
    alias_ratio = float(power[far].max() / power[i]) if far.any() and power[i] > 0 else 0.0
    alias_period = float(periods[far][np.argmax(power[far])]) if far.any() else None

    # Fine-tune duration with a denser grid at the chosen period
    fine_d = np.linspace(dur * 0.6, min(dur * 1.6, P * 0.3), 25)
    r2 = bls.power([P], fine_d, objective="likelihood")
    dur, T0 = float(r2.duration[0]), float(r2.transit_time[0])

    # Move T0 to the middle of the data so the ephemeris error is smallest
    mid = 0.5 * (t[0] + t[-1])
    T0 = T0 + np.round((mid - T0) / P) * P
    return {"period": P, "t0": float(T0), "duration": dur,
            "period_err": float(resolution / 3), "alias_ratio": alias_ratio,
            "alias_period": alias_period, "zoom_periods": periods, "zoom_power": power}


def local_period(t, f, e, p0, frac=0.02, bin_size=10 / 1440):
    """Independent BLS of one season in a +/- frac window: does it find the period on its own?"""
    tb, fb, eb = bin_lc(t, f, e, bin_size)
    base = tb[-1] - tb[0]
    if base < 2 * p0:
        return None
    durs = DURATIONS_H[DURATIONS_H / 24 < p0 * 0.3] / 24
    step = durs.min() * p0 / (3 * base)
    n = int(np.clip(2 * frac * p0 / step, 300, 20_000))
    periods = np.linspace(p0 * (1 - frac), p0 * (1 + frac), n)
    res = BoxLeastSquares(tb, fb, dy=eb).power(periods, durs, objective="likelihood")
    power = np.where(np.asarray(res.depth) > 0, np.asarray(res.power), 0.0)
    i = int(np.argmax(power))
    return {"period": float(periods[i]), "resolution": float(durs.min() * p0 / base)}


def transit_mask(t, period, t0, duration, factor=1.5):
    ph = np.abs(((t - t0 + 0.5 * period) % period) - 0.5 * period)
    return ph < factor * duration / 2
