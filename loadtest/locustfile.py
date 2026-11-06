"""ClassNest stress test: 10 minutes, stepped load 50 -> 100 -> 200 -> 300 -> 400 concurrent users (2 min each).

Users are a realistic mix - students (70 %), teachers (20 %), admins (5 %), public visitors (5 %) - each signing in
once (bcrypt) from its own simulated address (X-Forwarded-For; the test instance trusts it from 127.0.0.1) and then
browsing with 2-6 s of "think time". 8 extra students keep asking the real AI tutor (Gemma 3 on the GPU) throughout.
Rate limits (comments, AI) answering 429 are counted as successes under their own "(rate limited)" name.

The AI tutor's timings (first word, whole streamed answer) are written to <csv prefix>_ai.csv, NOT into Locust's
request statistics: an answer that streams for many seconds is not a page load and would swamp the p99 / slowest
figures.
"""
import csv
import itertools
import json
import os
import random
import re
import time

from locust import HttpUser, LoadTestShape, between, events, task

DATA = json.load(open(os.path.join(os.path.dirname(__file__), "locust_accounts.json")))
AI_LOG: list[dict] = []
VIDEO = DATA["video"]
CSRF = re.compile(r'data-csrf="([^"]+)"')
TOKEN = re.compile(r'name="csrf_token" value="([^"]+)"')
_ips = itertools.count(1)
QUESTIONS = ["Can you explain this topic with a simple example?", "What are the key points I should revise?",
             "Why does this method work?", "Give me one practice question on today's class.",
             "What is a common mistake students make here?"]


def next_ip() -> str:
    n = next(_ips)
    return f"10.{100 + (n >> 16) % 100}.{(n >> 8) & 255}.{n & 255}"


class Member(HttpUser):
    abstract = True
    wait_time = between(2, 6)
    home = "/"

    def sign_in(self, ident, password="Demo@12345", admin=False):
        self.client.headers["X-Forwarded-For"] = next_ip()
        page = self.client.get("/login?tab=admin" if admin else "/login", name="GET /login").text
        data = {"csrf_token": TOKEN.search(page).group(1), "_once": os.urandom(8).hex(), "next": "/"}
        data.update({"username": ident, "password": password} if admin else {"identifier": ident, "password": password})
        with self.client.post("/login/admin" if admin else "/login", data=data, allow_redirects=False,
                              name="POST sign-in (bcrypt)", catch_response=True) as r:
            if r.status_code != 303 or r.headers.get("location", "").startswith("/login"):
                r.failure(f"sign-in failed: {r.status_code} {r.headers.get('location')}")
        html = self.client.get(self.home, name=f"GET {self.home} (dashboard)").text
        m = CSRF.search(html)
        self.csrf = m.group(1) if m else ""
        return html

    def api_headers(self):
        return {"X-CSRF-Token": self.csrf, "Accept": "application/json"}

    def poll_notifications(self):
        self.client.get("/api/notifications", name="GET /api/notifications (30 s poll)", headers={"Accept": "application/json"})

    def calendar_events(self):
        self.client.get("/api/calendar/events?start=2026-10-26T00:00:00&end=2026-12-07T00:00:00", name="GET /api/calendar/events",
                        headers={"Accept": "application/json"})


