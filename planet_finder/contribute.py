"""From candidate to Community TOI: readiness checks, Research Note draft, ExoFOP parameter sheet."""
import csv
import io
import json
import zipfile
from datetime import datetime, timezone

REPO_URL = "https://github.com/Imshad18/exoplanet-hunter"


def _cand(res, cid):
    for c in res["candidates"]:
        if c["id"] == cid:
            return c
    raise KeyError(cid)


def epoch_err(c):
    """Approximate transit-time uncertainty: half the duration over the SNR."""
    return c["duration_h"] / 24 / 2 / max(c["snr"], 1)


def checks(res, c):
    out = []

    def add(name, status, value, detail):
        out.append({"name": name, "status": status, "value": value, "detail": detail})

    m = c["match"]
    if m["kind"] == "none":
        add("Not already known", "pass", "no match", "No confirmed planet, TOI or community TOI on this star at this period or a harmonic.")
    else:
        add("Not already known", "fail", m["name"], f"Already listed ({m.get('disposition_text') or m['kind']}). ExoFOP removes duplicates; "
            "if you have better parameters, add them as a new parameter set to the existing object instead.")
    v = c["verdict"]
    add("Automated vetting", "pass" if v["n_fail"] == 0 else "fail", f"{v['n_fail']} fail · {v['n_warn']} warn",
        "All false-positive tests in this app should pass before you spend anyone's telescope time.")
    four = [c["period"], c["t0_bjd"], c["depth_ppm"], c["duration_h"]]
    add("Period, epoch, depth and duration measured", "pass" if all(x and x > 0 for x in four) else "fail", "4 of 4" if all(four) else "missing",
        "ExoFOP's TOI working group needs all four, greater than zero, to consider a community TOI.")
    add("Signal-to-noise", "pass" if c["snr"] >= 10 else "warn" if c["snr"] >= 7.1 else "fail", f"{c['snr']:.1f}",
        "TESS TOIs are usually above 7; candidates around 10 or more get much more follow-up interest.")
    ns = sum(1 for s in c["seasons"] if s["snr"] and s["snr"] >= 3)
    add("Seen in more than one observing season", "pass" if ns >= 2 else "warn", f"{ns} season(s)",
        "Independent detection in separate years is strong evidence the signal is astrophysical.")
    cont = res["target"].get("contratio")
    add("Light from neighbouring stars", "pass" if (cont or 0) < 0.1 else "warn", f"{(cont or 0) * 100:.1f}%",
        "High contamination means the dip could come from a nearby star; mention it in the note.")
    fails = [x for x in out if x["status"] == "fail"]
    if m["kind"] != "none":
        verdict = ("known", "Already known", "This signal is already catalogued, so it can't be submitted as a new candidate.")
    elif fails:
        verdict = ("fail", "Not ready", "Fix or explain: " + ", ".join(x["name"] for x in fails) + ".")
    else:
        verdict = ("pass", "Ready to write up", "Write the research note, publish it, then upload the candidate to ExoFOP.")
    return {"checks": out, "verdict": {"tone": verdict[0], "label": verdict[1], "text": verdict[2]}}


def figure(res, c):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 8, "font.family": "DejaVu Sans", "axes.spines.top": False, "axes.spines.right": False})
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 2.6), gridspec_kw={"width_ratios": [1.3, 1]})
    f = c["plots"]["fold"]
    a1.plot(f["x"], f["y"], ".", ms=1, color="0.7", rasterized=True)
    a1.plot(f["bx"], f["by"], "o", ms=3, color="#b4530f")
    hw, d = c["duration_h"] / 2, c["depth"]
    a1.plot([min(f["x"]), -hw, -hw, hw, hw, max(f["x"])], [1, 1, 1 - d, 1 - d, 1, 1], color="k", lw=0.8)
    a1.set_xlabel("Hours from mid-transit")
    a1.set_ylabel("Relative flux")
    lo = min(min(f["by"]), 1 - 1.6 * d)
    a1.set_ylim(lo - 0.1 * (1 - lo), 1 + 0.6 * d)
    a1.set_title(f"P = {c['period']:.5f} d", fontsize=8)
    tr = c["transits"]
    a2.errorbar([t["year"] for t in tr], [t["depth"] * 1e6 for t in tr], [t["err"] * 1e6 for t in tr], fmt="o", ms=2.5, color="#4e79a7", lw=0.6)
    a2.axhline(c["depth_ppm"], color="#b4530f", ls="--", lw=0.8)
    a2.set_xlabel("Year")
    a2.set_ylabel("Individual transit depth (ppm)")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=220)
    plt.close(fig)
    return buf.getvalue()


def _tex(s):
    return str(s).replace("&", r"\&").replace("%", r"\%").replace("_", r"\_").replace("#", r"\#")


