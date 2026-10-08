/* Exoplanet Hunter front-end */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 2) => (v === null || v === undefined || !isFinite(v) ? "—" : Number(v).toFixed(d));
const SEASON_COLORS = ["#4e79a7", "#e15759", "#59a14f", "#b07aa1", "#f28e2b", "#76b7b2", "#9c755f", "#edc948"];
const CAND_COLORS = ["#d4661a", "#4e79a7", "#59a14f", "#b07aa1", "#e15759", "#76b7b2"];
const BTJD = 2457000;
const STEPS = [["resolve", "Target"], ["download", "Download"], ["detrend", "Detrend"], ["search", "Search"],
               ["refine", "Refine"], ["vet", "Vet & recheck"], ["done", "Report"]];
const QUICK = ["TOI-700", "Pi Men", "TOI-270", "LHS 3844", "TOI-1452", "WASP-18", "TIC 307210830"];

const state = { activeJob: null, result: null, cand: 0, jobs: [], orbitRAF: null };

/* ---------- theme ---------- */
function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("theme"); } catch {}
  if (saved) document.documentElement.dataset.theme = saved;
  $("#themeBtn").onclick = () => {
    const dark = css("--bg") === "#111214";
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("theme", document.documentElement.dataset.theme); } catch {}
    if (state.result && !$("#resultView").classList.contains("hidden")) renderResult();
  };
}

/* ---------- api ---------- */
async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 3200);
}
function options() {
  return {
    period_min: +$("#optPmin").value, period_max: +$("#optPmax").value, max_planets: +$("#optNp").value,
    sde_threshold: +$("#optSde").value, detrend_window: +$("#optWin").value, max_sectors: +$("#optSec").value,
  };
}
async function submit(target, focus = true) {
  const job = await api("/api/jobs", { method: "POST", body: JSON.stringify({ target, options: options() }) });
  if (focus) showJob(job.id);
  refreshJobs();
  return job;
}

/* ---------- sidebar ---------- */
function initSidebar() {
  $("#quickTargets").innerHTML = QUICK.map((q) => `<span class="chip" data-t="${esc(q)}">${esc(q)}</span>`).join("");
  $("#quickTargets").onclick = (e) => { const t = e.target.dataset.t; if (t) { $("#targetInput").value = t; submit(t); } };
  $("#searchForm").onsubmit = (e) => { e.preventDefault(); submit($("#targetInput").value.trim()); };
  $("#batchRun").onclick = async () => {
    const lines = $("#batchInput").value.split("\n").map((s) => s.trim()).filter(Boolean);
    for (const l of lines) await submit(l, false);
    if (lines.length) toast(`Queued ${lines.length} star(s)`);
  };
  $("#loadTois").onclick = loadTois;
  $("#cancelBtn").onclick = () => state.activeJob && api(`/api/jobs/${state.activeJob}`, { method: "DELETE" });
}

