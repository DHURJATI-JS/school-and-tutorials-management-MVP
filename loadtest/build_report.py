"""Turn the Locust run into (1) app/data/loadtest_report.json for the in-app report page, (2) SVG charts + copies of
Locust's HTML report and CSVs in docs/load-test/ for the README.

usage: python build_report.py <results_dir> <project_dir>"""
import csv
import datetime as dt
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

RESULTS, PROJECT = Path(sys.argv[1]), Path(sys.argv[2])
DOCS = PROJECT / "docs" / "load-test"
DOCS.mkdir(parents=True, exist_ok=True)
STEPS = [(0, 120, 50), (120, 240, 100), (240, 360, 200), (360, 480, 300), (480, 600, 400)]  # = locustfile StepLoad
AI_USERS = 8  # = locustfile AIStudent.fixed_count
SETTLE = 30  # seconds of each step skipped as ramp-up when reading its rolling percentiles


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- read Locust's CSVs
stats = list(csv.DictReader(open(RESULTS / "classnest_stats.csv", encoding="utf-8")))
history = [r for r in csv.DictReader(open(RESULTS / "classnest_stats_history.csv", encoding="utf-8"))
           if r["Name"] == "Aggregated"]
t0 = int(history[0]["Timestamp"])
for r in history:
    r["t"] = int(r["Timestamp"]) - t0
failures = list(csv.DictReader(open(RESULTS / "classnest_failures.csv", encoding="utf-8")))

samples = []
res_file = RESULTS / "resources.csv"
if res_file.exists():
    rows = list(csv.DictReader(open(res_file, encoding="utf-8-sig")))
    # Each row's wall-clock time: the "epoch" column when present; otherwise the file's last write time belongs to
    # its last row (NOT the creation time - Windows keeps the old creation time when a file is overwritten).
    last_t = float(rows[-1]["t_s"]) if rows else 0
    for r in rows:
        wall = float(r["epoch"]) if r.get("epoch") else os.path.getmtime(res_file) - last_t + float(r["t_s"])
        t = int(wall - t0)
        if 0 <= t <= 600:
            samples.append({"t": t, "app_cpu": num(r["app_cpu_pct"]), "app_mem": num(r["app_mem_mb"]),
                            "db_cpu": num(r["db_cpu_pct"]), "db_mem": num(r["db_mem_mb"])})


# The AI tutor's timings (written by the locustfile; kept out of Locust's request stats on purpose)
ai_log = []
if (RESULTS / "classnest_ai.csv").exists():
    for r in csv.DictReader(open(RESULTS / "classnest_ai.csv", encoding="utf-8")):
        r["t"] = float(r["time"]) - t0
        ai_log.append(r)
by_model = [r for r in ai_log if r["outcome"] == "answered" and r["answered_by"] == "ai"]


def pctl(values, q):  # nearest-rank percentile of raw samples
    s = sorted(values)
    return s[max(0, math.ceil(q / 100 * len(s)) - 1)] if s else None


def ai_times(rows):
    first = [int(r["first_word_ms"]) for r in rows if r["first_word_ms"]]
    return first, [int(r["answer_ms"]) for r in rows]


def at(t):  # last history row at or before second t
    rows = [r for r in history if r["t"] <= t]
    return rows[-1] if rows else history[0]


