"""Write locust_accounts.json from a database that holds the demo school (`python -m app.cli demo-data`):
who can sign in and what they can open.

usage: python loadtest/prep_accounts.py <project_dir> <out.json> [database]   (database defaults to classnest_demo -
       load-test a COPY, never the live database: the test posts comments and watch progress)"""
import json
import shutil
import subprocess
import sys

PROJECT, OUT = sys.argv[1], sys.argv[2]
DATABASE = sys.argv[3] if len(sys.argv) > 3 else "classnest_demo"
DOCKER = shutil.which("docker") or r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"


def rows(q):
    out = subprocess.run([DOCKER, "compose", "exec", "-T", "db", "psql", "-U", "classnest", "-d", DATABASE, "-At",
                          "-F", "|", "-c", q], capture_output=True, text=True, cwd=PROJECT).stdout.strip()
    return [line.split("|") for line in out.splitlines() if line]


video = rows("SELECT v.id, v.batch_id, l.id FROM videos v JOIN lessons l ON l.video_id = v.id ORDER BY v.id LIMIT 1")[0]
students = [{"username": u, "batches": [int(b) for b in bs.split(",")]}
            for u, bs in rows("SELECT u.username, string_agg(e.batch_id::text, ',') FROM users u JOIN enrollments e "
                              "ON e.user_id = u.id WHERE u.role = 'student' AND u.status = 'active' GROUP BY u.username")]
teachers = [u for (u,) in rows("SELECT username FROM users WHERE role = 'staff' AND status = 'active' AND staff_role IN "
                               "('teacher', 'sme', 'principal', 'coordinator')")]
data = {"students": students, "teachers": teachers,
        "video": {"id": int(video[0]), "batch": int(video[1]), "lesson": int(video[2])}}
json.dump(data, open(OUT, "w"), indent=1)
print(f"{len(students)} students, {len(teachers)} teaching staff, video {data['video']}")