async function loadTois() {
  const box = $("#toiList");
  box.innerHTML = `<p class="muted">Querying NASA Exoplanet Archive…</p>`;
  try {
    const rows = await api("/api/toi-candidates");
    box.innerHTML = `<button class="btn small" id="queueTois">Queue all ${rows.length}</button>` + rows.map((r) => `
      <div class="toi-row"><div><b>TOI-${esc(r.toi)}</b> <span>TIC ${esc(r.tid)}</span><br>
      <span>P ${fmt(r.pl_orbper, 3)} d · ${fmt(r.pl_rade, 1)} R⊕ · Tmag ${fmt(r.st_tmag, 1)}${r.st_dist ? ` · ${fmt(r.st_dist * 3.2616, 0)} ly` : ""}</span></div>
      <button class="btn small" data-tic="${esc(r.tid)}">Recheck</button></div>`).join("");
    box.onclick = (e) => {
      if (e.target.dataset.tic) submit(`TIC ${e.target.dataset.tic}`);
      if (e.target.id === "queueTois") { rows.forEach((r) => submit(`TIC ${r.tid}`, false)); toast(`Queued ${rows.length} TOIs`); }
    };
  } catch (err) { box.innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
}

function candTags(cands) {
  if (!cands || !cands.length) return `<span class="tag tone-warn">no signal</span>`;
  return cands.map((c) => `<span class="tag tone-${c.tone}">${fmt(c.period, 2)}d · ${fmt(c.rp, 1)}R⊕</span>`).join("");
}

async function refreshJobs() {
  try { state.jobs = await api("/api/jobs"); } catch { return; }
  const running = state.jobs.filter((j) => ["running", "queued", "cancelling"].includes(j.status));
  const badge = $("#queueBadge");
  badge.textContent = running.length ? `${running.length} in queue` : "Idle";
  badge.classList.toggle("busy", running.length > 0);
  $("#jobList").innerHTML = state.jobs.length ? state.jobs.map((j) => `
    <div class="item ${j.id === state.activeJob ? "active" : ""}" data-job="${j.id}">
      <div class="row"><span class="name">${esc(j.target)}</span><span class="status s-${j.status}">${j.status}</span></div>
      ${j.status === "done" ? `<div class="sub">${candTags(j.summary)}</div>` : ""}
      ${j.status === "error" ? `<div class="sub">${esc(j.error)}</div>` : ""}
      ${["running", "cancelling"].includes(j.status) ? `<div class="mini-bar"><div style="width:${j.pct}%"></div></div>` : ""}
    </div>`).join("") : `<p class="muted">No jobs yet.</p>`;
  $("#jobList").onclick = (e) => {
    const it = e.target.closest("[data-job]"); if (!it) return;
    const j = state.jobs.find((x) => x.id === it.dataset.job);
    if (j.status === "done") openResult(j.result_file); else showJob(j.id);
  };
  if (state.jobs.some((j) => j.status === "done" && !refreshJobs.seen?.has(j.id))) refreshResults();
  refreshJobs.seen = new Set(state.jobs.filter((j) => j.status === "done").map((j) => j.id));
}

async function refreshResults() {
  const rows = await api("/api/results").catch(() => []);
  $("#resultList").innerHTML = rows.length ? rows.map((r) => `
    <div class="item ${state.result?.file === r.file ? "active" : ""}" data-file="${esc(r.file)}">
      <div class="row"><span class="name">${esc(r.input || "TIC " + r.tic)}</span><span class="sub">${esc(r.created.slice(5, 16).replace("T", " "))}</span></div>
      <div class="sub">${candTags(r.candidates)}</div>
    </div>`).join("") : `<p class="muted">None yet.</p>`;
  $("#resultList").onclick = (e) => { const it = e.target.closest("[data-file]"); if (it) openResult(it.dataset.file); };
}

/* ---------- running view ---------- */
function show(view) {
  for (const v of ["emptyView", "runView", "resultView"]) $("#" + v).classList.toggle("hidden", v !== view);
}
function showJob(id) {
  state.activeJob = id; show("runView"); pollJob();
}
async function pollJob() {
  const id = state.activeJob; if (!id) return;
  let j; try { j = await api(`/api/jobs/${id}`); } catch { return; }
  if (state.activeJob !== id || $("#runView").classList.contains("hidden")) return;
  $("#runTitle").textContent = j.target;
  const idx = STEPS.findIndex((s) => s[0] === j.step);
  $("#steps").innerHTML = STEPS.map((s, i) => `<div class="stepx ${i < idx || j.status === "done" ? "ok" : i === idx ? "on" : ""}">${s[1]}</div>`).join("");
  $("#runBar").style.width = `${j.pct}%`;
  const log = $("#runLog"); const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
  log.textContent = j.status === "queued" ? "Waiting in queue…" : j.log.join("\n");
  if (atBottom) log.scrollTop = log.scrollHeight;
  $("#cancelBtn").classList.toggle("hidden", !["queued", "running"].includes(j.status));
  if (j.status === "done") { refreshResults(); return openResult(j.result_file); }
  if (["error", "cancelled"].includes(j.status)) return;
  setTimeout(pollJob, 1200);
}

/* ---------- result view ---------- */
async function openResult(file) {
  const r = await api(`/api/results/${encodeURIComponent(file)}`);
  r.file = file; state.result = r; state.cand = 0; state.activeJob = null;
  history.replaceState(null, "", "#r=" + encodeURIComponent(file));
  show("resultView"); renderResult(); refreshResults(); refreshJobs();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

const btjdToDate = (t) => new Date((t + BTJD - 2440587.5) * 86400000);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const layoutBase = (extra = {}) => ({
  paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
  font: { color: css("--muted"), family: "IBM Plex Mono", size: 11 },
  margin: { l: 58, r: 12, t: 8, b: 38 }, showlegend: false, hovermode: "closest",
  xaxis: { gridcolor: css("--grid"), zerolinecolor: css("--zero"), linecolor: css("--line-2"), ...(extra.xaxis || {}) },
  yaxis: { gridcolor: css("--grid"), zerolinecolor: css("--zero"), linecolor: css("--line-2"), ...(extra.yaxis || {}) },
  ...Object.fromEntries(Object.entries(extra).filter(([k]) => !["xaxis", "yaxis"].includes(k))),
});
const plotCfg = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d"] };

function starName(t) {
  if (t.input && !/^\s*(tic)?[\s-]*\d+\s*$/i.test(t.input)) return t.input;
  return `TIC ${t.tic}`;
}

function renderResult() {
  const r = state.result, t = r.target, c = r.candidates;
  const ids = [`TIC ${t.tic}`, t.hip && `HIP ${t.hip}`, t.gaia && `Gaia ${t.gaia}`].filter(Boolean).join(" · ");
  const known = [...r.known.planets.map((p) => p.pl_name), ...r.known.tois.map((x) => `TOI-${x.toi}`)];
  $("#resultView").innerHTML = `
  <div class="result">
    <div class="hero">
      <div class="card"><canvas id="orbit"></canvas>
        <div class="orbit-caption">${c.length} signal(s) · orbits to scale in √a · speed ∝ 1/P</div></div>
      <div class="card star-card">
        <div class="eyebrow">Host star</div>
        <h2>${esc(starName(t))}</h2>
        <div class="ids">${esc(ids)}</div>
        <div class="facts">
          <div class="fact"><div class="k">Distance</div><div class="v">${t.dist_ly ? fmt(t.dist_ly, 0) + " <small>ly</small>" : "—"}</div></div>
          <div class="fact"><div class="k">Temperature</div><div class="v">${fmt(t.teff, 0)} <small>K</small></div></div>
          <div class="fact"><div class="k">TESS mag</div><div class="v">${fmt(t.tmag, 2)}</div></div>
          <div class="fact"><div class="k">Radius</div><div class="v">${fmt(t.radius, 2)} <small>R☉</small></div></div>
          <div class="fact"><div class="k">Mass</div><div class="v">${fmt(t.mass, 2)} <small>M☉</small></div></div>
          <div class="fact"><div class="k">Contamination</div><div class="v">${t.contratio != null ? fmt(t.contratio * 100, 1) + "<small>%</small>" : "—"}</div></div>
          <div class="fact"><div class="k">Sectors</div><div class="v">${r.data.sectors.length}</div></div>
          <div class="fact"><div class="k">Seasons</div><div class="v">${r.data.seasons.length}</div></div>
          <div class="fact"><div class="k">Baseline</div><div class="v">${fmt(r.data.baseline_days / 365.25, 1)} <small>yr</small></div></div>
        </div>
        ${t.assumed?.length ? `<p class="disclaimer">⚠ TIC lacks ${t.assumed.join(", ")}; solar-like values assumed.</p>` : ""}
        <p class="disclaimer">Archive: ${known.length ? esc(known.join(", ")) : "no known planets or TOIs on this star."}</p>
        <div class="actions">
          <button class="btn small" id="dlJson">Download report JSON</button>
          <a class="btn small" target="_blank" rel="noopener" href="https://exofop.ipac.caltech.edu/tess/target.php?id=${t.tic}">ExoFOP ↗</a>
          <a class="btn small" target="_blank" rel="noopener" href="https://mast.stsci.edu/portal/Mashup/Clients/Mast/Portal.html?searchQuery=TIC%20${t.tic}">MAST ↗</a>
          <button class="btn small" id="rerun">Re-run</button>
        </div>
      </div>
    </div>

    <div>
      <div class="panel-title"><h3>Signals found</h3><span>${fmt(r.runtime_s, 0)} s · ${r.data.points.toLocaleString()} data points</span></div>
      <div class="cand-strip">${c.length ? c.map((x, i) => `
        <div class="cand t-${x.verdict.tone} ${i === state.cand ? "active" : ""}" data-i="${i}">
          <div class="label">${esc(x.verdict.label)}</div>
          <div class="big">${fmt(x.physical.rp_re, 2)} R⊕ · ${fmt(x.period, 3)} d</div>
          <div class="sub">depth ${fmt(x.depth_ppm, 0)} ppm · SNR ${fmt(x.snr, 1)} · ${x.transits.length} transits</div>
        </div>`).join("") : `<div class="card none-found">No periodic transit signal above the detection threshold.
          Try a wider period range or lower SDE threshold in search settings.</div>`}
      </div>
    </div>

    <div id="candDetail"></div>

    <div class="card">
      <div class="panel-title"><h3>Light curve</h3><div class="tabs" id="lcTabs"></div></div>
      <div id="plotRaw" class="plot"></div>
      <div class="panel-title" style="margin-top:8px"><h3>Detrended</h3><span>stellar variability removed · transits highlighted</span></div>
      <div id="plotFlat" class="plot"></div>
    </div>

    <div class="card">
      <div class="panel-title"><h3>Periodogram</h3><div class="tabs" id="pgTabs"></div></div>
      <div id="plotPg" class="plot short"></div>
    </div>

    <div class="card">
      <div class="panel-title"><h3>Data used</h3><span>${r.data.sectors.length} sectors from MAST</span></div>
      <table class="data-table"><tr><th>Sector</th><th>Year</th><th>Season</th><th>Pipeline</th><th>Cadence</th><th>Points</th></tr>
      ${r.data.sectors.map((s) => `<tr><td>${s.sector}</td><td>${fmt(s.year, 1)}</td>
        <td><span style="color:${SEASON_COLORS[s.season % 8]}">●</span> ${esc(r.data.seasons[s.season].years)}</td>
        <td>${esc(s.author)}</td><td>${s.exptime >= 60 ? fmt(s.exptime / 60, 0) + " min" : s.exptime + " s"}</td><td>${s.points.toLocaleString()}</td></tr>`).join("")}
      </table>
      <details class="log" style="margin-top:12px"><summary>Pipeline log</summary>
        <pre class="console">${esc(r.log.map((l) => `[${l.t}s] ${l.msg}`).join("\n"))}</pre></details>
    </div>
  </div>`;

  $("#resultView").querySelectorAll(".cand").forEach((el) => (el.onclick = () => {
    state.cand = +el.dataset.i;
    $("#resultView").querySelectorAll(".cand").forEach((x) => x.classList.toggle("active", x === el));
    renderCandidate(); renderFlat();
  }));
  $("#dlJson").onclick = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(r, null, 1)], { type: "application/json" }));
    a.download = r.file; a.click();
  };
  $("#rerun").onclick = () => submit(t.input || `TIC ${t.tic}`);

  renderLightCurve("all"); renderFlat(); renderPeriodograms(0); renderCandidate(); drawOrbit();
}