class Student(Member):
    weight = 70
    home = "/student"

    def on_start(self):
        me = random.choice(DATA["students"])
        self.batches = me["batches"]
        self.has_video = VIDEO["batch"] in self.batches
        html = self.sign_in(me["username"])
        self.lessons = sorted(set(re.findall(r'/student/lessons/(\d+)', html))) or []
        tasks_html = self.client.get("/student/tasks", name="GET /student/tasks").text
        self.task_ids = sorted(set(re.findall(r'/student/tasks/(\d+)', tasks_html)))  # not self.tasks (Locust's)
        classes = self.client.get("/student/classes", name="GET /student/classes").text
        self.lessons = sorted(set(self.lessons) | set(re.findall(r'/student/lessons/(\d+)', classes)))

    @task(10)
    def dashboard(self):
        self.client.get("/student", name="GET /student (dashboard)")

    @task(8)
    def notifications(self):
        self.poll_notifications()

    @task(5)
    def tasks_page(self):  # not "tasks": that name is Locust's own task list
        self.client.get("/student/tasks", name="GET /student/tasks")

    @task(4)
    def task_detail(self):
        if self.task_ids:
            self.client.get(f"/student/tasks/{random.choice(self.task_ids)}", name="GET /student/tasks/[id]")

    @task(4)
    def calendar(self):
        self.client.get("/student/calendar", name="GET /student/calendar")
        self.calendar_events()

    @task(3)
    def classes(self):
        self.client.get("/student/classes", name="GET /student/classes")

    @task(4)
    def lesson(self):
        if self.lessons:
            self.client.get(f"/student/lessons/{random.choice(self.lessons)}", name="GET /student/lessons/[id]")

    @task(4)
    def watch_video(self):  # the class video: page, watch progress, comment refresh
        if not self.has_video:
            return
        self.client.get(f"/student/lessons/{VIDEO['lesson']}", name="GET /student/lessons/[id] (with video)")
        start = random.randint(0, 8)
        self.client.post(f"/api/videos/{VIDEO['id']}/progress", json={"intervals": [[start, start + 4]], "position": start + 4},
                         headers=self.api_headers(), name="POST /api/videos/[id]/progress")
        self.client.get(f"/videos/{VIDEO['id']}/comments", headers={"Accept": "application/json"},
                        name="GET /videos/[id]/comments (20 s refresh)")

    @task(1)
    def comment(self):
        if not self.has_video:
            return
        with self.client.post(f"/videos/{VIDEO['id']}/comments", data={"body": random.choice(QUESTIONS)},
                              headers=self.api_headers(), name="POST /videos/[id]/comments", catch_response=True) as r:
            if r.status_code == 429:  # the per-person comment limit doing its job: counted once, under its own name
                r.request_meta["name"] = "POST /videos/[id]/comments (rate limited)"
                r.success()

    @task(2)
    def social(self):
        self.client.get(random.choice(["/friends", "/messages"]), name="GET /friends | /messages")

    @task(2)
    def tutor_page(self):
        self.client.get("/student/tutor", name="GET /student/tutor")

    @task(2)
    def money_and_profile(self):
        self.client.get(random.choice(["/student/fees", "/student/profile"]), name="GET /student/fees | profile")


class Teacher(Member):
    weight = 20
    home = "/staff"

    def on_start(self):
        html = self.sign_in(random.choice(DATA["teachers"]))
        self.batches = sorted(set(re.findall(r'/staff/batches/(\d+)', html)))

    @task(8)
    def dashboard(self):
        self.client.get("/staff", name="GET /staff (dashboard)")

    @task(6)
    def notifications(self):
        self.poll_notifications()

    @task(6)
    def batch(self):
        if self.batches:
            self.client.get(f"/staff/batches/{random.choice(self.batches)}", name="GET /staff/batches/[id]")

    @task(3)
    def insights_and_doubts(self):
        self.client.get(random.choice(["/staff/insights", "/staff/doubts"]), name="GET /staff/insights | doubts")

    @task(3)
    def calendar(self):
        self.client.get("/staff/calendar", name="GET /staff/calendar")
        self.calendar_events()

    @task(2)
    def video(self):
        with self.client.get(f"/videos/{VIDEO['id']}", name="GET /videos/[id] (watch page)", catch_response=True) as r:
            if r.status_code == 404:  # only that batch's teacher (and supervisors) may open it
                r.success()


