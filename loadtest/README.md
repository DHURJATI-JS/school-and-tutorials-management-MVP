# Load test (Locust)

The 10-minute stress test behind **Admin → Load test report** and the README's
[load-test section](../README.md#-load-test--10-minutes-with-locust). The latest results live in
[`docs/load-test/`](../docs/load-test/) (charts, Locust's full HTML report, raw CSVs) and in
`app/data/loadtest_report.json` (what the admin page shows).

> [!CAUTION]
> Load-test a **copy** of the database, never the live one. The test signs in as real accounts, posts comments,
> records watch progress and asks the AI tutor questions.

## What it does

| | |
|---|---|
| **Load** | 50 → 100 → 200 → 300 → 400 concurrent users, 2 minutes each, plus 8 students asking the AI tutor throughout |
| **People** | students 70 %, teachers 20 %, admins 5 %, public visitors 5 %, each signing in once (bcrypt) from its own address, then clicking around with 2–6 s of thinking time |
| **AI** | real questions to the AI tutor, streamed to the end; first-word and whole-answer times go to `results/classnest_ai.csv`, *not* into Locust's request statistics (an answer that streams for 20 s is not a slow page) |
| **Rate limits** | a `429` from the comment or AI limits is the protection working: counted as a success under its own `(rate limited)` name |

## Run it

```powershell
# 1. a copy of the database with the demo school (uploads kept apart from the real ones)
docker compose exec db psql -U classnest -d classnest -c "CREATE DATABASE classnest_demo"
$env:POSTGRES_DB = "classnest_demo"; $env:UPLOAD_DIR = "$env:TEMP\classnest_demo_uploads"
.\.venv\Scripts\python.exe -m app.cli demo-data

# 2. the app on that copy, in the same window. run.py links the local AI model as usual. X-Forwarded-For is
#    trusted from localhost only, so each simulated user gets its own address and the per-address sign-in
#    limit doesn't stop the test.
$env:PORT = "8099"; $env:BASE_URL = "http://127.0.0.1:8099"; $env:TRUST_PROXY_HEADERS = "true"; $env:JOBS_ENABLED = "false"
$env:ADMIN_USERS = '{"admin": "Admin@12345"}'   # the test's admin account (only on this copy)
python run.py

# 3. in another window: Locust in its own venv (keeps gevent & co. out of the app's), the account list,
#    a 30-second dry run, then the 10 minutes
python -m venv .venv-locust; .\.venv-locust\Scripts\python.exe -m pip install locust
python loadtest/prep_accounts.py . loadtest/locust_accounts.json
New-Item -ItemType Directory -Force results/smoke | Out-Null
$env:CN_SMOKE = "1"; .\.venv-locust\Scripts\locust.exe -f loadtest/locustfile.py --headless --host http://127.0.0.1:8099 --csv results/smoke/classnest 2>&1 | Tee-Object results/smoke/console.txt
.\.venv-locust\Scripts\python.exe loadtest/validate_run.py results/smoke/classnest_stats.csv results/smoke/console.txt
Remove-Item Env:CN_SMOKE
.\.venv-locust\Scripts\locust.exe -f loadtest/locustfile.py --headless --host http://127.0.0.1:8099 --csv results/classnest --csv-full-history --html results/report.html 2>&1 | Tee-Object results/console.txt

# optional, started just before the 10 minutes: app + database CPU/memory every 10 s
powershell -File loadtest/sample_resources.ps1 -AppPid <uvicorn pid> -Out results/resources.csv

# 4. check the run really exercised everything, then build the admin page data, the README charts and docs/load-test/
.\.venv-locust\Scripts\python.exe loadtest/validate_run.py results/classnest_stats.csv results/console.txt
.\.venv-locust\Scripts\python.exe loadtest/build_report.py results .
```

The uvicorn PID is `(Get-NetTCPConnection -LocalPort 8099 -State Listen).OwningProcess`. Set `CN_AI_MODEL` before
`build_report.py` if the AI wasn't the local Gemma 3 1B (e.g. `$env:CN_AI_MODEL = "Claude Sonnet 5.5 · Anthropic API"`).

**Why the validator matters:** a script error inside a Locust task is *not* a failed request (Locust only logs it),
so a broken user class can look like a quiet, fast run. `validate_run.py` fails on any task error, on a missing core
action, or when the AI tutor answered nothing.

**Starting the server another way?** Give uvicorn `--timeout-keep-alive 75` (what `run.py` uses, `KEEP_ALIVE_SECONDS`).
With uvicorn's own 5-second default, a busy single worker resets a few connections just as the next request arrives:
that is what the first full run found, at 300–400 users.

## Files

| File | |
|---|---|
| `locustfile.py` | the user mix, the stepped load shape (`CN_SMOKE=1`: 30 s dry run), the AI timing log |
| `prep_accounts.py` | reads the demo students / teachers / class video from the copy into `locust_accounts.json` (git-ignored) |
| `sample_resources.ps1` | app process and PostgreSQL container CPU / memory every 10 s |
| `validate_run.py` | fails the run if it didn't really exercise the app |
| `build_report.py` | per-step numbers, `app/data/loadtest_report.json`, SVG charts and copies in `docs/load-test/` |