function renderLightCurve(season) {
  const r = state.result, raw = r.plots.raw, seasons = r.data.seasons;
  $("#lcTabs").innerHTML = [`<span class="tab ${season === "all" ? "on" : ""}" data-s="all">All</span>`,
    ...seasons.map((s) => `<span class="tab ${season === s.id ? "on" : ""}" data-s="${s.id}">${esc(s.years)}</span>`)].join("");
  $("#lcTabs").onclick = (e) => { if (e.target.dataset.s) { const s = e.target.dataset.s; renderLightCurve(s === "all" ? "all" : +s); renderFlat(s === "all" ? "all" : +s); } };
  const traces = seasons.filter((s) => season === "all" || s.id === season).map((s) => {
    const idx = raw.t.map((t, i) => i).filter((i) => raw.t[i] >= s.t0 - 1 && raw.t[i] <= s.t1 + 1);
    return { type: "scattergl", mode: "markers", x: idx.map((i) => btjdToDate(raw.t[i])), y: idx.map((i) => raw.f[i]),
      customdata: idx.map((i) => raw.sector[i]), hovertemplate: "Sector %{customdata}<br>%{x|%Y-%m-%d %H:%M}<br>%{y:.5f}<extra></extra>",
      marker: { size: 2.5, color: SEASON_COLORS[s.id % 8] } };
  });
  Plotly.react("plotRaw", traces, layoutBase({ yaxis: { title: "Normalised flux" } }), plotCfg);
}

