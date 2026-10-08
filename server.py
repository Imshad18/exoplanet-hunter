"""Web server: job queue around the pipeline + static UI.

Run:  .venv/bin/python server.py   then open http://localhost:8000
"""
import json
import queue
import threading
import time
import traceback
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from planet_finder import archive, contribute, pipeline, zenodo

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Exoplanet Hunter")

jobs = {}
job_queue = queue.Queue()
lock = threading.Lock()


class JobRequest(BaseModel):
    target: str
    options: dict = {}


def worker():
    while True:
        jid = job_queue.get()
        job = jobs.get(jid)
        if not job or job["status"] == "cancelled":
            continue
        job["status"] = "running"
        job["started"] = time.time()

        def progress(step, pct, msg):
            with lock:
                job["step"], job["pct"] = step, round(pct, 1)
                job["log"].append(msg)

        try:
            res = pipeline.run(job["target"], job["options"], progress=progress,
                               cancel=lambda: job["status"] == "cancelling")
            job["result_file"] = res["file"]
            job["summary"] = [{"period": c["period"], "rp": c["physical"]["rp_re"],
                               "label": c["verdict"]["label"], "tone": c["verdict"]["tone"]}
                              for c in res["candidates"]]
            job["tic"] = res["target"]["tic"]
            job["status"] = "done"
        except pipeline.Cancelled:
            job["status"] = "cancelled"
            job["log"].append("Cancelled.")
        except Exception as exc:
            job["status"] = "error"
            job["error"] = str(exc)
            job["log"].append(f"ERROR: {exc}")
            traceback.print_exc()
        job["finished"] = time.time()


threading.Thread(target=worker, daemon=True).start()


def _public(job, with_log=True):
    j = {k: v for k, v in job.items() if k != "log"}
    if with_log:
        j["log"] = job["log"][-400:]
    return j


@app.post("/api/jobs")
def create_job(req: JobRequest):
    target = req.target.strip()
    if not target:
        raise HTTPException(400, "Empty target")
    jid = uuid.uuid4().hex[:10]
    jobs[jid] = {"id": jid, "target": target, "options": req.options, "status": "queued",
                 "step": "queued", "pct": 0, "log": [], "created": time.time()}
    job_queue.put(jid)
    return _public(jobs[jid])


@app.get("/api/jobs")
def list_jobs():
    return sorted((_public(j, False) for j in jobs.values()), key=lambda j: -j["created"])


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    if jid not in jobs:
        raise HTTPException(404)
    return _public(jobs[jid])


@app.delete("/api/jobs/{jid}")
def cancel_job(jid: str):
    job = jobs.get(jid)
    if not job:
        raise HTTPException(404)
    if job["status"] == "queued":
        job["status"] = "cancelled"
    elif job["status"] == "running":
        job["status"] = "cancelling"
    return _public(job, False)


@app.get("/api/results")
def list_results():
    out = []
    for p in sorted(pipeline.RESULTS_DIR.glob("*.json"), reverse=True):
        try:
            r = json.loads(p.read_text())
            out.append({"file": p.name, "created": r["created"], "tic": r["target"]["tic"],
                        "input": r["target"].get("input"),
                        "candidates": [{"period": c["period"], "rp": c["physical"]["rp_re"],
                                        "label": c["verdict"]["label"], "tone": c["verdict"]["tone"]}
                                       for c in r["candidates"]]})
        except Exception:
            continue
    return out


@app.get("/api/results/{name}")
def get_result(name: str):
    p = pipeline.RESULTS_DIR / Path(name).name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/json")


@app.delete("/api/results/{name}")
def delete_result(name: str):
    p = pipeline.RESULTS_DIR / Path(name).name
    if p.exists():
        p.unlink()
    return {"ok": True}


@app.get("/api/toi-candidates")
def toi_candidates(n: int = 24):
    try:
        return archive.candidate_tois(n)
    except Exception as exc:
        raise HTTPException(502, f"NASA Exoplanet Archive unavailable: {exc}")


# ------------------------------------------------------------------ contribution
class Author(BaseModel):
    name: str = ""
    affiliation: str = ""
    email: str = ""
    orcid: str = ""


class ZenodoReq(BaseModel):
    author: Author
    token: str
    sandbox: bool = True


class PublishReq(BaseModel):
    id: int
    token: str
    sandbox: bool = True


def _result(name):
    p = pipeline.RESULTS_DIR / Path(name).name
    if not p.exists():
        raise HTTPException(404)
    return json.loads(p.read_text())


@app.get("/api/contrib/{name}/{cid}/checks")
def contrib_checks(name: str, cid: int):
    res = _result(name)
    return contribute.checks(res, contribute._cand(res, cid))


@app.post("/api/contrib/{name}/{cid}/package")
def contrib_package(name: str, cid: int, author: Author):
    res = _result(name)
    data, _ = contribute.package(res, cid, author.model_dump())
    fn = f"TIC{res['target']['tic']}_{cid:02d}_candidate.zip"
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{fn}"'})


@app.post("/api/contrib/{name}/{cid}/zenodo")
def contrib_zenodo(name: str, cid: int, req: ZenodoReq):
    res = _result(name)
    data, _ = contribute.package(res, cid, req.author.model_dump())
    meta = contribute.zenodo_metadata(res, cid, req.author.model_dump(), zenodo.creator(req.author.model_dump()))
    try:
        return zenodo.draft({f"TIC{res['target']['tic']}_{cid:02d}_candidate.zip": data}, meta, req.token, req.sandbox)
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.post("/api/zenodo-publish")
def zenodo_publish(req: PublishReq):
    try:
        return zenodo.publish(req.id, req.token, req.sandbox)
    except Exception as exc:
        raise HTTPException(502, str(exc))


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")

if __name__ == "__main__":
    print("Exoplanet Hunter → http://localhost:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