def research_note(res, c, author):
    """AASTeX Research Note draft (RNAAS: up to 1,000 words and one figure)."""
    t, ph = res["target"], c["physical"]
    seasons = ", ".join(s["years"] for s in c["seasons"])
    name = _tex(author.get("name") or "Author Name")
    aff = _tex(author.get("affiliation") or "Independent researcher")
    orcid = f"[{author['orcid']}]" if author.get("orcid") else ""
    email_line = ("\\email{" + _tex(author["email"]) + "}") if author.get("email") else ""
    dist = f"{t['dist_pc']:.0f} pc" if t.get("dist_pc") else "unknown distance"
    sectors = ", ".join(str(s["sector"]) for s in res["data"]["sectors"])
    tests = "; ".join(f"{_tex(x['name'])} ({_tex(x['value'])})" for x in c["tests"])
    return rf"""\documentclass[RNAAS]{{aastex631}}
\begin{{document}}

\title{{A transiting planet candidate around TIC {t['tic']} from multi-year TESS photometry}}

\author{orcid}{{{name}}}
\affiliation{{{aff}}}
\correspondingauthor{{{name}}}
{email_line}

\keywords{{Exoplanet detection methods (489) --- Transit photometry (1709) --- Exoplanets (498)}}

\section{{Introduction}}
The Transiting Exoplanet Survey Satellite \citep[TESS;][]{{Ricker2015}} has re-observed much of the sky in several
years, so shallow periodic transits that are marginal in a single sector can become significant when all
sectors are combined. Here we report a candidate transiting planet around TIC {t['tic']} ($T={t.get('tmag') or 0:.2f}$, {dist})
that is not listed as a TOI, community TOI or confirmed planet.

\section{{Data and method}}
We used TESS light curves from sectors {sectors} (SPOC, TESS-SPOC or QLP products obtained from MAST with
\texttt{{lightkurve}}; \citealt{{Lightkurve2018}}), spanning {res['data']['baseline_days'] / 365.25:.1f} years in {len(res['data']['seasons'])} observing
seasons ({seasons}). Stellar variability was removed with a robust running median, and the transit search used the
box least-squares method \citep{{Kovacs2002}}, combining per-season periodograms before a coherent fit across all
data. The signal was vetted with standard tests (odd/even depths, secondary eclipse, transit shape, single-event
dominance, duration versus stellar density) and measured independently in each observing season.
Stellar parameters are from the TESS Input Catalog \citep{{Stassun2019}}. Code: \url{{{REPO_URL}}}.

\section{{Results}}
We find a transit signal with period $P = {c['period']:.6f} \pm {c['period_err']:.6f}$~d, mid-transit time
$T_0 = {c['t0_bjd']:.5f} \pm {epoch_err(c):.5f}$~BJD$_\mathrm{{TDB}}$, depth ${c['depth_ppm']:.0f} \pm {c['depth_err'] * 1e6:.0f}$~ppm and
duration ${c['duration_h']:.2f}$~h, detected with SNR~{c['snr']:.1f} in {len(c['transits'])} individual transits.
With $R_\star = {t['radius']:.2f}\,R_\odot$ this corresponds to $R_p \approx {ph['rp_re']:.2f} \pm {c['rp_err']:.2f}\,R_\oplus$,
an orbital separation of {ph['a_au']:.4f}~au and an equilibrium temperature of about {ph['teq_k']:.0f}~K.
Figure~\ref{{fig:lc}} shows the phase-folded light curve and the depth of every individual transit.
Vetting results: {tests}.

\begin{{figure}}[ht!]
\plotone{{figure.png}}
\caption{{Left: TESS photometry of TIC {t['tic']} folded at the candidate period, with binned data and a box model.
Right: depth of each individual transit versus time, with the mean depth (dashed).\label{{fig:lc}}}}
\end{{figure}}

\section{{Discussion}}
The signal is present in every observing season with a consistent depth, which argues against an instrumental
origin. A blended eclipsing binary cannot be excluded from TESS photometry alone; we encourage
ground-based photometry at the predicted transit times, high-resolution imaging and radial-velocity follow-up.
{('Note that ' + f"{t['contratio'] * 100:.0f}" + r'\% of the flux in the TESS aperture is expected from neighbouring stars.') if (t.get('contratio') or 0) > 0.05 else ''}

\begin{{acknowledgments}}
This paper includes data collected by the TESS mission, which are publicly available from the Mikulski Archive
for Space Telescopes (MAST). Funding for the TESS mission is provided by NASA's Science Mission Directorate.
\end{{acknowledgments}}

\begin{{thebibliography}}{{}}
\bibitem[Kov{{\'a}}cs et al.(2002)]{{Kovacs2002}} Kov{{\'a}}cs, G., Zucker, S., \& Mazeh, T. 2002, A\&A, 391, 369
\bibitem[Lightkurve Collaboration(2018)]{{Lightkurve2018}} Lightkurve Collaboration 2018, Astrophysics Source Code Library, ascl:1812.013
\bibitem[Ricker et al.(2015)]{{Ricker2015}} Ricker, G.~R., et al. 2015, JATIS, 1, 014003
\bibitem[Stassun et al.(2019)]{{Stassun2019}} Stassun, K.~G., et al. 2019, AJ, 158, 138
\end{{thebibliography}}

\end{{document}}
"""


