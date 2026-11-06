"""ClassNest one-command launcher - installs and starts everything it needs.

    python run.py              # bootstrap everything, then start the web app
    python run.py --open       # same, and open the site in your browser when it is ready
    python run.py --reload     # same, with auto-reload for development
    python run.py --no-docker  # skip Docker (use your own PostgreSQL / OCR / SMTP from .env)
    python run.py --setup-only # install + start services, but don't start the web app
    python run.py --no-llm     # don't start the local Gemma 3 model even if a GPU is found

`python app.py` runs this same launcher. On Windows you can also double-click start.bat - it installs Python first
if it is missing.

Steps - each one is skipped when it is already done, so normal starts take a few seconds:
 1. creates .venv (or repairs a broken one) and re-launches itself inside it
 2. installs requirements.txt into .venv (only when the file changed)
 3. creates .env from .env.example with fresh secrets if .env is missing
 4. installs WSL2 + Docker Desktop with winget if missing (Windows; asks for administrator approval)
 5. starts Docker Desktop / the Docker engine and waits for it
 6. docker compose up: PostgreSQL + pgvector, Tesseract OCR service, Mailpit - waits until healthy
 6b. AI (AI_PROVIDER=auto|local): finds an NVIDIA GPU (or AMD on Linux), picks the largest Gemma 3 that fits,
     starts it on vLLM in Docker and links the app to it; otherwise Claude (ANTHROPIC_API_KEY) or the gateway
 7. starts the web server (HTTPS when SSL_CERTFILE/SSL_KEYFILE are set in .env)

Only the Python standard library is used until step 2 has finished.
"""
from __future__ import annotations

import hashlib
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
IS_WIN = platform.system() == "Windows"
VENV_PY = VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")
ARGS = set(sys.argv[1:])
DOCKER_DESKTOP = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
DOCKER_CLI = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
SERVICES = ["db", "ocr", "mailpit"]
COLOR = sys.stdout.isatty()


def _out(colour: str, msg: str) -> None:
    tag = f"\033[{colour}m[ClassNest]\033[0m" if COLOR else "[ClassNest]"
    print(f"{tag} {msg}", flush=True)


def say(msg: str) -> None:
    _out("96", msg)


def warn(msg: str) -> None:
    _out("93", msg)


def fail(msg: str) -> NoReturn:
    _out("91", msg)
    sys.exit(1)


def enable_console_colours() -> None:
    """Turn on ANSI escape handling in the Windows console (ENABLE_VIRTUAL_TERMINAL_PROCESSING)."""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle, mode = kernel32.GetStdHandle(-11), ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except (AttributeError, OSError):
        pass


def quiet(cmd: list[str], timeout: float | None = None) -> bool:
    """Run a command silently; True when it exits with code 0."""
    try:
        return subprocess.run(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=timeout).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------- 1. virtualenv
def in_venv() -> bool:
    return Path(sys.prefix).resolve() == VENV.resolve()


def ensure_venv() -> None:
    if in_venv():
        return
    if sys.version_info < (3, 10):
        fail(f"Python 3.10 or newer is required (this is {platform.python_version()}).")
    if not quiet([str(VENV_PY), "-c", "import sys"], timeout=60):
        say("Creating the virtual environment (.venv)..." if not VENV_PY.exists()
            else "The virtual environment is broken - recreating .venv...")
        try:
            subprocess.run([sys.executable, "-m", "venv", "--clear", str(VENV)], cwd=ROOT, check=True)
        except subprocess.CalledProcessError:
            fail("Could not create .venv - is the Python 'venv' module installed?")
    child = subprocess.Popen([str(VENV_PY), str(ROOT / "run.py"), *sys.argv[1:]], cwd=ROOT)
    while True:
        try:
            sys.exit(child.wait())
        except KeyboardInterrupt:
            continue  # Ctrl+C reaches the child too; wait for it to shut down cleanly


