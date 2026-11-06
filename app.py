"""Start ClassNest with one command:  python app.py

Installs and starts everything it needs, then runs the web app - the same launcher as `python run.py` and
double-clicking start.bat:
  .venv + Python packages -> .env with fresh secrets -> WSL2 + Docker Desktop -> PostgreSQL, OCR, Mailpit
  -> Gemma 3 on vLLM when there is a GPU -> the web app.
Steps that are already done are skipped, so later starts take seconds. run.py's options work here too:
  python app.py --open        # also open the browser when the site is ready
  python app.py --setup-only  # install + start the services, not the web app

(The web application itself is the `app` package - this file only launches it.)
"""
import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().with_name("run.py")), run_name="__main__")