steps = []
for start, end, users in STEPS:
    a, b = at(start), at(end)
    reqs = int(b["Total Request Count"]) - int(a["Total Request Count"])
    fails = int(b["Total Failure Count"]) - int(a["Total Failure Count"])
    steady = [r for r in history if start + SETTLE <= r["t"] < end]
    pct = {k: [num(r[k]) for r in steady if num(r[k])] for k in ("50%", "95%", "99%", "100%")}
    rps = [num(r["Requests/s"]) for r in steady if num(r["Requests/s"]) is not None]
    win = [s for s in samples if start + SETTLE <= s["t"] < end]
    app_cpu = [s["app_cpu"] for s in win if s["app_cpu"] is not None]
    db_cpu = [s["db_cpu"] for s in win if s["db_cpu"] is not None]
    app_mem = [s["app_mem"] for s in win if s["app_mem"] is not None]
    ai_first, _ = ai_times([r for r in by_model if start <= r["t"] < end])
    steps.append({
        "ai_answers": len(ai_first), "ai_first_word_p50": pctl(ai_first, 50),
        "users": users, "from_s": start, "to_s": end, "requests": reqs, "failures": fails,
        "rps": round(reqs / (end - start), 1), "rps_steady": round(statistics.mean(rps), 1) if rps else None,
        "p50": round(statistics.median(pct["50%"])) if pct["50%"] else None,
        "p95": round(statistics.median(pct["95%"])) if pct["95%"] else None,
        "p99": round(statistics.median(pct["99%"])) if pct["99%"] else None,
        "worst": round(max(pct["100%"])) if pct["100%"] else None,
        "app_cpu": round(statistics.mean(app_cpu), 1) if app_cpu else None,
        "app_cpu_peak": round(max(app_cpu), 1) if app_cpu else None,
        "db_cpu": round(statistics.mean(db_cpu), 1) if db_cpu else None,
        "app_mem": round(max(app_mem)) if app_mem else None})

agg = next(r for r in stats if r["Name"] == "Aggregated")
endpoint_rows = [r for r in stats if r["Name"] != "Aggregated"]


def ep(r):
    return {"name": r["Name"], "type": r["Type"], "requests": int(r["Request Count"]),
            "failures": int(r["Failure Count"]), "p50": num(r["50%"]), "p95": num(r["95%"]), "p99": num(r["99%"]),
            "max": round(num(r["Max Response Time"]) or 0), "avg": round(num(r["Average Response Time"]) or 0),
            "kb": round((num(r["Average Content Size"]) or 0) / 1024, 1), "rps": round(num(r["Requests/s"]) or 0, 2)}


endpoints = sorted((ep(r) for r in endpoint_rows if r["Type"] != "AI"), key=lambda e: -e["requests"])
ai_rows = {r["Name"]: ep(r) for r in endpoint_rows if r["Type"] == "AI"}
total_requests, total_failures = int(agg["Request Count"]), int(agg["Failure Count"])
rps_series = [num(r["Requests/s"]) or 0 for r in history]

# ---------------------------------------------------------------- 5-second series for the charts
series = {"t": [], "users": [], "rps": [], "p50": [], "p95": [], "cpu": [], "db_cpu": []}
for t in range(0, 601, 5):
    r = at(t)
    window = [num(x["Requests/s"]) or 0 for x in history if t - 5 < x["t"] <= t]
    series["t"].append(t)
    series["users"].append(int(r["User Count"]))
    series["rps"].append(round(statistics.mean(window), 1) if window else 0)
    series["p50"].append(num(r["50%"]) or None)
    series["p95"].append(num(r["95%"]) or None)
    near = min(samples, key=lambda s: abs(s["t"] - t)) if samples else None
    series["cpu"].append(near["app_cpu"] if near and abs(near["t"] - t) <= 10 else None)
    series["db_cpu"].append(near["db_cpu"] if near and abs(near["t"] - t) <= 10 else None)


def ps(cmd):
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True).stdout.strip()


model = os.environ.get("CN_AI_MODEL", "Gemma 3 1B · vLLM on a 4 GB laptop GPU")
if ai_log:
    first_all, answer_all = ai_times(by_model)
    ai_summary = {"asked": len(ai_log), "by_model": len(by_model),
                  "fallback": sum(r["outcome"] == "answered" and r["answered_by"] == "fallback" for r in ai_log),
                  "rate_limited": sum(r["outcome"] == "rate limited" for r in ai_log),
                  "failed": sum(r["outcome"] not in ("answered", "rate limited") for r in ai_log),
                  "first_word_p50": pctl(first_all, 50), "first_word_p95": pctl(first_all, 95),
                  "first_word_max": max(first_all, default=None),
                  "answer_p50": pctl(answer_all, 50), "answer_p95": pctl(answer_all, 95), "model": model}
