"""Fail loudly if a Locust run didn't exercise what it should: task errors in the console, core actions missing, or
no AI tutor answers in <prefix>_ai.csv.
usage: python validate_run.py <prefix>_stats.csv <console.txt>"""
import csv
import os
import sys

stats = {r["Name"]: r for r in csv.DictReader(open(sys.argv[1], encoding="utf-8"))}
console = open(sys.argv[2], encoding="utf-8", errors="replace").read()
errors = console.count("/ERROR/") + console.count("Traceback (most recent call last)")
must = ["GET /student (dashboard)", "GET /api/notifications (30 s poll)", "GET /student/calendar",
        "GET /student/lessons/[id]", "GET /staff (dashboard)", "GET /admin (dashboard)", "GET / (public home)",
        "POST sign-in (bcrypt)", "POST /api/ai/ask (stream opened)"]
missing = [m for m in must if m not in stats]
ai_file = sys.argv[1].replace("_stats.csv", "_ai.csv")
ai = list(csv.DictReader(open(ai_file, encoding="utf-8"))) if os.path.exists(ai_file) else []
answered = [r for r in ai if r["outcome"] == "answered"]
if not answered:
    missing.append(f"AI tutor answers in {os.path.basename(ai_file)}")
print("task errors in console:", errors, "| missing core actions:", missing or "none")
print("endpoints seen:", len(stats) - 1, "| requests:", stats["Aggregated"]["Request Count"],
      "| failures:", stats["Aggregated"]["Failure Count"])
print("AI questions:", len(ai), "| answered:", len(answered), "| by the model:",
      sum(r["answered_by"] == "ai" for r in answered), "| other outcomes:",
      sorted({r["outcome"] for r in ai} - {"answered"}) or "none")
sys.exit(1 if errors or missing else 0)
