"""Command-line runner:  .venv/bin/python cli.py "TOI-270" [--max-planets 3] [--pmax 20]"""
import argparse
import warnings

warnings.filterwarnings("ignore")

from planet_finder import pipeline  # noqa: E402

ap = argparse.ArgumentParser(description="Search a star's TESS data for transiting planets")
ap.add_argument("target")
ap.add_argument("--pmin", type=float, default=0.5)
ap.add_argument("--pmax", type=float, default=20.0)
ap.add_argument("--max-planets", type=int, default=3)
ap.add_argument("--max-sectors", type=int, default=0)
a = ap.parse_args()

res = pipeline.run(a.target, {"period_min": a.pmin, "period_max": a.pmax, "max_planets": a.max_planets,
                              "max_sectors": a.max_sectors},
                   progress=lambda step, pct, msg: print(f"[{pct:5.1f}%] {msg}", flush=True))
print()
for c in res["candidates"]:
    v = c["verdict"]
    print(f"Candidate {c['id']}: P={c['period']:.5f} d  depth={c['depth_ppm']:.0f} ppm  "
          f"Rp={c['physical']['rp_re']:.2f} Re  SNR={c['snr']:.1f}  -> {v['label']} (score {v['score']})")
    for t in c["tests"]:
        print(f"    [{t['status']:4}] {t['name']}: {t['value']}")