function renderFlat(season = renderFlat.season ?? "all") {
  renderFlat.season = season;
  const r = state.result, fl = r.plots.flat, seasons = r.data.seasons;
  const s = season === "all" ? null : seasons.find((x) => x.id === season);
  const idx = fl.t.map((t, i) => i).filter((i) => !s || (fl.t[i] >= s.t0 - 1 && fl.t[i] <= s.t1 + 1));
  const traces = [{ type: "scattergl", mode: "markers", x: idx.map((i) => btjdToDate(fl.t[i])), y: idx.map((i) => fl.f[i]),
    marker: { size: 2.5, color: css("--point") }, hoverinfo: "skip" }];
  r.candidates.forEach((c, k) => {
    const hw = c.duration_h / 48;
    const inT = idx.filter((i) => { const ph = (((fl.t[i] - c.t0) % c.period) + c.period) % c.period; return ph < hw || ph > c.period - hw; });
    traces.push({ type: "scattergl", mode: "markers", x: inT.map((i) => btjdToDate(fl.t[i])), y: inT.map((i) => fl.f[i]),
      marker: { size: k === state.cand ? 5 : 4, color: CAND_COLORS[k % 6] }, name: `Candidate ${k + 1}`,
      hovertemplate: `Candidate ${k + 1}<br>%{x|%Y-%m-%d %H:%M}<extra></extra>` });
  });
  Plotly.react("plotFlat", traces, layoutBase({ yaxis: { title: "Detrended flux" } }), plotCfg);
}

function renderPeriodograms(k) {
  const pgs = state.result.plots.periodograms;
  if (!pgs.length) { $("#plotPg").innerHTML = `<p class="muted">No periodogram.</p>`; return; }
  $("#pgTabs").innerHTML = pgs.map((p, i) => `<span class="tab ${i === k ? "on" : ""}" data-k="${i}">Search ${p.iteration} · SDE ${fmt(p.sde, 1)}</span>`).join("");
  $("#pgTabs").onclick = (e) => { if (e.target.dataset.k) renderPeriodograms(+e.target.dataset.k); };
  const p = pgs[k];
  const shapes = [1, 2, 0.5, 3, 1 / 3].map((h) => ({ type: "line", x0: p.best * h, x1: p.best * h, yref: "paper", y0: 0, y1: 1,
    line: { color: CAND_COLORS[0], width: 1, dash: h === 1 ? "solid" : "dot" } }));
  Plotly.react("plotPg", [{ type: "scattergl", mode: "lines", x: p.period, y: p.power, line: { color: css("--text"), width: 1 },
    hovertemplate: "P = %{x:.4f} d<br>ΔlogL = %{y:.1f}<extra></extra>" }],
    layoutBase({ shapes, xaxis: { type: "log", title: "Period (days)" }, yaxis: { title: "Stacked ΔlogL" } }), plotCfg);
}

function nextTransits(c, n = 6) {
  const nowJd = Date.now() / 86400000 + 2440587.5;
  const e0 = Math.ceil((nowJd - c.t0_bjd) / c.period);
  const sT0 = c.duration_h / 24 / 10;
  return Array.from({ length: n }, (_, i) => {
    const e = e0 + i, jd = c.t0_bjd + e * c.period;
    const sig = Math.sqrt(sT0 ** 2 + (e * c.period_err) ** 2) * 24;
    return { date: new Date((jd - 2440587.5) * 86400000), sig };
  });
}

