<div align="center">

<img src="app/static/img/logo-256.png" width="104" alt="ClassNest logo">

# ClassNest

### The AI-powered classroom for small schools & tuition centres

**Live classes · AI notes · AI tests · AI tutor · holiday-aware scheduling · auto-attendance · fees & payroll · PTMs**
<br/>with a private AI that runs **on your own GPU in Docker** — or **Claude** when you'd rather not run it locally.

<br/>

![Python](https://img.shields.io/badge/Python-3.12%20%7C%203.15-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL%2016-pgvector-4169E1?logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white)
![vLLM](https://img.shields.io/badge/vLLM-v0.33-FF6F00)
![Gemma 3](https://img.shields.io/badge/Gemma%203-NVIDIA%20%7C%20AMD-8B5CF6)
![Claude](https://img.shields.io/badge/Claude-Opus%205.5-D97757?logo=anthropic&logoColor=white)
![OCR](https://img.shields.io/badge/OCR-Tesseract-0A7BBB)
![Security](https://img.shields.io/badge/attack%20suite-40%2F40%20passing-2EA44F)
![Load test](https://img.shields.io/badge/load%20test-400%20users%20%C2%B7%200%20failed-2EA44F)
![Classes](https://img.shields.io/badge/Class%201%E2%80%9312%20%C2%B7%20UG%20%C2%B7%20exams-any%20subject-F59E0B)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

[**Quick start**](#-quick-start) · [**Screenshots**](#-screenshots) · [**Classes & subjects**](#-classes-subjects--batches) · [**Demo school**](#-demo-school) · [**AI models**](#-ai-models--local-gemma-3-or-claude) · [**Features**](#-feature-tour) · [**Security**](#-security) · [**Tested**](#-tested-under-attack-and-load) · [**Load test**](#-load-test--10-minutes-with-locust)

</div>

> [!WARNING]
> **Student-grade project.** ClassNest is a learning / demonstration project provided **“as is”, without any warranty**.
> The author is **not responsible** for any loss, damage, data breach, financial or academic consequence arising from its use.
> Have it reviewed by professionals before using it with real children's data or money. The code is released under the
> [MIT License](LICENSE); the full disclaimer is also on the app's built-in **Terms & Conditions** page (`/p/terms`),
> which cannot be deleted from the admin panel.

---

## 🌟 At a glance

<table>
<tr>
<td width="33%" valign="top">

### 🎓 Teach
- Google-Meet-style **live classes** inside the app, recorded
- **AI class notes** from captions + chat
- **AI tests** from the batch's own notes (RAG)
- **AI tutor** that can read a paused video frame
- **Comments & replies** on class videos
- Holiday-aware **calendar & tasks**

</td>
<td width="33%" valign="top">

### 🏫 Run the school
- Students & staff with **virtual ID cards**
- **ID-card sign-in** (OCR) + one-time codes
- **Auto-attendance** from meetings & videos
- **Fees & payroll** — Cashfree or proof uploads
- **PTMs**, broadcasts, directory, reports desk
- **Friends & live chat**, groups across batches

</td>
<td width="33%" valign="top">

### 🛡 Stay safe
- Private **AI on your GPU** (or Claude)
- Abusive-language filter → **auto IP ban**
- **Ban / delete on the spot** (30-day restore)
- CSP nonces, CSRF, bcrypt, lockouts, SRI
- **40/40** scripted attack checks passing
- Inclusive, **gentle tone** for special needs

</td>
</tr>
</table>

---

## 📸 Screenshots

<sub>Taken in Microsoft Edge from the built-in [demo school](#-demo-school), with the local Gemma 3 1B model on a 4 GB laptop GPU.</sub>

<table>
<tr>
<td width="50%"><img src="docs/screenshots/home.png" alt="Public website"><br/><sub><b>Public website</b> — edited with the built-in section builder</sub></td>
<td width="50%"><img src="docs/screenshots/admin-dashboard-light.png" alt="Admin dashboard"><br/><sub><b>Admin dashboard</b> — students, staff, fees, attendance and test scores at a glance</sub></td>
</tr>
<tr>
<td><img src="docs/screenshots/admin-dashboard-dark.png" alt="Admin dashboard in dark mode"><br/><sub><b>Dark mode</b> — follows the OS, remembers your choice</sub></td>
<td><img src="docs/screenshots/admin-batch.png" alt="A batch"><br/><sub><b>A batch</b> — students with health notes, classes, tasks and tests</sub></td>
</tr>
<tr>
<td><img src="docs/screenshots/student-ai-tutor.png" alt="AI doubt solver"><br/><sub><b>AI doubt solver</b> answering from the class notes (sources under the answer). Honest note: the small 1B model still slips — <i>“…divide 3 by 4 and get a whole number”</i> is wrong; see <a href="#-classes-subjects--batches">which model for which classes</a>.</sub></td>
<td><img src="docs/screenshots/admin-class-edit.png" alt="Edit or reschedule a class"><br/><sub><b>Edit or reschedule</b> one class, or hand it to a substitute host</sub></td>
</tr>
<tr>
<td><img src="docs/screenshots/student-video-comments.png" alt="Comments and replies under a class video"><br/><sub><b>Comments & replies under a class video</b> — <code>▶ 0:04</code> chips jump the video to that moment; teachers are labelled</sub></td>
<td><img src="docs/screenshots/login-banned.png" alt="Sign-in page telling a banned person why"><br/><sub><b>Banned</b> — signed out on the spot; the sign-in page shows the reason and the admin's contact e-mail</sub></td>
</tr>
</table>

<details><summary><b>15 more screenshots</b> — sign-in, calendar, AI insights, fees, IP block, deleted accounts, student & teacher views</summary>
<br/>
<table>
<tr>
<td width="50%"><img src="docs/screenshots/login.png" alt="Sign in"><br/><sub><b>Sign in</b> — password, ID-card scan (OCR + one-time code) or admin</sub></td>
<td width="50%"><img src="docs/screenshots/admin-calendar.png" alt="Holiday-aware calendar"><br/><sub><b>Holiday-aware calendar</b> — every task and class in its batch colour</sub></td>
</tr>
<tr>
<td><img src="docs/screenshots/admin-students.png" alt="Students"><br/><sub><b>Students</b> — batches, special-needs and health flags, parent contacts</sub></td>
<td><img src="docs/screenshots/admin-insights.png" alt="AI insights"><br/><sub><b>AI insights</b> — the facts come from the rules, the AI adds next steps</sub></td>
</tr>
<tr>
<td><img src="docs/screenshots/admin-fees.png" alt="Fees"><br/><sub><b>Fees</b> — dues, receipts, overdue and proofs to verify</sub></td>
<td><img src="docs/screenshots/admin-ai-status.png" alt="AI and services status"><br/><sub><b>AI & services</b> — local Gemma 3 on the GPU, OCR, e-mail, knowledge base</sub></td>
</tr>
<tr>
<td><img src="docs/screenshots/student-dashboard.png" alt="Student dashboard"><br/><sub><b>Student dashboard</b> — today's classes, due work, results</sub></td>
<td><img src="docs/screenshots/student-calendar.png" alt="Student calendar"><br/><sub><b>Student calendar</b> — classes, tasks and tests together</sub></td>
</tr>
<tr>
<td><img src="docs/screenshots/student-test-result.png" alt="Test result"><br/><sub><b>Auto-graded test</b> with explanations</sub></td>
<td><img src="docs/screenshots/student-profile-idcard.png" alt="Profile and ID card"><br/><sub><b>Virtual ID card</b> — also used to sign in</sub></td>
</tr>
<tr>
<td><img src="docs/screenshots/teacher-dashboard-dark.png" alt="Teacher dashboard"><br/><sub><b>Teacher dashboard</b> — classes to start, AI insights for their batches</sub></td>
<td><img src="docs/screenshots/teacher-batch-dark.png" alt="Teacher's batch view"><br/><sub><b>Teacher's batch view</b> — attendance, scores, health notes</sub></td>
</tr>
<tr>
<td><img src="docs/screenshots/teacher-video-comments-dark.png" alt="Teacher watch page with comments"><br/><sub><b>Teacher's watch page</b> — answer students' questions under the video</sub></td>
<td><img src="docs/screenshots/ip-blocked.png" alt="IP block page"><br/><sub><b>IP block</b> — every page and live connection from that network stops at once; reason, end time and admin e-mail shown</sub></td>
</tr>
<tr>
<td><img src="docs/screenshots/admin-deleted-account.png" alt="A deleted, dormant account"><br/><sub><b>Deleted account</b> — dormant for 30 days with a Restore button, then removed for good</sub></td>
<td></td>
</tr>
</table>
</details>

---

## 📚 Contents

1. [Quick start](#-quick-start)
2. [Classes, subjects & batches](#-classes-subjects--batches)
3. [Demo school](#-demo-school)
4. [AI models — local Gemma 3 or Claude](#-ai-models--local-gemma-3-or-claude)
5. [Feature tour](#-feature-tour)
6. [Architecture](#-architecture)
7. [How the key flows work](#-how-the-key-flows-work)
8. [Configuration](#%EF%B8%8F-configuration)
9. [Security](#-security)
10. [Tested under attack and load](#-tested-under-attack-and-load)
11. [Load test — 10 minutes with Locust](#-load-test--10-minutes-with-locust)
12. [Performance & N+1 safety](#-performance--n1-safety)
13. [AI fail-safes](#-ai-fail-safes)
14. [Project layout](#-project-layout)
15. [CLI & maintenance](#-cli--maintenance)
16. [Troubleshooting](#-troubleshooting)

---

## 🚀 Quick start

> **Run `python app.py`** — or double-click `start.bat` on Windows (it also installs Python if it is missing). One command installs and starts everything and skips whatever is already done. (`python run.py` is the same launcher.)

```mermaid
flowchart LR
    A([▶ python app.py / start.bat]) --> B[🐍 Python + .venv<br/>+ packages]
    B --> C[🔑 .env with<br/>fresh secrets]
    C --> D[🐳 WSL2 + Docker<br/>installed & started]
    D --> E[🗄 PostgreSQL · 🔎 OCR<br/>· 📧 Mailpit]
    E --> F{GPU?}
    F -->|NVIDIA / AMD| G[🤖 Gemma 3 on vLLM<br/>in Docker]
    F -->|no| H[☁️ Claude if a key is set<br/>· fallbacks otherwise]
    G --> I([🌐 ClassNest<br/>linked to the AI])
    H --> I
```

| # | What the launcher does |
|---|---|
| 1 | Installs Python 3.12 with winget if Python is missing (`start.bat` only) |
| 2 | Creates `.venv` (repairs a broken one) and installs `requirements.txt` — only when the file changes |
| 3 | Creates `.env` from `.env.example` with fresh secrets (session key, database password, the AI container's key) |
| 4 | Installs WSL2 + Docker Desktop with winget if missing (approve the administrator prompt; Windows may need one restart) |
| 5 | Starts Docker Desktop and waits for the engine |
| 6 | `docker compose up --wait` → PostgreSQL + pgvector, Tesseract OCR, Mailpit |
| 7 | **Finds your GPU, picks the largest Gemma 3 that fits, starts it on vLLM in Docker and links the app to it** |
| 8 | Starts the web server on http://localhost:8080 (HTTPS when a certificate is configured) |

```powershell
python app.py               # everything, then the web app (same as: python run.py)
python app.py --open        # ...and open the browser when it is ready (start.bat does this)
python app.py --reload      # auto-reload while developing
python app.py --setup-only  # install + start the services, not the web app
python app.py --no-llm      # don't start the local AI model even if a GPU is found
python app.py --no-docker   # use your own PostgreSQL (with pgvector) / OCR / SMTP from .env
```

> [!NOTE]
> **First run:** ~700 MB for the database/OCR/mail containers, and — on a PC with a GPU — the vLLM image (~10 GB download, ~33 GB unpacked) plus the model weights (2 GB for Gemma 3 1B). Both download once; later starts take seconds plus 1–3 minutes for the model to load. The web app starts right away and switches its AI on by itself when the model is ready.

| URL | What |
|---|---|
| http://localhost:8080 | 🌐 Website |
| http://localhost:8080/login | 🔑 Sign in (tabs: Student/Staff · ID card · Admin) |
| http://localhost:8080/admin/ai | 🤖 AI & services status, with a live prompt tester |
| http://localhost:8025 | 📧 Mailpit — every email the app sends (welcome, ID cards, OTPs, warnings…) |

**Admin accounts** come from `ADMIN_USERS` in `.env` (template: `admin` / `ChangeMe@123`, `principal` / `ChangeMe@456`). Change them — preferably to bcrypt hashes from `python -m app.cli hash-password`. Weak admin passwords are flagged at startup and on the dashboard.

**First steps:** Batches → Calendar (add national holidays) → Staff (add a teacher) → Students → Classes / Tests → check Mailpit for the ID cards. Just exploring? Load the [demo school](#-demo-school) instead.

<details><summary><b>🐳 Run everything in Docker instead</b></summary>

```powershell
docker compose --profile app --profile llm-nvidia up -d --build   # NVIDIA GPU (use llm-amd on a ROCm Linux box)
```
The `app` container reaches the model at `http://llm:8000/v1` (a network alias shared by both GPU flavours).
</details>

<details><summary><b>🔒 HTTPS for other devices (camera & microphone)</b></summary>

Browsers only allow camera/microphone on `https://` or `http://localhost`. For phones/laptops on your LAN:
```powershell
.\.venv\Scripts\python.exe -m pip install cryptography
.\.venv\Scripts\python.exe -m app.cli make-cert
```
Then set `SSL_CERTFILE=certs/cert.pem`, `SSL_KEYFILE=certs/key.pem`, `SESSION_COOKIE_SECURE=true` and `BASE_URL=https://<your-LAN-IP>:8080`.
</details>

---

## 🏫 Classes, subjects & batches

### Which classes — and up to which class?

ClassNest runs classes from **Class 1 up to Class 12**, and three more levels beyond school:

| Level | Choose it for |
|---|---|
| **Class 1 – Class 12** | school students — primary, middle, secondary and senior secondary. Not tied to any board or syllabus: your class notes are the syllabus the AI works from |
| **Undergraduate** | college-level subjects |
| **Competitive Exams** | entrance / competitive-exam coaching batches |
| **Adult Learning** | courses for adults |

Every batch and every student gets one of these levels in the admin forms (the list is `CLASS_LEVELS` in `app/constants.py`).

### Which subjects?

**Any subject.** The subject is free text, so you can teach whatever you offer. The form suggests the common ones:

> Mathematics · Science · Physics · Chemistry · Biology · English · Hindi · Social Studies · History · Geography · Computer Science · Accountancy · Economics · General Knowledge

The AI tutor, class notes and test generator are told the batch's class and subject (*“a friendly Mathematics tutor for Class 10 students”*) and answer from **that batch's own class notes** first.

> [!IMPORTANT]
> **Which AI model for which classes.** The 1B model that fits a 4 GB GPU explains school topics well when the class notes cover them, but it does make mistakes. In our test it said 3/4 “gives a whole number” (see the [screenshot](#-screenshots)). For **senior classes (9–12), competitive exams and undergraduate** subjects, use a GPU with **12 GB or more** (Gemma 3 4B / 12B are picked automatically) or **Claude**, and keep teachers reviewing AI-written tests before publishing.

### Batch rules

| Rule | Default | Setting in `.env` |
|---|---|---|
| Every batch lasts at least | 1 hour | `MIN_BATCH_MINUTES` |
| Active batches at most | 12 | `MAX_BATCHES` |
| Total batch time on any weekday | 12 hours | `MAX_DAILY_HOURS` |
| A student's batches | may not overlap in time, and must fit the daily limit | — |
| Nothing scheduled or due on | Sunday (+ holidays from the calendar) | `WEEKLY_OFF_DAYS` |

### ✏️ The admin can edit everything about a batch

**Admin → Batches → *a batch* → Edit**

| What | How it's handled |
|---|---|
| **Name, class (std), subject**, colour, description | saved straight away (the name must stay unique) |
| **Days and start / end time** | re-checked against the batch rules **and every enrolled student's other batches**. An edit that would make someone's classes clash is refused, with the students' names |
| **Teacher** — the host of the batch's live classes | the new teacher is notified and sees the batch at once; the previous one no longer does |
| **Monthly fee**, active / inactive | inactive batches don't count towards the limits |
| **Members** | **Add students** on the batch page (overlap-checked) or remove them with one click |

**Classes that are already scheduled follow the change:** upcoming classes still at the old batch time move to the new time, and students are told. Upcoming live rooms hosted by the old teacher get the new one. A class you gave its own time or a substitute host keeps it. Classes that have started or ended never change.

**One class at a time — Admin → Classes → *a class* → Edit or reschedule:** change the topic, date, start/end time or the **host**, e.g. a substitute teacher. Holidays are respected and students are notified of a new date or time. The substitute gets the room link, sees the class on their dashboard and can start it.

---

## 🎭 Demo school

Want to see ClassNest full of life before adding real people? One command creates a realistic small tuition centre:

```powershell
.\.venv\Scripts\python.exe -m app.cli demo-data                   # create it (36 students)
.\.venv\Scripts\python.exe -m app.cli demo-data --with-insights   # ...and run the AI progress analysis too
.\.venv\Scripts\python.exe -m app.cli demo-data --students 80     # more or fewer students (3-300)
.\.venv\Scripts\python.exe -m app.cli demo-data --clear           # remove the demo again
```

| What you get | |
|---|---|
| 👩‍🏫 **14 staff** | principal, coordinator, five teachers (maths, science, English, physics, chemistry), a computer-science SME, counsellor, accountant, librarian, two cleaners and a security guard |
| 🗂 **8 batches** | Class 8 Maths & Science · Class 10 Maths, Science & English · Class 12 Physics, Chemistry & Computer Science, all within the batch rules |
| 🧑‍🎓 **36 students** | photos, ID proofs, **virtual ID cards**, parents, a few health notes and special needs, two or three batches each |
| 📅 **Classes** | two weeks of held classes with attendance and **class notes the AI tutor answers from**, plus live rooms for the next three days |
| 📝 **Work** | homework with submissions and grades, a weekly test per batch with graded attempts, batch-chat messages |
| 🎬 **Class video** | a short “Rational numbers” clip on the Class 10 Maths class, with a comment thread (a question pinned to 0:04, the teacher's answers and students' replies) |
| 💰 **Money** | two months of fees (paid, pending and overdue) and last month's salaries |
| 📨 **Website** | three contact enquiries |

Every demo account signs in with the password **`Demo@12345`**, for example `diya.fernandes` (student), `sunita.rao` (teacher), `meera.iyer` (principal) or `ramesh.yadav` (cleaner). The command prints more.

> [!CAUTION]
> **Remove the demo (`--clear`) before real use** — everyone can read its password here. `--clear` removes the demo people, batches and enquiries with everything attached to them (classes, fees, attendance…), including demo batches you edited, so don't put real students into demo batches. Demo e-mail addresses use the reserved `demo.classnest.test` domain, so nothing reaches real people. `--with-insights` does send its summary to your `ADMIN_ALERT_EMAILS`.

---

## 🤖 AI models — local Gemma 3 or Claude

ClassNest talks to one AI backend at a time, chosen by **`AI_PROVIDER`** in `.env`:

| `AI_PROVIDER` | Uses | Privacy | Cost |
|---|---|---|---|
| **`auto`** *(default)* | Gemma 3 on **this PC's GPU** when one is found → otherwise **Claude** if `ANTHROPIC_API_KEY` is set → otherwise the built-in fallbacks | ✅ local first | free locally |
| `local` | Always the bundled vLLM container | ✅ never leaves the PC | free |
| `anthropic` | **Claude** through the Anthropic API | data sent to Anthropic | pay per use |
| `openai` | Any OpenAI-compatible server in `OPENAI_BASE_URL` (Ollama, LM Studio, another vLLM…) | depends | depends |
| `off` | No AI — every feature uses its deterministic fallback | ✅ | free |

```mermaid
flowchart LR
    APP[⚡ ClassNest] -->|OpenAI-compatible API<br/>Bearer key| VLLM
    APP -.->|Anthropic SDK<br/>when AI_PROVIDER=anthropic| CLAUDE[☁️ Claude API]
    subgraph Docker["🐳 Docker"]
        VLLM[vLLM server<br/>127.0.0.1:8002 · alias llm:8000]
        VLLM --> GPU[(🎮 NVIDIA / AMD GPU<br/>Gemma 3 weights)]
    end
```

### 🎮 Local: Gemma 3 on vLLM, in Docker

`run.py` reads the GPU's memory and picks the model automatically (override with `VLLM_MODEL`):

| GPU memory | Model | Context | Images |
|---|---|---|---|
| 4 – 11.5 GB | `gemma-3-1b-it` | 8K | — (OCR text is used instead) |
| 11.5 – 15.5 GB | `gemma-3-4b-it` | 8K | ✅ |
| 15.5 – 31 GB | `gemma-3-4b-it` | 16K | ✅ |
| 31 – 46 GB | `gemma-3-12b-it` | 8K | ✅ |
| 46 GB + | `gemma-3-12b-it` | 16K | ✅ |

| | NVIDIA | AMD |
|---|---|---|
| Works on | Windows (Docker Desktop + WSL2) and Linux | **Linux with ROCm** (Docker Desktop on Windows can't pass AMD GPUs to containers) |
| Compose profile | `llm-nvidia` → `vllm/vllm-openai:v0.33.0` | `llm-amd` → `rocm/vllm` RDNA build (Radeon RX 7000/9000); set `VLLM_AMD_IMAGE=rocm/vllm:latest` for Instinct |
| Needs | a recent NVIDIA driver (the container toolkit ships with Docker Desktop) | `/dev/kfd`, `/dev/dri` (ROCm kernel driver) |

What the launcher takes care of:
- **Memory sizing** — vLLM is given what is really free *inside* the container (the CUDA runtime and WSL take ~0.7 GB on Windows), capped at 90 %; GPUs under 6 GB skip CUDA-graph capture to save memory.
- **Linking** — exports `OPENAI_BASE_URL=http://127.0.0.1:8002/v1` (host port `VLLM_PORT`, so 8000 stays free for the web app), the per-install key and the model name to the app, and matches the app's AI concurrency to vLLM's batch size (vLLM batches requests itself).
- **Restarts** — a healthy container is left alone; a failing one, or one serving another model, is replaced.
- **Weights** are cached in the `hf-cache` Docker volume. The `unsloth/gemma-3-*` repos are ungated copies of Google's Gemma 3 weights under the same Gemma licence; to use `google/gemma-3-*` set `VLLM_MODEL` and `HF_TOKEN`.
- The port is bound to **127.0.0.1 only** and every request needs the key.

> [!TIP]
> **Measured on a 4 GB RTX 3050 laptop GPU** (Gemma 3 1B): first token in ~0.2 s when idle, ~122 tokens/s across 12 parallel requests, room for ~49,800 tokens of context (six full 8K conversations at once). Answers are friendly and fast, but a 1B model makes factual slips — generated tests are always labelled *“review before publishing”*. A 12 GB+ GPU (Gemma 3 4B) or Claude gives much better accuracy.
>
> Because small models slip, ClassNest keeps the facts out of the model's hands where it matters. Tutor answers are grounded in the batch's class notes. Progress alerts state the numbers from the rules, so the AI only writes the next steps and the note to parents, and any AI text that quotes figures or leaves placeholders is replaced. Prompt scaffolding the model echoes back (`<context>`, `[1] Class notes…`, `Question: …`) is stripped from answers.

### ☁️ Claude, when you don't want to run the model locally

1. Create an API key at **https://platform.claude.com**.
2. In `.env`: `ANTHROPIC_API_KEY=sk-ant-...` and `AI_PROVIDER=anthropic` (or leave `auto` on a PC without a GPU).
3. Restart. **Admin → AI & services** shows the model, effort and context window.

| Setting | Default | Notes |
|---|---|---|
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | `claude-sonnet-5-5` or `claude-haiku-5-5` are cheaper and faster (Nov 2026 list prices per million tokens in/out: Opus 5.5 $4/$20 · Sonnet 5.5 $2/$10 · Haiku 5.5 $0.10/$0.50) |
| `ANTHROPIC_EFFORT` | `medium` | `low` = quicker, cheaper answers; `high` = more careful reasoning |
| `ANTHROPIC_MAX_TOKENS` | `16000` | room for the model's (hidden) thinking plus the answer |

How it is wired (`app/services/ai_claude.py`, official `anthropic` SDK): every request streams; tests and grading use **structured outputs** (JSON Schema), so answers always have the right shape; **refusal fallbacks are switched on** (`fallbacks: "default"`) so a request the safety filter declines is retried server-side on Anthropic's recommended model — and if the whole chain declines, the feature falls back (the doubt goes to the teacher). Claude has no embeddings endpoint, so knowledge search uses the built-in lexical vectors.

---

## ✨ Feature tour

### 🌐 Public website — edited like WordPress
| | |
|---|---|
| 🧱 **Section builder** | Hero, feature grid, stats, “how it works”, testimonials, FAQ, gallery, rich text, call-to-action and contact sections. Drag to reorder, show/hide, edit with a rich-text editor (Quill) and image uploads. |
| 🎨 **Branding** | Institute name, tagline, logo, primary/accent colours, footer, social links. |
| 📞 **Contact** | Your school's email, WhatsApp and phone come from `.env` (`CONTACT_EMAIL`, `CONTACT_WHATSAPP`, `CONTACT_PHONE`) and can be overridden in Branding. Floating WhatsApp button, contact form with honeypot + rate limiting. |
| 📄 **Pages** | Terms & Conditions (with the disclaimer), Privacy, About — or any page you create. |

### 👩‍🏫 Academics
| | |
|---|---|
| 🗂 **Batches** | Class 1–12, Undergraduate, Competitive Exams or Adult Learning, any subject. Each batch ≥ **1 hour**, at most **12 active batches**, at most **12 hours/day** cumulative (per weekday meter). Students can't join overlapping batches. Name, class, subject, days, times, teacher and members are all [editable by the admin](#%EF%B8%8F-the-admin-can-edit-everything-about-a-batch), and scheduled classes follow. |
| 🗓 **Holiday-aware calendar** | Holidays block **assignment dates and deadlines**. Vacations (e.g. **May summer break**) allow **holiday homework only**. Weekly offs (Sunday by default). One-click national holidays + summer vacation preset. Date fields show live hints (“⛔ Diwali — not allowed”). |
| ✅ **Task-based scheduling** | Assignments, homework, projects and tests appear on each student's **calendar**. Submissions (text + file), late flag, grading & feedback. |
| 🎥 **Classes** | Each class uses either a **pre-recorded video** (no meeting needed) or a **live class**. Without a video, a live meeting is **mandatory** — a room is created and **admins are warned by SMTP email** (on scheduling, 24 h before, and if it hasn't started 15 min after the start time). Any class can be **rescheduled or given a substitute host** until it starts. |
| 💬 **Video comments** | Under every class video and recording: **comments with replies** (a reply to a reply stays in the thread), optional **“pin to this moment”** timestamps that jump the video there, *Teacher* / *Admin* labels, live updates without reloading. Only the batch, its teacher and admins can see them. Replies notify the person answered; a student's comment notifies the teacher. Abusive words are filtered like the chats, and authors or teachers can delete. |
| 🧪 **AI tests** | The AI writes MCQ / true-false / short-answer questions from the batch's **class & subject** and the **knowledge base (pgvector RAG)**, constrained to an exact JSON schema. Edit everything, publish to a batch (holiday rules apply), timed test with autosave, auto-grading, AI grading of short answers, question analytics, CSV export. |
| 🧠 **Knowledge base** | Upload notes, PDFs or images (OCR'd). Class notes and video notes are indexed automatically. Hybrid lexical + semantic search with reciprocal-rank fusion. |

### 🎙 Live classes (Google-Meet-style, inside the app)
| | |
|---|---|
| 📹 **WebRTC** | HD video grid, mute/camera, **screen sharing**, chat, hand-raise, reactions, speaking indicators, participant list, host mute/remove, auto-reconnect. |
| ⏺ **Recording** | The host's browser records a composite of every tile + mixed audio and uploads it in 5-second chunks. It becomes a video for students who missed the class. |
| 📝 **AI notes** | Live captions + chat form the transcript; the AI writes structured notes (summary, key concepts, examples, Q&A, homework). |
| 🙋 **Auto-attendance** | Present if the student attends the **whole meeting** (`MEETING_ATTENDANCE_RATIO`, default 90% to tolerate network blips) **or watches ≥ 50%** of the video/recording (unique seconds watched, anti-skip; the video length comes from the server, never the student's browser). |

### 🤖 AI everywhere
| | |
|---|---|
| 💬 **AI doubt solver** | Per-lesson and per-batch tutor that answers from the class's own notes, streamed live. **📸 Ask about this moment**: screenshots the paused video frame + timestamp — vision models read the image; OCR text from the frame and the transcript at that moment are added too. |
| 🧑‍💼 **Private AI assistant** | Live chat that **remembers context** (rolling memory) and is **visible only to the user**. Staff can ask about a student's progress (real attendance/score data for their batches); students ask what's due or how they're doing. |
| 🛡 **Concerns → reports** | If a student complains about a staff member (or anyone raises bullying / harassment / safety), **only that message** is logged to the admin **Reports desk**: *dismiss · accept · resolve · ban*. The reporter gets notified of the outcome. |
| 📈 **Performance monitor** | Daily analysis of scores, attendance and missed work → alerts to **admins** (digest), **batch teachers** (digest + in-app) and **parents** (polite email). |
| 💛 **Inclusive tone** | Students flagged with special needs / health issues get lenient thresholds and **gentle, patient wording** everywhere — except formal warnings. |

### 🧑‍🤝‍🧑 People, onboarding & community
| | |
|---|---|
| 🪪 **Registration** | Photo (upload or **webcam**), **date of birth**, contact details and **ID proof** are mandatory. Staff roles: teacher, principal, vice-principal, coordinator, **SME**, counsellor, accountant, librarian, lab assistant, receptionist, **cleaner**, security, driver… |
| 📧 **Welcome emails** | New joiners get a **thank-you email** (parent in CC) and a **confidential** email with their **virtual ID card** (inline + PNG) and password. |
| 🔑 **Sign-in** | Username/password, or **scan the ID card → Tesseract OCR → one-time code to email and phone**. Admins sign in with the accounts in `.env`. |
| 🚪 **Remove · ban · delete** | Every action takes effect **on the spot**: sessions end and live classes and chats close. **Remove** withdraws access with a stated reason (templates: spamming, bad behaviour, fee default…); reinstate any time. **Ban** keeps the account and its data but shows the person as *Banned* everywhere; their sign-in page says *banned*, gives the reason and the **admin's contact e-mail**. You can also block their network. **Delete** puts the account **dormant for 30 days**: hidden, shown as “Deleted user”, restorable with one click. After that it is **removed for good** automatically, files included. Comments others replied to stay as placeholders. |
| 🏥 **Health records** | Conditions, medication, emergency contacts — visible to the student's batch teachers. |
| 👫 **Friends & live chat** | Friend requests, **unfriend, block**, **live direct messages** over WebSockets (typing indicator, unread counts, toasts), **groups with classmates and teachers from any batch**, report button. Conversations with a banned or deleted person become read-only. |
| 💬 **Batch chats** | Live group chat per batch with moderator deletion. |

### 💰 Finance
| | |
|---|---|
| 🧾 **Fees** | Monthly dues per batch, receipts, statements, CSV. **If Cashfree is set up**: students pay online and fees **reflect automatically** (webhook + server-side verification). **If not**: students upload a **payment screenshot** → the office verifies or rejects with a reason. |
| 💼 **Salaries** | Payments for every staff role. **Cashfree Payouts** (bank / UPI) reflect automatically; otherwise the **salary receipt / proof upload is mandatory**. Staff download receipts and printable slips. |

### 📣 Communication
| | |
|---|---|
| ✉️ **Broadcasts** | Email (and optional SMS) to all students, all parents, batch students/parents, staff by role or extra addresses; formal templates; **AI polish**. |
| 👪 **PTMs** | Formal invitations, **RSVP tracking without login**, online/hybrid video room with guest access, reminders and cancellations. |
| 📇 **Directory** | One-click email, call and **WhatsApp** for students, parents and staff. |

### 🎨 Experience
Premium MDB-based design system, **light & dark mode** (remembers your choice, follows the OS by default), smooth page progress bar, toasts, accessible charts with a table view, **minified + fingerprinted CSS/JS** with long-lived caching. A **feather-in-a-nest favicon** (SVG + PNG + `.ico` + Apple touch icon) and an **installable web-app manifest** that switch to your own logo and colours once you set them in Branding.

---

## 🏗 Architecture

```mermaid
flowchart LR
    subgraph Browser["🧑‍🎓 Browser · Jinja + MDB + vanilla JS"]
        UI[Pages & dashboards]
        RTC[WebRTC live class]
        WS[WebSockets: meet · batch chat · friends]
    end
    subgraph App["⚡ FastAPI · async"]
        MW[Security headers · size guard · sessions · IP bans]
        R[Routers: public · auth · admin · student · staff · meet · chat · social · assistant · api · webhooks]
        S[Services: AI · RAG · mail · OCR · moderation · payments · scheduling · jobs]
    end
    subgraph Docker["🐳 docker compose"]
        DB[(PostgreSQL 16<br/>+ pgvector)]
        OCR[🔎 Tesseract OCR]
        MAIL[📧 Mailpit]
        LLM[🤖 vLLM · Gemma 3<br/>NVIDIA / AMD GPU]
    end
    CLAUDE[☁️ Claude API]
    CF[💳 Cashfree PG & Payouts]
    UI --> MW --> R --> S
    WS --> R
    RTC <-. peer-to-peer media .-> RTC
    S --> DB
    S --> OCR
    S --> MAIL
    S --> LLM
    S -.-> CLAUDE
    S <--> CF
```

| Layer | Technology |
|---|---|
| Web | FastAPI, Starlette sessions, Jinja2, MDB UI Kit 7, Font Awesome, Chart.js, FullCalendar, Quill, SortableJS |
| Data | PostgreSQL 16 + **pgvector** (HNSW cosine indexes), **async SQLAlchemy 2** + asyncpg, FIFO-fair connection pool |
| AI | **vLLM v0.33** serving **Gemma 3** in Docker (OpenAI-compatible API, `openai` SDK) · **Claude** via the official `anthropic` SDK |
| OCR | Tesseract in its own container (FastAPI + pytesseract) |
| Email / SMS | aiosmtplib (Mailpit for development), SMS via console / Twilio / any webhook |

---

## 🔄 How the key flows work

<details open><summary><b>🪪 ID-card sign-in with OCR + OTP</b></summary>

```mermaid
sequenceDiagram
    actor S as Student / Staff
    participant A as ClassNest
    participant O as Tesseract OCR
    participant M as Email + SMS
    S->>A: Upload or snap the virtual ID card
    A->>O: Image (pre-processed, 4 OCR passes)
    O-->>A: Text
    A->>A: Find STU-/STF- number (tolerates O↔0, S↔5 …)
    A->>M: 6-digit code to registered email AND phone (max 5 per hour)
    S->>A: Enter code (5 attempts per code, 10 per hour, 5-minute expiry)
    A-->>S: Signed in ✅ (session rotated, login audited)
```
</details>

<details><summary><b>🎥 Live class lifecycle</b></summary>

```mermaid
flowchart TD
    L[Lesson scheduled] -->|video attached| V[No meeting needed ▶️]
    L -->|no video| M[Live meeting mandatory 🔴]
    M --> W1[📧 Warning email to admins + teacher]
    M --> R[24 h reminder · 15-min-late alert]
    M --> H[Host starts class]
    H --> REC[Composite recording → 5 s chunks]
    H --> CAP[Live captions + chat → transcript]
    H --> END[Class ends]
    END --> ATT[Attendance: ≥ 90% time = present]
    END --> NOTES[🤖 AI notes → pgvector index]
    END --> VID[Recording published → 50% watched = present]
```
</details>

<details><summary><b>🧪 AI test generation (RAG + structured output)</b></summary>

```mermaid
flowchart LR
    B[Batch: class + subject] --> Q[Topic + difficulty + types]
    KB[(Knowledge base<br/>notes · PDFs · class notes)] -->|hybrid vector search| C[Relevant context]
    Q --> P[Prompt with one<br/>example per type]
    C --> P
    P --> G[🤖 Gemma 3 / Claude<br/>JSON Schema enforced]
    G --> VAL{Validate & dedupe}
    VAL -->|enough| D[Draft test → admin edits → publish to calendar]
    VAL -->|some missing| TOP[Top up with editable starter questions]
    VAL -->|AI offline / unusable| F[Fallback: cloze / true-false / short questions from notes]
    TOP --> D
    F --> D
```
</details>

<details><summary><b>💳 Payments: Cashfree or proof</b></summary>

```mermaid
flowchart TD
    D[Fee due] --> Q{Cashfree configured?}
    Q -->|yes| P[Student pays online] --> V[Server re-checks the order with Cashfree<br/>+ signed webhook] --> PAID[Paid ✅ receipt emailed · statement updated]
    Q -->|no| U[Student uploads payment screenshot] --> VER{Office verifies}
    VER -->|approve| PAID
    VER -->|reject with reason| D
```
</details>

<details><summary><b>🤬 Moderation & automatic IP ban</b></summary>

```mermaid
flowchart LR
    MSG[Chat message] --> F{Abusive?}
    F -->|no| OK[Delivered]
    F -->|mild| MASK[Masked, no strike]
    F -->|severe| STRIKE[Masked + strike]
    STRIKE --> N{3 strikes in 24 h?}
    N -->|no| WARN[Warning shown]
    N -->|yes| BAN[🚫 IP ban 72 h<br/>sockets closed]
    BAN --> E1[📧 Admin informed]
    BAN --> E2[📧 Person told why]
```
</details>

<details><summary><b>🛡 Private assistant → reports desk</b></summary>

```mermaid
sequenceDiagram
    actor U as Student
    participant AI as Private assistant
    participant R as Reports desk
    actor Ad as Admin
    U->>AI: "Sir shouts at me and humiliates me in class"
    AI-->>U: Empathetic reply (streamed) + "shared confidentially"
    AI->>AI: Classify concern (AI, keyword fallback) + match the person named
    AI->>R: Report: summary + only this message as evidence
    R->>Ad: 📧 alert
    Ad->>R: Dismiss / Accept / Resolve / Ban (with note)
    R-->>U: 🔔 "Your report was reviewed"
```
</details>

---

## ⚙️ Configuration

Everything lives in **`.env`** (template: `.env.example`; `.env` is git-ignored). Highlights:

| Variable | Default | Purpose |
|---|---|---|
| `HOST` / `PORT` / `BASE_URL` | `0.0.0.0` / `8080` / `http://localhost:8080` | Where the web server listens, and the address used in e-mail links. Change `PORT` and `BASE_URL` together; 8000 works too (the local AI model uses 8002) |
| `AI_PROVIDER` | `auto` | `auto` · `local` · `anthropic` · `openai` · `off` — see [AI models](#-ai-models--local-gemma-3-or-claude) |
| `VLLM_MODEL` / `VLLM_MAX_MODEL_LEN` | *(auto by GPU)* | Local model and context length; `HF_TOKEN` for gated repos |
| `VLLM_PORT` | `8002` | Host port of the local model's API (inside Docker it is `llm:8000`) |
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` | `http://127.0.0.1:8002/v1` / *(generated)* | The vLLM container (filled in by `run.py`) or another OpenAI-compatible server |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` / `ANTHROPIC_EFFORT` | — / `claude-opus-5-5` / `medium` | Claude |
| `LLM_VISION` | `auto` | Send video screenshots to vision models; falls back to OCR text automatically |
| `AI_MAX_CONCURRENCY` | `4` | Parallel AI requests (raised to vLLM's batch size automatically for the local model) |
| `EMBEDDING_MODEL` | *(empty)* | Optional semantic embeddings; lexical vectors always work |
| `ADMIN_USERS` | template accounts | `{"username": "password or bcrypt hash"}` |
| `CONTACT_EMAIL` / `CONTACT_WHATSAPP` / `CONTACT_PHONE` | placeholders (`contact@example.com`, zeros) | Your school's contact details: home page, footer, e-mails, receipts and the ban notices. Set them before going live |
| `ADMIN_ALERT_EMAILS` | *(empty → `CONTACT_EMAIL`)* | Who receives warnings (missed live classes, bans, reports, payment proofs) |
| `MAX_BATCHES` / `MIN_BATCH_MINUTES` / `MAX_DAILY_HOURS` | 12 / 60 / 12 | Batch rules |
| `WEEKLY_OFF_DAYS` | `sun` | Days nothing can be assigned or due |
| `VIDEO_ATTENDANCE_RATIO` / `MEETING_ATTENDANCE_RATIO` | 0.5 / 0.9 | Auto-attendance thresholds |
| `SMTP_*` | Mailpit | Gmail: `smtp.gmail.com`, 587, `SMTP_STARTTLS=true`, App Password |
| `SMS_PROVIDER` | `console` | `twilio` or `webhook` for real SMS OTPs |
| `CASHFREE_*` | disabled | Online fees & payouts. When disabled, proofs are mandatory |
| `ACCOUNT_LOCK_THRESHOLD` / `OTP_SENDS_PER_HOUR` | 10 / 5 | Brute-force & OTP-bombing limits |
| `DELETED_RETENTION_DAYS` | 30 | How long a deleted account stays dormant (restorable) before it is removed for good |
| `COMMENTS_PER_5_MIN` | 10 | Video comments + replies per person |
| `TRUST_PROXY_HEADERS` / `FORWARDED_ALLOW_IPS` | `false` / `127.0.0.1` | Only behind a reverse proxy you control |
| `KEEP_ALIVE_SECONDS` | 75 | Idle connections stay open this long, longer than the 30 s notification poll. Behind a proxy, set it longer than the proxy's upstream idle timeout |
| `ABUSE_MAX_STRIKES` / `ABUSE_WINDOW_HOURS` / `ABUSE_BAN_HOURS` | 3 / 24 / 72 | Moderation |

---

## 🔐 Security

| Control | Where |
|---|---|
| bcrypt (12 rounds) for every password, run off the event loop; constant-time admin checks | `app/security.py` |
| CSRF token on every form + `X-CSRF-Token` for fetch; one-time `_once` tokens stop double submits | `app/security.py`, `app/static/js/app.js` |
| Talisman-style headers: per-request **CSP nonce** (no inline handlers), CDN scripts pinned to exact files + **Subresource Integrity**, `X-Frame-Options`, `nosniff`, Referrer & Permissions policies, HSTS on HTTPS, `no-store` for private pages | `app/middleware.py` |
| **Per-account lockout** (10 failed sign-ins → 15 min, from any IP) for members *and* admins, plus per-IP limits | `app/routers/auth.py` |
| OTP: attempts survive “resend”, ≤ 10 wrong codes and ≤ 5 codes sent per person per hour | `app/routers/auth.py` |
| Sessions bound to the password: a reset (or a changed admin secret) signs out every old session | `app/security.py` |
| Request-size guard before any parsing (forms/JSON 2 MB, uploads by type); chunked bodies cut off mid-stream | `app/middleware.py` |
| Uploads: allow-lists, random names, image re-encoding (strips EXIF/GPS), 50 MP pixel limit before decoding, path-traversal guard | `app/services/storage.py` |
| File serving permission-checked per file; staff limited to the batches they teach | `app/routers/files.py` |
| IP bans for HTTP **and** WebSockets; origin checks, per-person socket caps, per-message budgets, slow readers dropped | `app/middleware.py`, `app/routers/*`, `app/services/chat.py` |
| Bans act **on the spot**. An account ban ends every session and closes the person's live classes, batch chats and friends' chats; open pages reload into a sign-in notice with the reason and the admin's contact e-mail. An IP ban does the same for everyone on that network, showing a block page with the e-mail. | `app/services/accounts.py`, `app/security.py`, `app/static/js/app.js` |
| Deleted accounts stay dormant (hidden, restorable) for `DELETED_RETENTION_DAYS`, then the advisory-locked job purges the person, their files and their messages | `app/services/accounts.py`, `app/services/jobs.py` |
| An account's status (banned / deleted) is revealed only after the password or a one-time code proves who is signing in | `app/routers/auth.py` |
| HTML sanitising (nh3) for CMS pages and AI output; prompt-injection guard for retrieved context | `app/templating.py`, `app/services/ai.py` |
| CSV exports neutralise spreadsheet formulas; e-mail headers can't contain line breaks | `app/routers/common.py`, `app/services/mailer.py` |
| Cashfree webhooks: HMAC signature + server-side re-verification (never trusts the browser) | `app/routers/webhooks.py`, `app/services/payments.py` |
| Local AI bound to 127.0.0.1 with a per-install key; `X-Forwarded-For` ignored unless a trusted proxy is configured | `docker-compose.yml`, `run.py` |
| `.gitignore` / `.dockerignore` keep secrets, student uploads, exports and the decoded word list out of git and images | repo root |

---

## 🧪 Tested under attack and load

Every run used a **throwaway copy** of the app (separate database and upload folder, deleted afterwards).

<table>
<tr><th>🛡 Attack suite (40 checks)</th><th>⚡ Burst test — the pool fix</th><th>🤖 AI on a 4 GB RTX 3050 (Gemma 3 1B)</th></tr>
<tr valign="top">
<td>

**23 vulnerabilities found and fixed** — anonymous unlimited uploads, OTP and password brute force, e-mail/SMS bombing, stale sessions after a reset, attendance cheating, CSV formula injection, cross-batch file access, socket floods, open redirect, crashes on bad input…

Now: **40 / 40 checks pass**, including access control across six roles, IDOR, CSRF, XSS and oversized bodies.

</td>
<td>

| | Before | After |
|---|---|---|
| 60 users, slowest | 16.8 s | **0.83 s** |
| 200-user burst, slowest | 25.7 s | **2.4 s** |
| Errors | 0 | **0** |

Cause: SQLAlchemy's asyncio pool let new requests overtake waiting ones; replaced with a FIFO-fair queue.

</td>
<td>

| 24 tutor questions at once | Before | After |
|---|---|---|
| Median first word | 62 s | **0.9 s** |
| Whole burst | 140 s | **49 s** |

Pages stayed fast during the burst (median 28 ms). AI test generation: **0 → 13 of 15** usable questions after switching to schema-constrained output.

</td>
</tr>
</table>

### 🏫 With a full demo school loaded (final code)

50 people, 8 batches and two weeks of history from the [demo school](#-demo-school), on the 4 GB laptop GPU:

| Check | Result |
|---|---|
| Every page reachable by **10 roles** (student ×2, teacher, principal, coordinator, SME, counsellor, accountant, cleaner, admin) | **344 pages, all HTTP 200** |
| 60 users browsing at once | 3,220 requests · p95 0.54 s · slowest **1.8 s** · 0 errors |
| 200-user burst | 3,033 requests · p95 1.8 s · slowest **3.0 s** · 0 errors |
| **10-minute Locust run**: 50 → 400 people at once, plus 8 asking the AI | **31,900 requests · 0 failed** · p95 52 ms up to 200 people · [full results](#-load-test--10-minutes-with-locust) |
| 8 tutor questions at once (real model) | **8 / 8** answered by the AI · first word ≤ **0.7 s** |
| Real browser pass (Microsoft Edge) | 20 screens in light and dark mode — **no console errors, CSP violations or failed integrity checks** |
| Admin edit flows | **20 / 20** checks: clash refused, classes moved and re-hosted, substitute can start the class, bad dates/hosts rejected |
| Friends, comments, bans, deletion, IP block | **48 / 48** end-to-end checks (below) + **9 / 9** in a real browser |
| Attack suite re-run | **40 / 40** |

### 👥 Friends, comments and account safety — verified end to end

| Area | What was checked |
|---|---|
| Friends + live chat | friend request → accept → direct message delivered **live** both ways, typing indicator, history stored |
| Video comments | post with a 0:04 timestamp, teacher reply (labelled), reply-to-reply joins the thread, notifications to the author and the teacher, other batches get 404, abusive comment refused, only authors/teachers can delete, deleted comments with replies leave a placeholder |
| Ban | open chat socket kicked at once; the session ends; sign-in shows *banned* + reason + admin e-mail; their comments, DMs and batch-chat messages show *Banned*; the DM becomes read-only; unban restores access |
| Delete | sign-in shows *deleted* + the restore-by date; comments hidden; Restore brings everything back; **after 30 days the purge removes the account, its files and comment text** while others' replies stay |
| IP block | friends chat **and** batch chat sockets kicked instantly, every page and API call gets the block page (reason + admin e-mail), new live connections refused, unblock restores access |
| In the browser | posting / replying without reload, timestamp chips seek the video, a banned person's open page jumps to the notice by itself (~3 s), a blocked network's open page switches to the block page |

<details><summary><b>Accounts & community round — found and fixed</b></summary>

| Found | Fix |
|---|---|
| A manual IP ban didn't close friends'/DM chat sockets — an open chat kept working | One `disconnect_ip` closes live classes, batch chats and friends' chats |
| The IP block page showed the `.env` contact address, ignoring Branding | Uses the Branding contact e-mail |
| A ban or removal mid-session just bounced people to the sign-in page without saying why | The session remembers the reason; the sign-in page shows the notice and the admin's e-mail; open pages reload into it |
| Every dialog on a person's page shared field IDs — clicking “Email them” in one dialog toggled another dialog's switch | Form macros take an `id`; each dialog has its own |
| Students saw broken avatar images under comments (photos are private) | Photos only where the viewer may see them; initials otherwise |
| The comment box stayed on “Please wait…” after the first post | The comment forms opt out of the global double-submit guard (they have their own) |

</details>

<details><summary><b>Batch editing, AI notes & layout round — found and fixed</b></summary>

| Found | Fix |
|---|---|
| Changing a batch's days/times never re-checked enrolled students, so classes could silently clash; already-scheduled classes kept the old time and teacher | Clash check on save; upcoming classes follow the batch; new: reschedule one class / substitute host |
| AI progress notes could contradict the data (50 % attendance called *“consistently strong”*) or reach parents as *“Dear [Teacher's Name]”* | Numbers come from the rules; the AI only adds next steps and a supportive note, gated for percentages and placeholders |
| The 1B tutor echoed prompt scaffolding (`<context>`, `[1] Class notes…`, `Question: …`) | Stripped from answers (and from the live stream) |
| Demo classes had no notes for the tutor to use | Correct notes for all 42 demo topics, indexed like real class notes |
| `demo-data --clear` missed a demo batch whose description had been edited | Demo batches are also recognised by their demo classes |
| Every card after the first in a row sat 18 px lower (dashboards, calendar, profile) | Grids no longer add the stacking margin |
| Long batch names cut off in the score chart | Shortened with “…” to fit; full name in the tooltip and table view |
| Dropdown arrow drawn over short select text; AI-insight text wrapped under the avatar | Selects keep room for the arrow; text keeps its column, buttons wrap |
| “100.0%” overflowed the score ring; AI status said *“not resolved yet”* for a configured model | “100%”; shows the model in use |

</details>

<details><summary><b>Final bug / vulnerability / N+1 hunt — found and fixed</b></summary>

| Found | Fix |
|---|---|
| 16 places turned form or socket input into a number with `str.isdigit()` + `int()`. A superscript “²” passes `isdigit()` but crashes `int()`, and so does a 5,000-digit number, so a crafted value caused a server error. Signed-in students could reach this (creating a group, the friends-chat socket). | One strict `int_or()` / `ints()` helper (1–9 plain digits) everywhere; 75 targeted probes, no server errors |
| N+1 queries in three write paths: enrolling students, the timetable clash check, and recording attendance when a live class ends | Batched (numbers in [Performance & N+1 safety](#-performance--n1-safety)) |
| The no-JavaScript fallback of the comment form redirected to the raw `Referer` header | Goes through the same-site-only `back()` helper like every other redirect |

Re-run on the final code: attack suite **40 / 40**, accounts / comments / live chat **48 / 48**, admin edit flows **20 / 20**, XSS payloads in comments rendered as plain text.

</details>

The Claude backend was verified end-to-end (tutor, assistant, test generation, refusals, fallbacks, errors) against a protocol-faithful mock of the Anthropic API — add your key to use the real one.

---

## 📈 Load test — 10 minutes with Locust

<sub>Run on 6 Nov 2026 against a <b>copy</b> of the demo school, on the same laptop as everything else: AMD Ryzen 7 170 (16 threads), 15 GB RAM, RTX 3050 Laptop 4 GB, Windows 11. The same results, with interactive charts, are in the app under <b>Admin → Load test report</b>.</sub>

<table>
<tr>
<td align="center" width="17%">👥<br/><b>400</b><br/><sub>people at once<br/>+ 8 asking the AI</sub></td>
<td align="center" width="17%">📨<br/><b>31,900</b><br/><sub>requests<br/>in 10 minutes</sub></td>
<td align="center" width="16%">✅<br/><b>0</b><br/><sub>failed<br/>requests</sub></td>
<td align="center" width="17%">⚡<br/><b>92 req/s</b><br/><sub>at 400 people,<br/>one worker</sub></td>
<td align="center" width="16%">⏱<br/><b>52 ms</b><br/><sub>p95 response time<br/>up to 200 people</sub></td>
<td align="center" width="17%">🤖<br/><b>82 / 82</b><br/><sub>AI answers by the model,<br/>first word in 0.4 s</sub></td>
</tr>
</table>

**How it was run.** [Locust](https://locust.io) stepped the load up every two minutes — **50 → 100 → 200 → 300 → 400 people at once** — while 8 more students kept asking the AI tutor (Gemma 3 1B on the 4 GB GPU) real questions. Every simulated person signs in (bcrypt) to one of the demo school's accounts from their own IP address, then clicks around with **2–6 seconds** of reading time between pages:

| Who | Share | What they do |
|---|---|---|
| 🧑‍🎓 Students | 70 % | dashboard, the 30-second notification poll, tasks, calendar + events feed, classes, lessons, **the class video** (watch progress + comment refresh), friends & messages, the tutor page, fees, profile — and now and then **a comment** |
| 👩‍🏫 Teachers | 20 % | dashboard, batch pages, insights & doubts, calendar, the watch page |
| 🛡 Admins | 5 % | dashboard with charts, student list, person pages, every list page, calendar |
| 🌐 Visitors | 5 % | public home page, sign-in page, terms (not signed in) |
| 🤖 AI tutor | +8 | a real question every 30–60 s, read to the end of the streamed answer |

<p align="center">
  <img src="docs/load-test/throughput.svg" width="760" alt="Requests per second: rises with each step, levelling off at about 92 per second at 400 people">
  <img src="docs/load-test/latency.svg" width="760" alt="Median and p95 response time: flat and low up to 200 people, rising at 300 and 400">
  <img src="docs/load-test/cpu.svg" width="760" alt="CPU: the app reaches one full core at 400 people; the database stays around a third of a core">
</p>

### Step by step

| People at once | Requests | Failed | Req/s | Median | p95 | p99 | Slowest | AI first word | App CPU¹<br/>avg / peak | DB CPU¹ |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **50** | 1,638 | 0 | 13.7 | 16 ms | 29 ms | 36 ms | 240 ms | 0.2 s | 17 / 43 % | —² |
| **100** | 3,246 | 0 | 27.1 | 17 ms | 34 ms | 43 ms | 240 ms | 0.3 s | 25 / 31 % | 5 % |
| **200** | 6,480 | 0 | 54.0 | 21 ms | 52 ms | 80 ms | 1,100 ms | 0.4 s | 46 / 54 % | 12 % |
| **300** | 9,376 | 0 | 78.1 | 62 ms | 470 ms | 630 ms | 1,100 ms | 0.5 s | 87 / 127 % | 34 % |
| **400** | 11,068 | 0 | 92.2 | 630 ms | 1,100 ms | 1,200 ms | 2,400 ms | 1.6 s | 96 / 120 % | 31 % |

<sub>Percentiles are the median of Locust's rolling 10-second values over each step's steady part (its first 30 s skipped). ¹ % of one CPU core: the app is one async worker, and bcrypt runs in extra threads, so sign-in bursts go above 100 %. ² The first resource sampler read the wrong container (a tooling bug, since fixed), so database CPU starts about 4 minutes in.</sub>

### What the numbers say

- **Up to 200 people at once, ClassNest barely notices:** median ~20 ms, p95 at most 52 ms, the worker less than half busy.
- **300 is comfortable:** p95 0.47 s.
- **At 400, one worker is full.** It levels off at ~92 requests a second and pages slow to a median of 0.63 s (p95 1.1 s), but **nothing failed**. The slowest of all 31,900 requests took 2.4 s. ClassNest runs a single worker by design (live classes and chats keep their rooms in memory), so that is this laptop's ceiling; a faster CPU core raises it.
- **The database is never the bottleneck:** about a third of a core at 300–400 people (peak 54 %).
- **Memory stays small:** the app process grew from 192 MB to 255 MB; the database never passed 332 MB.
- **The AI kept answering:** all 82 questions were answered by the model itself, with no fallbacks. The first word came in 0.2 s at 50 people and 1.6 s at 400. The 8 askers loaded the GPU equally in every step, so that slowdown comes from the busy app worker, not from the GPU. Whole answers: median 12 s, p95 39 s.
- **Sign-in is slow on purpose** (median 0.25 s): bcrypt makes guessing stolen passwords expensive. The CPU spikes at the start of each step are the new arrivals signing in.

> [!NOTE]
> **Simulated people are much busier than real ones.** Each clicks every 2–6 seconds for ten minutes straight; real students read, watch and write for far longer between clicks. Locust ran on the same laptop and never reported running short of CPU. Not covered by this test: downloading the video files themselves (the watch page and progress tracking were tested), and the WebSockets of live classes and chats (live-class video is peer-to-peer).

<details><summary><b>What the load test found — and fixed</b></summary>

| Found | Fix |
|---|---|
| **The first full run lost 73 of about 31,500 requests (0.23 %)** to *connection reset* / *remote end closed connection*, all at 300–400 people, with nothing in the server log. None of the 3,600+ requests sent straight after another response (sign-in, calendar feed, video progress) failed. | uvicorn closes an idle connection after **5 s** by default; on a busy worker that close can land just as the next request is sent on it. Connections now stay open **75 s** (`KEEP_ALIVE_SECONDS`), longer than the 30-second notification poll. Same load again: **0 failures**. |
| The report would have counted the AI tutor's streamed answers (12–39 s) as *requests*, inflating p99 and "slowest" | AI timings are logged separately; the request tables hold only page and API calls |
| A rate-limited comment was counted twice | Counted once, under its own name |
| Once moved into `loadtest/`, the resource sampler measured the AI container instead of the database | It locates the project explicitly and stops if the database container isn't found |

</details>

**Files:** [`docs/load-test/`](docs/load-test/) — Locust's full interactive report (`locust-report.html`), the raw CSVs, AI timings and CPU samples · **Run it yourself:** [`loadtest/README.md`](loadtest/README.md)

<p align="center"><img src="docs/screenshots/admin-load-test.png" width="900" alt="Admin → Load test report"><br/><sub><b>Admin → Load test report</b> — the same run inside the app (<a href="docs/screenshots/admin-load-test-full.png">full page</a>)</sub></p>

---

## ⚡ Performance & N+1 safety

* **Every ORM relationship is `lazy="raise"`** — an accidental lazy load fails loudly instead of silently issuing N queries. Lists use `selectinload` / `joinedload`.
* **Measured, not assumed.** A test-only wrapper counted every SQL statement per request while every role crawled every page: 405 requests across 82 page types.
  - No statement ever repeated more than 5 times in one request (the N+1 signature).
  - The heaviest page (the student dashboard) runs 12 statements, and list pages run the same 5 whether they show 3 rows or 25.
  - The write paths that looped are now batched: adding students to a batch (was 2 queries per student, now **6 in total** for 12 students), the timetable clash check (was 2 per enrolled student, now **14 in total** for 17), and the end-of-class attendance update (was 1 per participant, now **1 statement**).
* **First-come-first-served database pool** (`app/database.py`) — no request can be starved by newcomers, so bursts never approach the 30 s pool timeout.
* **Connections are reused for 75 s** (`KEEP_ALIVE_SECONDS`), longer than the 30-second notification poll. Open pages keep one connection instead of reconnecting on every poll. With uvicorn's 5-second default, a busy server sometimes closed a connection just as the next request arrived (found by the [load test](#-load-test--10-minutes-with-locust)).
* Dashboards use **one round-trip of scalar sub-queries** and grouped aggregates; the performance monitor uses a fixed number of `GROUP BY` queries regardless of student count.
* Notifications, fee dues and attendance are written with **bulk / multi-row inserts** and `ON CONFLICT` upserts.
* **The local model batches requests** (vLLM continuous batching); the app sends up to vLLM's batch size at once.
* CSS/JS are **minified (rcssmin / rjsmin) and fingerprinted** at startup and served with `Cache-Control: immutable`; GZip for responses; WebRTC media is peer-to-peer.
* Background jobs run under a **Postgres advisory lock**, so only one worker runs them.

---

## 🧯 AI fail-safes

```mermaid
stateDiagram-v2
    [*] --> Closed
    Closed --> Closed: success
    Closed --> Open: unreachable / bad key (instant) · 3 failures
    Open --> HalfOpen: cool-down (60 s)
    HalfOpen --> Closed: probe succeeds
    HalfOpen --> Open: probe fails
```

| Feature | When the AI is offline (or declines)… |
|---|---|
| Tests | Starter questions drafted from your notes (cloze / true-false / short), clearly labelled |
| Class notes | Extractive summary of key sentences, terms and questions |
| Doubt solver | Relevant excerpts from class notes + the question is queued for the teacher |
| Short-answer grading | Keyword scoring, flagged for teacher review |
| Progress alerts | Rule-based, tone-aware templates |
| Assistant | Friendly offline message; concerns are still filed |

Also: model auto-discovery, automatic downgrades when a server rejects the `system` role, JSON schemas, JSON mode or images, tolerant JSON parsing with one self-repair round, short connect timeouts, a concurrency limit, and an **AI & services** status page with a live prompt tester.

---

## 🗂 Project layout

```
├── app/
│   ├── main.py              # app factory, middleware, error handling, lifespan
│   ├── config.py            # settings from .env (AI_PROVIDER resolution lives here)
│   ├── models.py            # 46 tables (async SQLAlchemy 2, pgvector)
│   ├── database.py          # async engine + FIFO-fair connection pool
│   ├── security.py          # bcrypt, CSRF, once-tokens, rate limits, auth, session binding
│   ├── middleware.py        # CSP nonce + SRI-pinned CDN, request-size guard, IP bans
│   ├── routers/             # public, auth, admin/*, student, staff, meet, chat, social, assistant, api, files, webhooks
│   ├── services/
│   │   ├── ai.py            # OpenAI-compatible backend (vLLM / gateway) + backend factory
│   │   ├── ai_claude.py     # Claude backend (official anthropic SDK)
│   │   ├── accounts.py      # ban / delete -> 30-day dormancy -> purge, instant disconnects
│   │   └── …                # rag, mailer, ocr, idcard, meetings, testgen, performance, moderation, payments
│   ├── templates/           # Jinja pages + HTML emails
│   ├── static/              # css/app.css + js/*.js (sources) → dist/ (minified, git-ignored)
│   └── data/                # wordlists.dat (encoded moderation lists) · loadtest_report.json (Admin → Load test report)
├── loadtest/                # Locust stress test: locustfile, account prep, run validator, report builder
├── docs/
│   ├── screenshots/         # the images in this README
│   └── load-test/           # latest run: charts, Locust's HTML report, raw CSVs
├── ocr_service/             # Tesseract micro-service (Docker)
├── docker-compose.yml       # db · ocr · mailpit · llm-nvidia / llm-amd (vLLM) · app (profiles)
├── app.py · start.bat       # one-command start: both run run.py
├── run.py                   # the launcher (installs, starts services, picks the AI model)
├── Dockerfile · requirements.txt · .env.example · .gitignore · .gitattributes · .dockerignore
```

---

## 🛠 CLI & maintenance

```powershell
.\.venv\Scripts\python.exe -m app.cli check              # database, AI model and OCR connectivity
.\.venv\Scripts\python.exe -m app.cli hash-password      # bcrypt hash for ADMIN_USERS
.\.venv\Scripts\python.exe -m app.cli build-assets       # re-minify CSS/JS
.\.venv\Scripts\python.exe -m app.cli wordlist-export words.json   # decode moderation lists to edit…
.\.venv\Scripts\python.exe -m app.cli wordlist-import words.json   # …re-encode (then delete words.json)
.\.venv\Scripts\python.exe -m app.cli make-cert          # self-signed HTTPS certificate for the LAN
.\.venv\Scripts\python.exe -m app.cli demo-data          # demo school (--with-insights · --students N · --clear)

docker compose --profile llm-nvidia logs -f llm-nvidia   # watch the local model load / serve
docker compose --profile llm-nvidia stop llm-nvidia      # free the GPU (run.py restarts it next time)
```

**Load test:** the 10-minute Locust run in [`loadtest/README.md`](loadtest/README.md) (always against a copy of the database).

> Tables are created automatically on startup (`CREATE EXTENSION vector` + `create_all`). If you change
> `EMBEDDING_DIM` later, drop the `knowledge_chunks` table and use **Knowledge base → Re-index all**.

---

## 🩺 Troubleshooting

| Symptom | Fix |
|---|---|
| `Could not connect to PostgreSQL` | Start with `python app.py` / `start.bat` (it starts Docker and the database); check `POSTGRES_PORT` isn't used by another Postgres |
| *Port … is already in use* | Another program (or a second ClassNest) has it: close that, or set `PORT` and `BASE_URL` in `.env`. The local AI model sits on 8002 (`VLLM_PORT`); the launcher moves it there by itself |
| Launcher says *Windows must restart* | WSL2 was just enabled — restart once. If it repeats, enable SVM / Intel VT-x virtualization in the BIOS |
| AI features say “offline” | Open **Admin → AI & services**. Local model still loading? It takes 1–3 minutes after each start (`docker compose --profile llm-nvidia logs -f llm-nvidia`) |
| vLLM log: *Free memory on device … is less than desired GPU memory utilization* | Close other GPU apps (games, browsers with hardware acceleration) or set `VLLM_GPU_MEMORY_UTILIZATION=0.6` in `.env` |
| No GPU found / AMD GPU on Windows | vLLM needs NVIDIA on Windows (or AMD on Linux + ROCm) — use `AI_PROVIDER=anthropic` with a Claude key instead |
| Claude: *API key was rejected* | Check `ANTHROPIC_API_KEY` (and that `AI_PROVIDER=anthropic` or `auto`), then restart |
| Camera/mic blocked | Use `https://` (see Quick start) or `http://localhost` |
| No emails | Open Mailpit (http://localhost:8025) or check **Admin → Email log** for SMTP errors |
| ID-card sign-in fails | Start the OCR container (`docker compose up -d ocr`); use a sharp, well-lit photo or the PNG from the email |
| *Too many failed sign-in attempts* | Wait 15 minutes (`ACCOUNT_LOCK_MINUTES`) or restart the app |
| Someone got auto-banned on a shared network | Bans are per IP — lift it in **Admin → Security** and consider a shorter `ABUSE_BAN_HOURS` |
| Behind nginx / Caddy: now and then a *502 Bad Gateway* | The proxy reused a connection the app had just closed. Set `KEEP_ALIVE_SECONDS` longer than the proxy's upstream idle timeout (nginx `keepalive_timeout` 60 s, Caddy 2 min) |
| A person says they are banned / their account was deleted | **Admin → Students/Staff → (status: Banned / Deleted) → the person → Unban / Restore**. A deleted account can be restored for `DELETED_RETENTION_DAYS` (30); after that it's gone for good |

---

<div align="center">

**🪶 ClassNest** — made with ❤️ as a student-grade project
<br/>
[MIT License](LICENSE) · provided “as is”, without warranty · questions and bugs: open an issue on this repository

</div>