def exofop_sheet(res, c):
    """Parameters with the column names used in the ExoFOP community-candidate tables."""
    t, ph = res["target"], c["physical"]
    rows = [
        ("TIC ID", t["tic"]), ("Discovery Data Source", "TESS"), ("User Disposition", "PC"),
        ("Period (days)", f"{c['period']:.6f}"), ("Period (days) Error", f"{c['period_err']:.6f}"),
        ("Transit Epoch (BJD)", f"{c['t0_bjd']:.5f}"), ("Transit Epoch (BJD) err", f"{epoch_err(c):.5f}"),
        ("Depth ppm", f"{c['depth_ppm']:.0f}"), ("Depth ppm Error", f"{c['depth_err'] * 1e6:.0f}"),
        ("Duration (hrs)", f"{c['duration_h']:.3f}"), ("Duration (hrs) Error", f"{c['duration_h'] * 0.1:.3f}"),
        ("Planet Radius (R_Earth)", f"{ph['rp_re']:.2f}"), ("Planet Radius (R_Earth) Error", f"{c['rp_err']:.2f}"),
        ("Insolation (Earth Flux)", f"{ph['insolation']:.1f}"), ("Equilibrium Temp (K)", f"{ph['teq_k']:.0f}"),
        ("Semi-Major Axis (AU)", f"{ph['a_au']:.4f}"),
        ("Notes", f"Multi-season BLS detection, SNR {c['snr']:.1f}, {len(c['transits'])} transits; {REPO_URL}"),
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([r[0] for r in rows])
    w.writerow([r[1] for r in rows])
    return buf.getvalue()


def lightcurve_csv(c):
    f = c["plots"]["fold"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["hours_from_midtransit", "relative_flux"])
    for x, y in zip(f["x"], f["y"]):
        w.writerow([x, y])
    return buf.getvalue()


def package(res, cid, author):
    c = _cand(res, cid)
    chk = checks(res, c)
    readme = f"""# Planet candidate TIC {res['target']['tic']}.{cid:02d}

Verdict: {chk['verdict']['label']}. {chk['verdict']['text']}

Steps to a Community TOI:
1. Compile note/rnaas.tex (with figure.png) on Overleaf and submit it to Research Notes of the AAS
   (https://journals.aas.org/research-notes/). RNAAS notes are citable and indexed in ADS.
2. Once it is published, request upload rights at https://exofop.ipac.caltech.edu/tess/pub_candidate_upload_request.php
3. Create the candidate on ExoFOP with the values in exofop_parameters.csv, attach figure.png to the TIC page with the
   same tag, and add the RNAAS link.

Files: note/rnaas.tex, note/figure.png, exofop_parameters.csv, folded_lightcurve.csv, candidate.json.
Checks:
""" + "\n".join(f"- [{x['status']}] {x['name']}: {x['value']}" for x in chk["checks"]) + f"\n\nGenerated by {REPO_URL} on {datetime.now(timezone.utc):%Y-%m-%d}.\n"
    fig = figure(res, c)
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
        f.writestr("README.md", readme)
        f.writestr("note/rnaas.tex", research_note(res, c, author))
        f.writestr("note/figure.png", fig)
        f.writestr("exofop_parameters.csv", exofop_sheet(res, c))
        f.writestr("folded_lightcurve.csv", lightcurve_csv(c))
        f.writestr("candidate.json", json.dumps({"target": res["target"], "candidate": {k: v for k, v in c.items() if k != "plots"},
                                                 "checks": chk}, indent=1, default=str))
    return z.getvalue(), chk


def zenodo_metadata(res, cid, author, creator):
    c = _cand(res, cid)
    t = res["target"]
    return {
        "upload_type": "dataset",
        "title": f"Transiting planet candidate TIC {t['tic']}.{cid:02d}: P = {c['period']:.4f} d, Rp ≈ {c['physical']['rp_re']:.1f} Earth radii",
        "creators": [creator],
        "description": (f"<p>Candidate found in multi-year TESS photometry: period {c['period']:.6f} d, epoch {c['t0_bjd']:.5f} BJD, "
                        f"depth {c['depth_ppm']:.0f} ppm, duration {c['duration_h']:.2f} h, SNR {c['snr']:.1f}. Includes a research-note draft, "
                        f"figure, ExoFOP parameter sheet and folded light curve.</p><p>Software: {REPO_URL}</p>"),
        "keywords": ["exoplanets", "TESS", "transit photometry", "planet candidate"],
        "license": "cc-by-4.0",
        "related_identifiers": [{"identifier": REPO_URL, "relation": "isSupplementedBy", "resource_type": "software"}],
    }