else:  # older runs recorded the AI timings as Locust request types
    fw, wa = ai_rows.get("AI tutor: first word") or {}, ai_rows.get("AI tutor: whole answer") or {}
    ai_summary = {"asked": wa.get("requests", 0), "by_model": wa.get("requests", 0) - wa.get("failures", 0),
                  "fallback": 0, "rate_limited": 0, "failed": wa.get("failures", 0),
                  "first_word_p50": fw.get("p50"), "first_word_p95": fw.get("p95"), "first_word_max": fw.get("max"),
                  "answer_p50": wa.get("p50"), "answer_p95": wa.get("p95"), "model": model}

cpu = ps("(Get-CimInstance Win32_Processor | Select-Object -First 1).Name")
cores = ps("(Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum")
ram = ps("[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)")
os_name = ps("(Get-CimInstance Win32_OperatingSystem).Caption").replace("Microsoft ", "")
gpu = ""
if shutil.which("nvidia-smi"):
    smi = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                         capture_output=True, text=True).stdout.strip().splitlines()
    if smi:
        name, _, mem = smi[0].partition(",")
        gpu = f"{name.strip()} ({round(int(mem.strip().split()[0]) / 1024)} GB)" if mem.strip() else name.strip()
try:  # run this script with the Python that has Locust installed to record its version
    from importlib.metadata import version as _v
    locust = f"Locust {_v('locust')}"
except Exception:
    locust = "Locust"
report = {
    "title": "10-minute Locust stress test",
    "ran_at": dt.datetime.fromtimestamp(t0).strftime("%d %b %Y, %H:%M"),
    "duration_s": 600, "tool": f"{locust} (headless, 1 process)",
    "target": f"one uvicorn worker with the launcher's settings · PostgreSQL 16 in Docker · AI: {model}",
    "machine": " · ".join(p for p in (f"{cpu} ({cores} threads)", f"{ram} GB RAM", gpu, os_name) if p)
               + " — the load generator ran on the same machine",
    "data": "the demo school: 50 people, 8 batches, 54 classes, 528 attendance marks, 192 fee records, "
            "a class video with comments",
    "mix": [
        {"who": "Students", "share": "70 %", "does": "dashboard, the 30-second notification poll, tasks, calendar + "
                                                    "events feed, classes, lesson pages, the class video (watch "
                                                    "progress + comment refresh), friends / messages, the AI tutor "
                                                    "page, fees, profile; now and then a comment"},
        {"who": "Teachers", "share": "20 %", "does": "dashboard, batch pages, insights / doubts, calendar, watch page"},
        {"who": "Admins", "share": "5 %", "does": "dashboard with charts, student list (paged), person pages, "
                                                 "every list page, calendar"},
        {"who": "Visitors", "share": "5 %", "does": "public home page, sign-in page, terms, icons (not signed in)"},
        {"who": "AI tutor", "share": f"+{AI_USERS}", "does": f"{AI_USERS} extra students all through the test: a real "
                                                             "question to the AI tutor every 30-60 s, streamed to "
                                                             "the end"}],
    "ai_users": AI_USERS,
    "think_time": "2-6 s between actions, like a person reading the page; everyone signs in once (bcrypt)",
    "steps": steps,
    "totals": {"requests": total_requests, "failures": total_failures,
               "failure_pct": round(total_failures * 100 / total_requests, 3) if total_requests else 0,
               "rps_avg": round(num(agg["Requests/s"]) or 0, 1), "rps_peak": round(max(rps_series), 1),
               "p50": num(agg["50%"]), "p95": num(agg["95%"]), "p99": num(agg["99%"]),
               "max": round(num(agg["Max Response Time"]) or 0),
               "sign_ins": next((e["requests"] for e in endpoints if e["name"].startswith("POST sign-in")), 0)},
    "endpoints": endpoints,
    "ai": ai_summary,
    "failures": [{"name": f["Name"], "error": f["Error"][:200], "count": int(f["Occurrences"])} for f in failures],
    "series": series,
    "notes": ["Per-step percentiles are the median of Locust's rolling 10-second percentiles over the step's steady "
              "part (its first 30 s are skipped as ramp-up).",
              "Locust rounds times for its percentiles (to 10 ms above 100 ms, to 100 ms above 1 s), so a p99 can "
              "read slightly above the slowest request.",
              "CPU is the app process as % of ONE core (one async worker, plus threads for bcrypt); PostgreSQL is "
              "the database container's CPU %.",
              "Rate-limited answers (HTTP 429 from the comment and AI limits) are protections working and are "
              "counted as successes under their own name.",
              "The AI tutor's timings (first word, whole streamed answer) are measured separately and are not part of "
              "the request statistics: an answer that streams for many seconds is not a page load. Opening the "
              "answer stream is a normal request."]
             # anything particular to this run, one note per line
             + ([n.strip() for n in open(RESULTS / "notes.txt", encoding="utf-8") if n.strip()]
                if (RESULTS / "notes.txt").exists() else [])}