# ---------------------------------------------------------------- 2. requirements
def ensure_requirements() -> None:
    req = ROOT / "requirements.txt"
    stamp = VENV / ".requirements.sha256"
    digest = hashlib.sha256(req.read_bytes()).hexdigest()
    if stamp.exists() and stamp.read_text().strip() == digest:
        return
    say("Installing Python packages into .venv (the first run takes a minute or two)...")
    ok = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                         "-r", str(req)], cwd=ROOT).returncode == 0
    if ok:
        stamp.write_text(digest)
        return
    import importlib.util
    if all(importlib.util.find_spec(m) for m in ("fastapi", "uvicorn", "sqlalchemy", "asyncpg", "jinja2")):
        warn("pip could not finish (offline?) - starting with the packages already installed.")
        return
    fail("Installing Python packages failed - check your internet connection and run again.")


# ---------------------------------------------------------------- 3. .env
def ensure_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        return
    example = ROOT / ".env.example"
    if not example.exists():
        fail(".env and .env.example are both missing.")
    say("Creating .env from .env.example with fresh secrets...")
    text = example.read_text(encoding="utf-8")
    for placeholder, value in {
        "SECRET_KEY=replace-with-a-long-random-string": f"SECRET_KEY={secrets.token_urlsafe(48)}",
        "OCR_SHARED_SECRET=replace-with-a-random-string": f"OCR_SHARED_SECRET={secrets.token_urlsafe(24)}",
        "POSTGRES_PASSWORD=change-me": f"POSTGRES_PASSWORD={secrets.token_urlsafe(18)}",
        "\nOPENAI_API_KEY=\n": f"\nOPENAI_API_KEY={secrets.token_urlsafe(24)}\n",  # key of the local vLLM container
    }.items():
        text = text.replace(placeholder, value)
    env.write_text(text, encoding="utf-8")
    warn("Admin logins come from ADMIN_USERS in .env - change those passwords before real use.")


def env_value(key: str, default: str = "") -> str:
    if os.environ.get(key):
        return os.environ[key]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip("'\"") or default
    return default


# ---------------------------------------------------------------- 4. WSL2 + Docker install
def docker_exe() -> str | None:
    found = shutil.which("docker")
    if found:
        return found
    if IS_WIN and DOCKER_CLI.exists():
        os.environ["PATH"] = str(DOCKER_CLI.parent) + os.pathsep + os.environ.get("PATH", "")
        return str(DOCKER_CLI)
    return None


def reboot_pending() -> bool:
    if not IS_WIN:
        return False
    import winreg
    for key in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"):
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key))
            return True
        except OSError:
            continue
    return False