class Admin(Member):
    weight = 5
    home = "/admin"

    def on_start(self):
        self.sign_in("admin", "Admin@12345", admin=True)
        self.members = []

    @task(6)
    def dashboard(self):
        self.client.get("/admin", name="GET /admin (dashboard)")

    @task(4)
    def students(self):
        html = self.client.get(f"/admin/students?page={random.randint(1, 2)}", name="GET /admin/students?page=N").text
        self.members = re.findall(r'/admin/members/(\d+)', html) or self.members

    @task(3)
    def member(self):
        if self.members:
            self.client.get(f"/admin/members/{random.choice(self.members)}", name="GET /admin/members/[id]")

    @task(3)
    def lists(self):
        path = random.choice(["/admin/batches", "/admin/fees", "/admin/insights", "/admin/lessons", "/admin/videos",
                              "/admin/staff", "/admin/reports"])
        self.client.get(path, name="GET /admin/<list pages>")

    @task(2)
    def calendar(self):
        self.client.get("/admin/calendar", name="GET /admin/calendar")
        self.calendar_events()

    @task(2)
    def notifications(self):
        self.poll_notifications()


class Visitor(HttpUser):
    weight = 5
    wait_time = between(2, 6)

    def on_start(self):
        self.client.headers["X-Forwarded-For"] = next_ip()

    @task(6)
    def home(self):
        self.client.get("/", name="GET / (public home)")

    @task(2)
    def pages(self):
        self.client.get(random.choice(["/login", "/p/terms"]), name="GET /login | /p/terms")

    @task(1)
    def icons(self):
        self.client.get(random.choice(["/favicon.ico", "/site.webmanifest"]), name="GET /favicon.ico | manifest")


class AIStudent(Member):
    """8 students asking the real AI tutor every 30-60 s for the whole test (the GPU part of the load)."""
    fixed_count = 8
    wait_time = between(30, 60)
    home = "/student"

    def on_start(self):
        me = DATA["students"][next(_ai) % len(DATA["students"])]
        self.batch = me["batches"][0]
        self.sign_in(me["username"])

    @task
    def ask(self):
        t0, first, final, outcome = time.perf_counter(), None, None, "answered"
        with self.client.post("/api/ai/ask", json={"question": random.choice(QUESTIONS), "batch_id": self.batch},
                              headers={"X-CSRF-Token": self.csrf}, stream=True, catch_response=True,
                              name="POST /api/ai/ask (stream opened)") as r:
            if r.status_code == 429:
                r.request_meta["name"] = "POST /api/ai/ask (rate limited)"
                r.success()
                outcome = "rate limited"
            elif r.status_code != 200:
                outcome = f"HTTP {r.status_code}"
                r.failure(outcome)
            else:
                try:
                    for line in r.iter_lines():
                        if line.startswith(b"data:"):
                            ev = json.loads(line[5:])
                            if ev.get("type") == "token" and first is None:
                                first = time.perf_counter() - t0
                            elif ev.get("type") == "final":
                                final = ev
                except Exception as e:  # the connection dropped mid-answer
                    outcome = f"stream broken ({type(e).__name__})"
                    r.failure(outcome)
                else:
                    if final is None:
                        outcome = "stream ended without an answer"
                        r.failure(outcome)
                    else:
                        r.success()
        AI_LOG.append({"time": round(time.time(), 2), "users": self.environment.runner.user_count,
                       "first_word_ms": round(first * 1000) if first is not None else "",
                       "answer_ms": round((time.perf_counter() - t0) * 1000), "outcome": outcome,
                       "answered_by": (final or {}).get("answered_by", ""),
                       "answer_chars": len((final or {}).get("html", ""))})


_ai = itertools.count()


@events.test_stop.add_listener
@events.quitting.add_listener
def save_ai_log(environment, **_):
    prefix = getattr(environment.parsed_options, "csv_prefix", None)
    if prefix and AI_LOG:
        with open(f"{prefix}_ai.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(AI_LOG[0]))
            w.writeheader()
            w.writerows(AI_LOG)


class StepLoad(LoadTestShape):
    """(ends at second, concurrent users, users started per second)"""
    stages = [(120, 50, 2), (240, 100, 2), (360, 200, 4), (480, 300, 4), (600, 400, 4)]
    if os.environ.get("CN_SMOKE"):  # 30-second dry run of the same script
        stages = [(30, 12, 6)]

    def tick(self):
        t = self.get_run_time()
        for end, users, rate in self.stages:
            if t < end:
                return users + AIStudent.fixed_count, rate
        return None