function renderCandidate() {
  const r = state.result, c = r.candidates[state.cand], box = $("#candDetail");
  if (!c) { box.innerHTML = ""; return; }
  const v = c.verdict, ph = c.physical, m = c.match;
  const icon = { pass: "✓", warn: "!", fail: "✕", info: "i" };
  const matchHtml = m.kind === "none"
    ? `<p>No confirmed planet or TOI on this star at this period (or its harmonics) in the NASA Exoplanet Archive.</p>`
    : `<table class="kv"><tr><td>Match</td><td>${esc(m.name)}</td></tr><tr><td>Catalogue period</td><td>${fmt(m.period, 5)} d</td></tr>
       <tr><td>Relation</td><td>${m.relation === "1" ? "same period" : esc(m.relation) + " (harmonic)"}</td></tr>
       ${m.disposition_text ? `<tr><td>TFOP disposition</td><td>${esc(m.disposition_text)}</td></tr>` : ""}
       ${m.facility ? `<tr><td>Discovered</td><td>${esc(m.facility)} ${esc(m.year ?? "")}</td></tr>` : ""}</table>`;
  box.innerHTML = `
  <div class="result">
    <div class="verdict t-${v.tone}">
      <div class="score" style="--s:${v.score}"><span>${v.score}</span></div>
      <div><div class="eyebrow">Candidate ${c.id}</div><h3>${esc(v.label)}</h3><p>${esc(v.text)}</p>
        <p class="disclaimer">Automated vetting score. A signal is only a confirmed planet after independent follow-up (spectroscopy, high-resolution imaging, ground-based photometry) — this tool tells you which signals deserve that effort.</p></div>
    </div>

    <div id="contrib"></div>

    <div class="metrics">
      ${metric("Period", fmt(c.period, 5) + " d", "± " + fmt(c.period_err * 86400, 0) + " s")}
      ${metric("Planet radius", fmt(ph.rp_re, 2) + " R⊕", "± " + fmt(c.rp_err, 2) + " · " + fmt(ph.rp_rj, 3) + " RJ")}
      ${metric("Depth", fmt(c.depth_ppm, 0) + " ppm", "± " + fmt(c.depth_err * 1e6, 0) + " · " + fmt(c.depth * 100, 3) + "%")}
      ${metric("Duration", fmt(c.duration_h, 2) + " h", "expected " + fmt(ph.expected_duration_h, 2) + " h")}
      ${metric("SNR / SDE", fmt(c.snr, 1) + " / " + fmt(c.sde, 1), "red-noise β " + fmt(c.red_noise_beta, 2))}
      ${metric("Transits seen", c.transits.length, c.seasons.length + " season(s)")}
      ${metric("Orbit", fmt(ph.a_au, 4) + " AU", fmt(ph.a_rs, 1) + " R★")}
      ${metric("Eq. temperature", fmt(ph.teq_k, 0) + " K", "albedo 0.3")}
      ${metric("Insolation", fmt(ph.insolation, 1) + " S⊕", ph.insolation > 0.35 && ph.insolation < 1.75 ? "≈ habitable zone" : "")}
    </div>

    <div class="grid-2">
      <div class="card"><div class="panel-title"><h3>Phase-folded transit</h3><span>all data at P = ${fmt(c.period, 5)} d</span></div>
        <div id="plotFold" class="plot tall"></div></div>
      <div class="card"><div class="panel-title"><h3>Every individual transit</h3><span>is the same dip there each time?</span></div>
        <div id="plotTransits" class="plot tall"></div></div>
    </div>

    <div class="card"><div class="panel-title"><h3>Recheck by observing season</h3><span>same ephemeris, each season measured on its own</span></div>
      <div class="seasons">${c.seasons.map((s, i) => `
        <div class="season"><div class="row"><b style="color:${SEASON_COLORS[s.season % 8]}">${esc(s.label)}</b><span>${s.n_transits} tr</span></div>
        <div class="row"><span>${fmt(s.depth * 1e6, 0)} ± ${fmt(s.err * 1e6, 0)} ppm</span><b class="${s.snr >= 3 ? "" : "muted"}">SNR ${fmt(s.snr, 1)}</b></div>
        ${s.indep_period ? `<div class="row"><span>independent P</span><b>${fmt(s.indep_period, 4)} d</b></div>` : `<div class="row"><span>independent P</span><span>— &lt;2 transits</span></div>`}
        <div id="sfold${i}" class="plot"></div></div>`).join("")}</div></div>

    <div class="grid-2">
      <div class="card"><div class="panel-title"><h3>Odd vs even transits</h3><span>eclipsing binaries differ</span></div><div id="plotOE" class="plot"></div></div>
      <div class="card"><div class="panel-title"><h3>Secondary eclipse search</h3><span>phase 0.5</span></div><div id="plotSec" class="plot"></div></div>
    </div>

    <div class="grid-2">
      <div class="card"><div class="panel-title"><h3>Vetting report</h3><span>${v.n_fail} fail · ${v.n_warn} warn</span></div>
        <table class="tests">${c.tests.map((x) => `<tr><td class="ic"><span class="dot ${x.status}">${icon[x.status]}</span></td>
          <td><div class="nm">${esc(x.name)}</div><div class="dt">${esc(x.detail)}</div></td><td class="vl">${esc(x.value)}</td></tr>`).join("")}</table></div>
      <div style="display:flex;flex-direction:column;gap:16px">
        <div class="card"><div class="panel-title"><h3>NASA Exoplanet Archive</h3></div>${matchHtml}</div>
        <div class="card"><div class="panel-title"><h3>Ephemeris for follow-up</h3></div>
          <table class="kv"><tr><td>T₀ (BJD_TDB)</td><td>${fmt(c.t0_bjd, 5)}</td></tr><tr><td>Period</td><td>${fmt(c.period, 6)} ± ${fmt(c.period_err, 6)} d</td></tr>
          ${nextTransits(c).map((n) => `<tr><td>Next transit</td><td>${n.date.toISOString().slice(0, 16).replace("T", " ")} UTC ± ${fmt(n.sig, 1)} h</td></tr>`).join("")}</table></div>
        <div class="card"><div class="panel-title"><h3>Period refinement</h3><span>coherent fit across all years</span></div><div id="plotZoom" class="plot short"></div></div>
      </div>
    </div>
  </div>`;

  renderContrib(r, c);
  const col = CAND_COLORS[state.cand % 6], hw = c.duration_h / 2, d = c.depth;
  const fold = c.plots.fold;
  const lo = Math.min(...fold.x), hi = Math.max(...fold.x);
  Plotly.react("plotFold", [
    { type: "scattergl", mode: "markers", x: fold.x, y: fold.y, marker: { size: 2.5, color: css("--point"), opacity: .5 }, hoverinfo: "skip" },
    { type: "scatter", mode: "markers", x: fold.bx, y: fold.by, marker: { size: 6, color: col }, hovertemplate: "%{x:.2f} h<br>%{y:.6f}<extra></extra>" },
    { type: "scatter", mode: "lines", x: [lo, -hw, -hw, hw, hw, hi], y: [1, 1, 1 - d, 1 - d, 1, 1], line: { color: css("--text"), width: 1.2, shape: "linear" }, hoverinfo: "skip" },
  ], layoutBase({ xaxis: { title: "Hours from mid-transit" }, yaxis: { title: "Relative flux", range: yRange(fold.by, d) } }), plotCfg);

  const tr = c.transits, seasonOf = (t) => (r.data.seasons.find((s) => t >= s.t0 - 1 && t <= s.t1 + 1) || { id: 0 }).id;
  Plotly.react("plotTransits", [
    { type: "scatter", mode: "markers", x: tr.map((x) => btjdToDate(x.t)), y: tr.map((x) => x.depth * 1e6),
      error_y: { type: "data", array: tr.map((x) => x.err * 1e6), color: css("--line-2"), thickness: 1 },
      marker: { size: 7, color: tr.map((x) => SEASON_COLORS[seasonOf(x.t) % 8]) },
      hovertemplate: "%{x|%Y-%m-%d}<br>%{y:.0f} ppm<extra></extra>" },
  ], layoutBase({ yaxis: { title: "Transit depth (ppm)", zeroline: true },
    shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: c.depth_ppm, y1: c.depth_ppm, line: { color: col, dash: "dash", width: 1.5 } },
             { type: "rect", xref: "paper", x0: 0, x1: 1, y0: c.depth_ppm - c.depth_err * 1e6, y1: c.depth_ppm + c.depth_err * 1e6, fillcolor: col + "22", line: { width: 0 } }] }), plotCfg);

  c.seasons.forEach((s, i) => Plotly.react(`sfold${i}`, [
    { type: "scatter", mode: "markers", x: s.fold.x, y: s.fold.y, marker: { size: 4, color: SEASON_COLORS[s.season % 8] }, hoverinfo: "skip" },
    { type: "scatter", mode: "lines", x: [-hw * 3, -hw, -hw, hw, hw, hw * 3], y: [1, 1, 1 - d, 1 - d, 1, 1], line: { color: css("--muted"), width: 1, dash: "dot" }, hoverinfo: "skip" },
  ], layoutBase({ margin: { l: 8, r: 8, t: 4, b: 18 }, xaxis: { showticklabels: true, tickfont: { size: 9 } },
    yaxis: { showticklabels: false, range: yRange(c.plots.fold.by, d) } }), { ...plotCfg, displayModeBar: false, staticPlot: true }));

  Plotly.react("plotOE", [
    { type: "scatter", mode: "markers+lines", name: "odd", x: c.plots.odd.x, y: c.plots.odd.y, marker: { size: 5, color: "#4e79a7" }, line: { width: 1 } },
    { type: "scatter", mode: "markers+lines", name: "even", x: c.plots.even.x, y: c.plots.even.y, marker: { size: 5, color: "#e15759" }, line: { width: 1 } },
  ], layoutBase({ showlegend: true, legend: { x: 0.02, y: 0.05, bgcolor: "rgba(0,0,0,0)" }, xaxis: { title: "Hours from mid-transit" },
    yaxis: { range: yRange(c.plots.fold.by, d) } }), plotCfg);
  Plotly.react("plotSec", [
    { type: "scatter", mode: "markers+lines", x: c.plots.secondary.x, y: c.plots.secondary.y, marker: { size: 5, color: "#f28e2b" }, line: { width: 1 } },
  ], layoutBase({ xaxis: { title: "Hours from phase 0.5" }, yaxis: { range: yRange(c.plots.fold.by, d) },
    shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 1, y1: 1, line: { color: css("--muted"), dash: "dot" } }] }), plotCfg);
  Plotly.react("plotZoom", [{ type: "scatter", mode: "lines", x: c.plots.zoom.period, y: c.plots.zoom.power, line: { color: col, width: 1 } }],
    layoutBase({ xaxis: { title: "Period (d)", tickformat: ".5f" }, yaxis: { title: "ΔlogL" } }), plotCfg);
}