out = PROJECT / "app" / "data" / "loadtest_report.json"
json.dump(report, open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print("wrote", out)

# ---------------------------------------------------------------- SVG charts for the README
SURF, PLANE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#f3f2ee", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
S1, S2 = "#2a78d6", "#eb6834"
STYLE = f"""<style>
.surf{{fill:{SURF}}} .band{{fill:{PLANE}}} .ink{{fill:{INK}}} .ink2{{fill:{INK2}}} .muted{{fill:{MUTED}}}
.grid{{stroke:{GRID}}} .axis{{stroke:{AXIS}}} .s1{{stroke:{S1}}} .s2{{stroke:{S2}}} .f1{{fill:{S1}}} .f2{{fill:{S2}}}
.ring{{stroke:{SURF}}} .frame{{stroke:rgba(11,11,11,.10)}}
text{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif}} .num{{font-variant-numeric:tabular-nums}}
@media (prefers-color-scheme: dark){{
.surf{{fill:#1a1a19}} .band{{fill:#222220}} .ink{{fill:#f0efec}} .ink2{{fill:#c3c2b7}} .grid{{stroke:#2c2c2a}}
.axis{{stroke:#383835}} .s1{{stroke:#3987e5}} .s2{{stroke:#d95926}} .f1{{fill:#3987e5}} .f2{{fill:#d95926}}
.ring{{stroke:#1a1a19}} .frame{{stroke:rgba(255,255,255,.10)}} }}
</style>"""


def nice_max(v):
    for step in (1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000):
        if v <= step * 4:
            return step * 4, step
    return v, v / 4


def line_chart(name, title, subtitle, lines, unit, fmt=lambda v: f"{v:,.0f}"):
    """lines: [(label, values, cls_index)] over series["t"]; one y-axis; step bands mark the load."""
    W, H, L, R, T, B = 760, 300, 58, 92, 74, 40
    pw, ph = W - L - R, H - T - B
    ts = series["t"]
    top = max((v for _, vals, _ in lines for v in vals if v is not None), default=1) or 1
    ymax, ystep = nice_max(top * 1.05)
    x = lambda t: L + pw * t / 600  # noqa: E731
    y = lambda v: T + ph - ph * v / ymax  # noqa: E731
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
           f'aria-label="{title}">', STYLE,
           f'<rect class="surf frame" x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="12" stroke-width="1"/>',
           f'<text class="ink" x="20" y="30" font-size="16" font-weight="700">{title}</text>',
           f'<text class="ink2" x="20" y="50" font-size="12">{subtitle}</text>']
    for i, (start, end, users) in enumerate(STEPS):  # load steps as quiet bands, labelled once
        if i % 2:
            out.append(f'<rect class="band" x="{x(start):.1f}" y="{T}" width="{x(end) - x(start):.1f}" height="{ph}"/>')
        out.append(f'<text class="muted" x="{(x(start) + x(end)) / 2:.1f}" y="{T - 6}" font-size="11" '
                   f'text-anchor="middle">{users} users</text>')
    v = 0
    while v <= ymax + 1e-9:
        out.append(f'<line class="grid" x1="{L}" x2="{L + pw}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke-width="1"/>')
        out.append(f'<text class="muted num" x="{L - 8}" y="{y(v) + 4:.1f}" font-size="11" text-anchor="end">'
                   f'{fmt(v)}</text>')
        v += ystep
    out.append(f'<line class="axis" x1="{L}" x2="{L + pw}" y1="{T + ph}" y2="{T + ph}" stroke-width="1"/>')
    for m in range(0, 11, 2):
        out.append(f'<text class="muted num" x="{x(m * 60):.1f}" y="{T + ph + 18}" font-size="11" '
                   f'text-anchor="middle">{m} min</text>')
    out.append(f'<text class="muted" x="{L - 8}" y="{T - 6}" font-size="11" text-anchor="end">{unit}</text>')
    legend_x = W - R - 8
    for label, vals, k in lines:
        pts = [(x(t), y(v)) for t, v in zip(ts, vals, strict=False) if v is not None]
        if not pts:
            continue
        d = "M" + " L".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        out.append(f'<path class="s{k}" d="{d}" fill="none" stroke-width="2" stroke-linejoin="round" '
                   f'stroke-linecap="round"/>')
        lx, ly = pts[-1]
        last = [v for v in vals if v is not None][-1]
        out.append(f'<circle class="f{k} ring" cx="{lx:.1f}" cy="{ly:.1f}" r="4" stroke-width="2"/>')
        out.append(f'<text class="ink num" x="{lx + 9:.1f}" y="{ly + 4:.1f}" font-size="12" font-weight="650">'
                   f'{fmt(last)}{"" if len(lines) == 1 else " " + label}</text>')
    if len(lines) > 1:  # >= 2 series: legend present (plus the direct end labels)
        for label, _, k in reversed(lines):
            w = 8 * len(label) + 30
            legend_x -= w
            # on the title row (short titles), clear of the subtitle
            out.append(f'<line class="s{k}" x1="{legend_x}" x2="{legend_x + 16}" y1="25" y2="25" stroke-width="2" '
                       f'stroke-linecap="round"/>')
            out.append(f'<text class="ink2" x="{legend_x + 22}" y="29" font-size="12">{label}</text>')
    out.append("</svg>")
    (DOCS / name).write_text("\n".join(out), encoding="utf-8")
    print("wrote", DOCS / name)


line_chart("throughput.svg", "Requests per second", "Every request the app answered, 5-second average",
           [("req/s", series["rps"], 1)], "req/s")
line_chart("latency.svg", "Response time", "Median and 95th percentile of page and API requests (rolling 10 s window)",
           [("median", series["p50"], 1), ("p95", series["p95"], 2)], "ms")
if samples:
    line_chart("cpu.svg", "Server CPU", "App (% of one core) and PostgreSQL · spikes: each step's sign-ins (bcrypt)",
               [("app", series["cpu"], 1), ("database", series["db_cpu"], 2)], "%")
for f in ("report.html", "classnest_stats.csv", "classnest_failures.csv", "classnest_ai.csv", "resources.csv"):
    if (RESULTS / f).exists():
        shutil.copy(RESULTS / f, DOCS / ("locust-report.html" if f == "report.html" else f))
print(json.dumps({"totals": report["totals"], "steps": steps, "ai": report["ai"], "failures": report["failures"]},
                 indent=1))