def wsl_ready() -> bool:
    try:
        out = subprocess.run(["wsl.exe", "--status"], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    text = (out.stdout + out.stderr).replace(b"\x00", b"").decode(errors="ignore").lower()
    problems = ("not installed", "unable to start", "virtualization", "enable the", "restart")
    return out.returncode == 0 and not any(p in text for p in problems)


RESTART_HINT = ("Restart Windows, then run start.bat (or python app.py) again.\n"
                "             If this keeps appearing after a restart, enable virtualization "
                "(SVM / Intel VT-x) in your BIOS/UEFI settings.")


def ensure_wsl() -> None:
    if not IS_WIN or wsl_ready():
        return
    if reboot_pending():
        fail("Windows must restart to finish enabling WSL2 / virtualization, which Docker needs.\n"
             "             " + RESTART_HINT)
    say("Installing WSL2 (Docker Desktop needs it) - please approve the administrator prompt...")
    subprocess.call(["powershell", "-NoProfile", "-Command",
                     "Start-Process wsl.exe -ArgumentList '--install','--no-distribution' -Verb RunAs -Wait"])
    if not wsl_ready():
        fail("WSL2 is installed but not active yet. " + RESTART_HINT)


def install_docker() -> str:
    if not IS_WIN:
        fail("Docker is not installed. Install Docker Engine + the compose plugin "
             "(https://docs.docker.com/engine/install/) and run again - or use --no-docker.")
    if not shutil.which("winget"):
        fail("Docker Desktop is missing and winget is unavailable. Install Docker Desktop from "
             "https://www.docker.com/products/docker-desktop/ and run again.")
    say("Installing Docker Desktop with winget (about 600 MB) - please approve the administrator prompt...")
    code = subprocess.call(["winget", "install", "-e", "--id", "Docker.DockerDesktop",
                            "--accept-package-agreements", "--accept-source-agreements"])
    exe = docker_exe()
    if code != 0 or not exe:
        fail("Docker Desktop did not install. Install it from https://www.docker.com/products/docker-desktop/ "
             "and run again.")
    return exe


# ---------------------------------------------------------------- 5. Docker engine
def engine_up(docker: str) -> bool:
    return quiet([docker, "info"], timeout=60)


def ensure_engine(docker: str) -> None:
    if engine_up(docker):
        return
    if platform.system() == "Linux":
        say("Starting the Docker service...")
        subprocess.call(["sudo", "systemctl", "start", "docker"])
    else:
        say("Starting Docker Desktop (first launch: accept its terms in the Docker window)...")
        # `docker desktop start` (Docker Desktop 4.37+) also works when the Docker window is open but the engine
        # is stopped; older versions are started by launching the app itself.
        if not quiet([docker, "desktop", "start", "--detach"], timeout=120):
            if IS_WIN and DOCKER_DESKTOP.exists():
                subprocess.Popen([str(DOCKER_DESKTOP)], creationflags=subprocess.DETACHED_PROCESS)
            elif platform.system() == "Darwin":
                subprocess.call(["open", "-a", "Docker"])
            else:
                fail("Could not start Docker Desktop - start it yourself and run again.")
    say("Waiting for the Docker engine (up to 5 minutes)...")
    deadline = time.time() + 300
    while time.time() < deadline:
        if engine_up(docker):
            say("Docker engine is running.")
            return
        time.sleep(3)
    fail("The Docker engine did not start. Open Docker Desktop, accept its terms and wait for "
         "'Engine running' - if Docker was just installed, restart Windows first. Then run again.")


# ---------------------------------------------------------------- 6. compose services
def tree_digest(folder: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            h.update(path.relative_to(folder).as_posix().encode())
            h.update(path.read_bytes())
    return h.hexdigest()


def ensure_services() -> None:
    docker = docker_exe()
    if docker is None:
        ensure_wsl()
        docker = install_docker()
    if not engine_up(docker):
        ensure_wsl()
        ensure_engine(docker)
    if not quiet([docker, "compose", "version"], timeout=60):
        fail("'docker compose' is not available - update Docker Desktop / install the compose plugin.")

    stamp = VENV / ".ocr-image.sha256"
    ocr_digest = tree_digest(ROOT / "ocr_service")
    rebuild = not (stamp.exists() and stamp.read_text().strip() == ocr_digest)
    say("Starting PostgreSQL + pgvector, the OCR service and Mailpit"
        + (" (first run downloads/builds about 700 MB)..." if rebuild else "..."))
    cmd = [docker, "compose", "up", "-d", "--wait", "--wait-timeout", "300", *(["--build"] if rebuild else []),
           *SERVICES]
    if subprocess.run(cmd, cwd=ROOT).returncode != 0:
        port = env_value("POSTGRES_PORT", "5432")
        fail("docker compose could not start the services - see the messages above.\n"
             f"             Port in use? Ports {port} (PostgreSQL), 8001 (OCR), 8025/1025 (Mailpit) must be free; "
             "change POSTGRES_PORT in .env if another PostgreSQL uses it.  Logs: docker compose logs db")
    stamp.write_text(ocr_digest)

    host = env_value("POSTGRES_HOST", "127.0.0.1")
    port = int(env_value("POSTGRES_PORT", "5432"))
    deadline = time.time() + 60
    while not port_open(host, port):
        if time.time() > deadline:
            fail(f"PostgreSQL is running in Docker but {host}:{port} is not reachable - check POSTGRES_HOST/PORT in .env.")
        time.sleep(1)
    say("Services ready:  PostgreSQL :%s   OCR http://localhost:8001/health   Mailpit (test inbox) "
        "http://localhost:8025" % port)
    ensure_local_llm(docker)


# ---------------------------------------------------------------- 6b. local AI model (Gemma 3 on vLLM)
# (minimum VRAM in MiB, model, context length). bf16 weights: 1B ~2 GB, 4B ~8.6 GB, 12B ~24 GB, plus KV cache.
# The unsloth/* repos are ungated copies of Google's Gemma 3 weights (same Gemma licence); set VLLM_MODEL (and
# HF_TOKEN) to use google/gemma-3-* instead.
GEMMA_TIERS = [
    (46_000, "unsloth/gemma-3-12b-it", 16384),
    (31_000, "unsloth/gemma-3-12b-it", 8192),
    (15_500, "unsloth/gemma-3-4b-it", 16384),
    (11_500, "unsloth/gemma-3-4b-it", 8192),
    (3_500, "unsloth/gemma-3-1b-it", 8192),
]
LLM_IMAGES = {"nvidia": ("VLLM_IMAGE", "vllm/vllm-openai:v0.33.0"),
              "amd": ("VLLM_AMD_IMAGE", "rocm/vllm:rocm7.14.1_rdna_ubuntu24.04_py3.14_pytorch_2.11_vllm_0.23.0")}


def detect_gpu() -> tuple[str, int, int, str] | None:
    """(vendor, total MiB, free MiB, name) of the largest GPU vLLM in Docker can use, or None.
    NVIDIA works on Linux and on Windows (Docker Desktop + WSL2). AMD needs Linux with ROCm (/dev/kfd);
    Docker Desktop on Windows cannot pass AMD GPUs to containers."""
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run([smi, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=30)
            rows = [[c.strip() for c in line.split(",")] for line in out.stdout.splitlines() if line.count(",") == 2]
            if out.returncode == 0 and rows:
                name, total, free = max(rows, key=lambda r: int(r[1]))
                return "nvidia", int(total), int(free), name
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    if platform.system() == "Linux" and Path("/dev/kfd").exists():
        best = 0
        for f in Path("/sys/class/drm").glob("card*/device/mem_info_vram_total"):
            try:
                best = max(best, int(f.read_text().strip()) // (1024 * 1024))
            except (OSError, ValueError):
                continue
        if best:
            return "amd", best, best, "AMD GPU (ROCm)"
    return None


PLACEHOLDER_KEYS = {"", "change-me-gateway-key"}
# Host port of the model's API (inside the container vLLM listens on 8000). Not 8000 on the host: that is
# uvicorn's / FastAPI's usual port and people run the web app there.
VLLM_DEFAULT_PORT = "8002"
# vLLM measures free memory from inside its own process, after the CUDA runtime (and on Windows the WSL layer)
# took its share: on a 4 GB laptop GPU it saw 3.18 GiB free where the host reported 3.87 GiB.
CUDA_OVERHEAD_MIB = 950


def local_llm_key() -> str:
    """The vLLM container's API key = the app's OPENAI_API_KEY in .env (generated on install), so run.py, a
    manual `docker compose up` and the app all agree. Older installs fall back to a key kept in .venv."""
    for name in ("VLLM_API_KEY", "OPENAI_API_KEY"):
        value = env_value(name, "")
        if value not in PLACEHOLDER_KEYS:
            return value
    path = VENV / ".vllm-api-key"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(24))
    return path.read_text().strip()


def stop_local_llm(docker: str, *services: str) -> None:
    targets = list(services) or ["llm-nvidia", "llm-amd"]
    profiles = [arg for s in targets for arg in ("--profile", s)]
    quiet([docker, "compose", *profiles, "stop", *targets], timeout=180)


def running_llm(docker: str, service: str) -> tuple[str, str, str] | None:
    """(model, health, host port) of the running LLM container, if any. Health: starting | healthy | unhealthy;
    host port "" when Docker doesn't say."""
    try:
        cid = subprocess.run([docker, "compose", "--profile", service, "ps", "-q", "--status", "running", service],
                             cwd=ROOT, capture_output=True, text=True, timeout=60).stdout.strip()
        if not cid:
            return None
        out = subprocess.run([docker, "inspect", "--format",
                              "{{json .Args}}|{{.State.Health.Status}}|{{json .HostConfig.PortBindings}}",
                              cid.splitlines()[0]], capture_output=True, text=True, timeout=60).stdout.strip()
        import json
        raw_args, health, raw_ports = (out.rsplit("|", 2) + ["", ""])[:3]
        args = [str(a) for a in json.loads(raw_args or "[]")]
        if args[:1] == ["serve"]:  # entrypoint ["vllm", "serve"]: Docker reports Args = ["serve", <model>, ...]
            args = args[1:]
        try:  # {"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8002"}]}
            host_port = str(json.loads(raw_ports)["8000/tcp"][0]["HostPort"])
        except (ValueError, KeyError, IndexError, TypeError):
            host_port = ""
        return (args[0], health.strip(), host_port) if args else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def ensure_local_llm(docker: str) -> None:
    """AI_PROVIDER=auto|local: run Gemma 3 on vLLM on this PC's GPU and link the web app to it."""
    choice = env_value("AI_PROVIDER", "auto").strip().lower()
    if choice not in ("auto", "local") or "--no-llm" in ARGS:
        stop_local_llm(docker)  # another provider is chosen: free the GPU
        return
    gpu = detect_gpu()
    if gpu is None:
        if choice == "local":
            warn("AI_PROVIDER=local, but no GPU usable by Docker was found (NVIDIA on Windows/Linux, AMD on Linux "
                 "with ROCm). Using the fallbacks - or set AI_PROVIDER=anthropic and an ANTHROPIC_API_KEY.")
        return
    vendor, total, free, name = gpu
    tier = next(((m, n) for vram, m, n in GEMMA_TIERS if total >= vram), None)
    model = env_value("VLLM_MODEL", "") or (tier[0] if tier else "")
    if not model:
        warn(f"{name} has {total // 1024} GB of memory - too little for Gemma 3 on vLLM (needs 4 GB). "
             "Set AI_PROVIDER=anthropic to use Claude instead.")
        return
    max_len = env_value("VLLM_MAX_MODEL_LEN", "") or str(tier[1] if tier else 8192)
    key, served = local_llm_key(), env_value("VLLM_SERVED_NAME", "gemma3")
    port = env_value("VLLM_PORT", VLLM_DEFAULT_PORT)
    service = f"llm-{vendor}"
    image_var, image_default = LLM_IMAGES[vendor]
    image = env_value(image_var, image_default)
    current = running_llm(docker, service)
    if current and current[0] == model and current[1] == "healthy" and current[2] in ("", port):
        # Already serving this model on this port: leave it alone. (Re-measuring free memory now would see vLLM's
        # own reservation, shrink VLLM_GPU_MEMORY_UTILIZATION and make compose recreate the container too small.)
        say(f"Gemma 3 ({model}) is already running on your {name}.")
        cmd = None
    else:
        if current:  # another model or port, or one that is failing/still loading: free the GPU, then measure
            if current[0] == model and current[2] not in ("", port):
                say(f"Moving the local model ({model}) from port {current[2]} to port {port}...")
            else:
                say(f"Restarting the local model ({current[0]}, {current[1] or 'no health yet'}) as {model}...")
            stop_local_llm(docker, service)
            time.sleep(3)
            gpu = detect_gpu() or gpu
            vendor, total, free, name = gpu
        # Ask vLLM for what will really be free inside the container, capped at 90 %.
        util = env_value("VLLM_GPU_MEMORY_UTILIZATION", "") or \
            f"{max(0.5, min(0.90, (free - CUDA_OVERHEAD_MIB) / total)) - 0.005:.2f}"
        eager = env_value("VLLM_EAGER", "") or ("--enforce-eager" if total < 6000 else "--no-enforce-eager")
        os.environ.update({"VLLM_MODEL": model, "VLLM_MAX_MODEL_LEN": max_len, "VLLM_GPU_MEMORY_UTILIZATION": util,
                           "VLLM_API_KEY": key, "VLLM_SERVED_NAME": served, "VLLM_EAGER": eager,
                           "VLLM_PORT": port})  # compose publishes the port run.py links the app to
        cmd = [docker, "compose", "--profile", service, "up", "-d", service]
    if cmd is None:
        pass
    elif quiet([docker, "image", "inspect", image], timeout=60):
        say(f"Starting Gemma 3 ({model}) on vLLM on your {name}...")
        if subprocess.run(cmd, cwd=ROOT).returncode != 0:
            warn(f"The local AI model did not start - see:  docker compose logs {service}")
            return
    else:
        log_path = VENV / "local-llm-setup.log"
        say(f"Downloading the vLLM server image for your {name} (about 10 GB, first run only) in the background.\n"
            f"             The app starts now and switches its AI on by itself once Gemma 3 is ready. "
            f"Progress: {log_path}")
        with log_path.open("w", encoding="utf-8") as log:
            subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    # Link the web app (env vars override .env): the OpenAI-compatible client talks to the local container.
    # vLLM batches requests itself (continuous batching), so the app may send as many at once as vLLM schedules;
    # the old limit of 4 made students queue for a minute in the stress test while the GPU had capacity left.
    batch = env_value("VLLM_MAX_NUM_SEQS", "16") or "16"
    concurrency = str(max(int(env_value("AI_MAX_CONCURRENCY", "4") or 4), int(batch)))
    os.environ.update({"AI_PROVIDER": "openai", "OPENAI_BASE_URL": f"http://127.0.0.1:{port}/v1",
                       "OPENAI_API_KEY": key, "LLM_MODEL": served, "AI_MAX_CONCURRENCY": concurrency,
                       "LLM_VISION": "false" if "-1b" in model else env_value("LLM_VISION", "auto"),
                       "LOCAL_LLM_INFO": f"{name} · {model}"})
    say(f"AI: {model} on {name} via vLLM -> http://127.0.0.1:{port}/v1 "
        "(each start loads the model for 1-3 minutes)")


# ---------------------------------------------------------------- 7. web server
def open_browser_when_ready(url: str, port: int) -> None:
    import threading
    import webbrowser

    def wait_then_open() -> None:
        deadline = time.time() + 120
        while time.time() < deadline:
            if port_open("127.0.0.1", port):
                webbrowser.open(url)
                return
            time.sleep(0.5)

    threading.Thread(target=wait_then_open, daemon=True).start()


def start_server() -> None:
    import uvicorn

    from app.config import settings

    ssl = bool(settings.SSL_CERTFILE and settings.SSL_KEYFILE)
    url = f"{'https' if ssl else 'http'}://localhost:{settings.PORT}"
    if port_open("127.0.0.1", settings.PORT):
        if str(settings.PORT) == env_value("VLLM_PORT", VLLM_DEFAULT_PORT):
            fail(f"Port {settings.PORT} is set for both the web app (PORT) and the local AI model (VLLM_PORT) in "
                 f".env - give one of them another port (the AI's default is {VLLM_DEFAULT_PORT}).")
        fail(f"Port {settings.PORT} is already in use - ClassNest may already be running at {url}.\n"
             "             Close the other window/program first, or set a different PORT in .env.")
    say(f"Starting ClassNest on {url}   (press Ctrl+C to stop)")
    if "--open" in ARGS:
        open_browser_when_ready(url, settings.PORT)
    # X-Forwarded-For is honoured only behind a configured reverse proxy, and only from FORWARDED_ALLOW_IPS
    # (never "*": with "*" the client-controlled left-most entry would become the client IP).
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload="--reload" in ARGS,
                proxy_headers=settings.TRUST_PROXY_HEADERS, forwarded_allow_ips=settings.FORWARDED_ALLOW_IPS,
                ws_max_size=1024 * 1024, log_level="info", timeout_keep_alive=settings.KEEP_ALIVE_SECONDS,
                ssl_certfile=settings.SSL_CERTFILE if ssl else None, ssl_keyfile=settings.SSL_KEYFILE if ssl else None)


def main() -> None:
    os.chdir(ROOT)
    if IS_WIN and COLOR:
        enable_console_colours()
    if os.environ.get("IN_DOCKER") != "1":  # the app container is already fully provisioned
        ensure_venv()
        ensure_requirements()
        ensure_env()
        if "--no-docker" not in ARGS:
            ensure_services()
    if "--setup-only" in ARGS:
        say("Setup complete - start the app with:  python app.py")
        return
    start_server()


if __name__ == "__main__":
    main()