/* ---------- contribute ---------- */
const lsGet = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} };
async function renderContrib(r, c) {
  const box = $("#contrib");
  box.innerHTML = `<div class="card"><div class="panel-title"><h3>Contribute</h3><span>checking ExoFOP readiness…</span></div></div>`;
  const chk = await api(`/api/contrib/${encodeURIComponent(r.file)}/${c.id}/checks`).catch(() => null);
  if (!chk || state.result !== r || r.candidates[state.cand] !== c) return;
  const a = lsGet("author", {}), z = lsGet("zenodo", { sandbox: true });
  const icon = { pass: "✓", warn: "!", fail: "✕", info: "i" };
  const tone = chk.verdict.tone === "pass" ? "new" : chk.verdict.tone === "known" ? "known" : "fail";
  box.innerHTML = `
  <div class="card">
    <div class="panel-title"><h3>Contribute</h3><span class="tone-${tone}">${esc(chk.verdict.label)}</span></div>
    <div class="grid-2">
      <div>
        <p class="muted" style="margin-top:0">${esc(chk.verdict.text)}</p>
        <table class="tests">${chk.checks.map((x) => `<tr><td class="ic"><span class="dot ${x.status}">${icon[x.status]}</span></td>
          <td><div class="nm">${esc(x.name)}</div><div class="dt">${esc(x.detail)}</div></td><td class="vl">${esc(x.value)}</td></tr>`).join("")}</table>
      </div>
      <div class="csteps">
        <div class="cstep"><h4>1. Your details</h4><div class="author">
          <input id="auName" placeholder="Your name" value="${esc(a.name || "")}"><input id="auAff" placeholder="Affiliation or Independent" value="${esc(a.affiliation || "")}">
          <input id="auEmail" placeholder="Email" value="${esc(a.email || "")}"><input id="auOrcid" placeholder="ORCID (get one free at orcid.org)" value="${esc(a.orcid || "")}"></div></div>
        <div class="cstep"><h4>2. Download the write-up package</h4><p>A Research Note draft (AASTeX, compiles on Overleaf) with a figure, an ExoFOP parameter sheet with their column names, and the folded light curve.</p>
          <div class="row"><button class="btn primary" id="cPkg">Download package</button><span id="cPkgNote" class="muted"></span></div></div>
        <div class="cstep"><h4>3. Timestamp it on Zenodo</h4><p>A DOI under your name records that you found it, today. Token: <a target="_blank" rel="noopener" href="https://zenodo.org/account/settings/applications/tokens/new/">Zenodo → Applications</a> (deposit:write, deposit:actions).</p>
          <div class="row"><input id="zToken" type="password" placeholder="Zenodo token" style="flex:1;min-width:150px" value="${esc(z.token || "")}"><label class="muted"><input type="checkbox" id="zSandbox" ${z.sandbox !== false ? "checked" : ""}> sandbox</label><button class="btn" id="cZen">Upload</button></div>
          <div id="cZenNote" class="muted" style="margin-top:6px"></div></div>
        <div class="cstep"><h4>4. Publish a Research Note of the AAS</h4><p>Short (up to 1,000 words, one figure), citable and indexed in ADS. ExoFOP accepts community candidates once they appear in RNAAS or a refereed journal.</p>
          <div class="row"><a class="btn" target="_blank" rel="noopener" href="https://www.overleaf.com/project">Open Overleaf ↗</a><a class="btn" target="_blank" rel="noopener" href="https://journals.aas.org/research-notes/">RNAAS ↗</a></div></div>
        <div class="cstep"><h4>5. Submit it to ExoFOP as a community TOI</h4><p>Request upload rights, then create the candidate with the parameter sheet, attach the figure to the TIC page with the same tag, and link the note. The TESS team reviews CTOIs for promotion to TOIs and ground-based follow-up.</p>
          <div class="row"><a class="btn" target="_blank" rel="noopener" href="https://exofop.ipac.caltech.edu/tess/pub_candidate_upload_request.php">Request upload rights ↗</a>
            <a class="btn" target="_blank" rel="noopener" href="https://exofop.ipac.caltech.edu/tess/target.php?id=${r.target.tic}">TIC ${r.target.tic} on ExoFOP ↗</a>
            <a class="btn" target="_blank" rel="noopener" href="https://exofop.ipac.caltech.edu/tess/candidate_help.php">Guidelines ↗</a></div></div>
        <div class="cstep"><h4>6. Get it observed</h4><p>Share the ephemeris below with amateur transit observers, or join citizen-science projects where volunteer-found planets have been confirmed with finders credited.</p>
          <div class="row"><a class="btn" target="_blank" rel="noopener" href="https://www.zooniverse.org/projects/nora-dot-eisner/planet-hunters-tess">Planet Hunters TESS ↗</a>
            <a class="btn" target="_blank" rel="noopener" href="https://exoplanets.nasa.gov/exoplanet-watch/">NASA Exoplanet Watch ↗</a></div></div>
      </div>
    </div>
  </div>`;
  const readAuthor = () => { const v = { name: $("#auName").value.trim(), affiliation: $("#auAff").value.trim(), email: $("#auEmail").value.trim(), orcid: $("#auOrcid").value.trim() }; lsSet("author", v); return v; };
  const readZ = () => { const v = { token: $("#zToken").value.trim(), sandbox: $("#zSandbox").checked }; lsSet("zenodo", v); return v; };
  for (const id of ["#auName", "#auAff", "#auEmail", "#auOrcid"]) $(id).onchange = readAuthor;
  $("#zToken").onchange = readZ; $("#zSandbox").onchange = readZ;
  $("#cPkg").onclick = async () => {
    $("#cPkgNote").textContent = "Building…";
    const res = await fetch(`/api/contrib/${encodeURIComponent(r.file)}/${c.id}/package`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(readAuthor()) });
    const el = document.createElement("a"); el.href = URL.createObjectURL(await res.blob()); el.download = `TIC${r.target.tic}_${String(c.id).padStart(2, "0")}_candidate.zip`; el.click();
    $("#cPkgNote").textContent = "Downloaded.";
  };
  $("#cZen").onclick = async () => {
    const zz = readZ(), au = readAuthor(), note = $("#cZenNote");
    if (!zz.token) { note.textContent = "Paste a Zenodo token first."; return; }
    if (!au.name) { note.textContent = "Enter your name in step 1."; return; }
    note.textContent = "Uploading…";
    try {
      const d = await api(`/api/contrib/${encodeURIComponent(r.file)}/${c.id}/zenodo`, { method: "POST", body: JSON.stringify({ author: au, token: zz.token, sandbox: zz.sandbox }) });
      note.innerHTML = `Draft ready${d.doi ? `, DOI ${esc(d.doi)}` : ""}. <a target="_blank" rel="noopener" href="${esc(d.html)}">Review ↗</a> <button class="btn small" id="cPub">Publish</button>`;
      $("#cPub").onclick = async () => {
        if (!confirm(`Publish on ${zz.sandbox ? "the Zenodo TEST server" : "Zenodo"}? Published records and DOIs are permanent.`)) return;
        try { const p = await api("/api/zenodo-publish", { method: "POST", body: JSON.stringify({ id: d.id, token: zz.token, sandbox: zz.sandbox }) });
          note.innerHTML = `<span class="tone-new">Published: <a target="_blank" rel="noopener" href="${esc(p.url)}">${esc(p.doi)}</a></span>`; }
        catch (e) { note.innerHTML = `<span class="tone-fail">${esc(e.message)}</span>`; }
      };
    } catch (e) { note.innerHTML = `<span class="tone-fail">${esc(e.message)}</span>`; }
  };
}

function yRange(by, d) {
  const lo = Math.min(1 - d * 1.6, ...by), hi = Math.max(1 + d * 0.6, ...by);
  const pad = (hi - lo) * 0.1;
  return [lo - pad, hi + pad];
}
const metric = (k, v, e) => `<div class="metric"><div class="k">${k}</div><div class="v">${v}</div><div class="e">${e ?? ""}</div></div>`;

/* ---------- orbit animation ---------- */
function teffColor(T) {
  const stops = [[3000, [255, 150, 90]], [4000, [255, 190, 120]], [5000, [255, 220, 170]], [5800, [255, 244, 220]], [7000, [230, 238, 255]], [10000, [180, 205, 255]]];
  for (let i = 1; i < stops.length; i++) if (T <= stops[i][0]) {
    const [t0, c0] = stops[i - 1], [t1, c1] = stops[i], f = Math.max(0, (T - t0) / (t1 - t0));
    return c0.map((v, j) => Math.round(v + (c1[j] - v) * f));
  }
  return stops[stops.length - 1][1];
}
function drawOrbit() {
  cancelAnimationFrame(state.orbitRAF);
  const cv = $("#orbit"), ctx = cv.getContext("2d"), r = state.result;
  const dpr = window.devicePixelRatio || 1;
  const resize = () => { cv.width = cv.clientWidth * dpr; cv.height = cv.clientHeight * dpr; };
  resize();
  const bodies = r.candidates.map((c, i) => ({ a: c.physical.a_au, P: c.period, rp: c.physical.rp_re, color: CAND_COLORS[i % 6],
    label: `Candidate ${i + 1} · ${fmt(c.period, 2)} d`, tone: c.verdict.tone, i, phase0: Math.random() * 6.28 }));
  // known planets not recovered by the search (dimmed)
  for (const p of r.known.planets) {
    if (!p.pl_orbper || r.candidates.some((c) => c.match.name === p.pl_name)) continue;
    const a = Math.cbrt((r.target.mass || 1) * (p.pl_orbper / 365.25) ** 2);
    bodies.push({ a, P: p.pl_orbper, rp: p.pl_rade || 2, color: css("--point"), label: `${p.pl_name} (known)`, dim: true, phase0: Math.random() * 6.28 });
  }
  const sc = teffColor(r.target.teff || 5772);
  const t0 = performance.now();
  const maxA = Math.max(...bodies.map((b) => Math.sqrt(b.a)), 0.01);
  const minP = Math.min(...bodies.map((b) => b.P), 10);
  function frame(now) {
    if (!document.body.contains(cv)) return;
    if (cv.width !== cv.clientWidth * dpr) resize();
    const W = cv.width, H = cv.height, cx = W / 2, cy = H / 2;
    ctx.clearRect(0, 0, W, H);
    const R = Math.min(W, H * 2.6) * 0.42, starR = 22 * dpr;
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, starR * 4);
    g.addColorStop(0, `rgba(${sc},0.35)`); g.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, starR * 4, 0, 7); ctx.fill();
    const sg = ctx.createRadialGradient(cx - starR * .3, cy - starR * .3, 0, cx, cy, starR);
    sg.addColorStop(0, "#fff"); sg.addColorStop(1, `rgb(${sc})`);
    ctx.fillStyle = sg; ctx.beginPath(); ctx.arc(cx, cy, starR, 0, 7); ctx.fill();
    const tsec = (now - t0) / 1000;
    for (const b of bodies) {
      const rx = starR * 1.8 + (R - starR * 1.8) * Math.sqrt(b.a) / maxA, ry = rx * 0.38;
      ctx.strokeStyle = b.dim ? css("--line-2") : b.color + "88"; ctx.lineWidth = dpr; ctx.setLineDash(b.dim ? [4 * dpr, 4 * dpr] : []);
      ctx.beginPath(); ctx.ellipse(cx, cy, rx, ry, 0, 0, 7); ctx.stroke(); ctx.setLineDash([]);
      const ang = b.phase0 + tsec * 0.9 * (minP / b.P);
      const x = cx + rx * Math.cos(ang), y = cy + ry * Math.sin(ang);
      const pr = Math.max(2.5, Math.min(9, 2 + Math.sqrt(b.rp) * 1.6)) * dpr;
      const behind = Math.sin(ang) < 0 && Math.hypot(x - cx, (y - cy) / 0.38) < starR * 1.2;
      if (!behind) {
        ctx.fillStyle = b.dim ? css("--point") : b.color; ctx.beginPath(); ctx.arc(x, y, pr, 0, 7); ctx.fill();
        ctx.fillStyle = b.dim ? css("--muted") : css("--text"); ctx.font = `${11 * dpr}px IBM Plex Mono`;
        ctx.fillText(b.label, x + pr + 5 * dpr, y + 4 * dpr);
      }
    }
    state.orbitRAF = requestAnimationFrame(frame);
  }
  state.orbitRAF = requestAnimationFrame(frame);
}

initTheme(); initSidebar(); refreshJobs(); refreshResults();
if (location.hash.startsWith("#r=")) openResult(decodeURIComponent(location.hash.slice(3))).catch((e) => { console.error(e); toast("Could not open report: " + e.message); show("emptyView"); });
setInterval(refreshJobs, 2000);
