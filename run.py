#!/usr/bin/env python3
"""
Master Supervisor Runner for Unified Bot Service
Runs:
  1. Poultry Bot & Dashboard (Flask / Gunicorn Web Service on $PORT)
  2. Back Checker Bot (Telegram Polling Background Worker)
  3. Field Checker Bot (Telegram Polling Background Worker)

All 3 run concurrently in a single container, consuming only 1 instance hour per clock hour.
"""

import os
import sys
import time
import signal
import subprocess
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [Supervisor] %(message)s"
)
logger = logging.getLogger("Supervisor")

# Track managed processes: {name: {"cmd": list, "proc": Popen, "restart": bool}}
managed_processes = {}
is_shutting_down = False

def sigterm_handler(signum, frame):
    global is_shutting_down
    logger.info("Received termination signal (%s). Shutting down all bots gracefully...", signum)
    is_shutting_down = True
    for name, item in managed_processes.items():
        proc = item.get("proc")
        if proc and proc.poll() is None:
            logger.info("Stopping %s (PID %d)...", name, proc.pid)
            try:
                proc.terminate()
            except Exception as e:
                logger.error("Error terminating %s: %e", name, e)

    # Wait up to 5 seconds for processes to finish
    deadline = time.time() + 5
    for name, item in managed_processes.items():
        proc = item.get("proc")
        if proc and proc.poll() is None:
            remaining = max(0.1, deadline - time.time())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                logger.warning("%s did not exit in time. Killing PID %d...", name, proc.pid)
                proc.kill()

    logger.info("All processes stopped. Exiting supervisor.")
    sys.exit(0)

def register_signals():
    signal.signal(signal.SIGINT, sigterm_handler)
    signal.signal(signal.SIGTERM, sigterm_handler)

def start_process(name, cmd, restart_on_failure=True):
    logger.info("Starting %s: %s", name, " ".join(cmd))
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(cmd, env=env)
    managed_processes[name] = {
        "cmd": cmd,
        "proc": proc,
        "restart": restart_on_failure,
        "start_time": time.time(),
        "restarts": 0
    }
    return proc

def load_env_file():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k and k not in os.environ and v:
                    os.environ[k] = v

def main():
    load_env_file()
    register_signals()
    port = os.environ.get("PORT", "10000")

    logger.info("=" * 60)
    logger.info("Starting Unified Bot Service")
    logger.info("=" * 60)

    # 1. Check Configuration
    poultry_token = os.environ.get("POULTRY_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
    back_token = os.environ.get("BACK_CHECKER_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
    field_token = os.environ.get("FIELD_CHECKER_BOT_TOKEN") or os.environ.get("FIELD_TELEGRAM_TOKEN")
    shared_creds = (
        os.environ.get("GOOGLE_CREDS_JSON")
        or os.environ.get("GOOGLE_CREDENTIALS")
        or os.environ.get("POULTRY_GOOGLE_CREDS")
        or (os.path.exists("service_account.json") and "service_account.json")
    )

    logger.info("Configuration Status:")
    logger.info("  - Poultry Bot Token:      %s", "✅ Configured" if poultry_token else "⚠️  Not set (set POULTRY_BOT_TOKEN or BOT_TOKEN)")
    logger.info("  - Back Checker Bot Token: %s", "✅ Configured" if back_token else "⚠️  Not set (set BACK_CHECKER_BOT_TOKEN)")
    logger.info("  - Field Checker Bot Token:%s", "✅ Configured" if field_token else "⚠️  Not set (set FIELD_CHECKER_BOT_TOKEN)")
    logger.info("  - Google Credentials:     %s", "✅ Configured" if shared_creds else "⚠️  Not set (set GOOGLE_CREDS_JSON or GOOGLE_CREDENTIALS)")
    logger.info("  - Web Port ($PORT):       %s", port)
    logger.info("=" * 60)

    # 2. Start Back Checker Bot (Polling)
    if back_token:
        start_process(
            "BackCheckerBot",
            [sys.executable, "-m", "bots.back_checker"]
        )
    else:
        logger.warning("Back Checker Bot skipped because no token was provided.")

    # 3. Start Field Checker Bot (Polling)
    if field_token:
        start_process(
            "FieldCheckerBot",
            [sys.executable, "-m", "bots.field_checker"]
        )
    else:
        logger.warning("Field Checker Bot skipped because no token was provided.")

    # 4. Start Web Service (Poultry Webhook + Dashboard + Healthz)
    # Prefer gunicorn for production; fall back to python app.py if gunicorn isn't available
    try:
        import gunicorn
        web_cmd = [
            sys.executable, "-m", "gunicorn",
            "--bind", f"0.0.0.0:{port}",
            "--workers", "1",
            "--threads", "4",
            "--access-logfile", "-",
            "--error-logfile", "-",
            "app:app"
        ]
    except ImportError:
        logger.warning("gunicorn not found; falling back to Flask development server.")
        web_cmd = [sys.executable, "app.py"]

    start_process("WebService", web_cmd, restart_on_failure=True)

    logger.info("All services initiated. Supervising processes...")

    # 5. Supervision Loop
    while not is_shutting_down:
        try:
            time.sleep(3)
        except KeyboardInterrupt:
            break

        for name, item in list(managed_processes.items()):
            proc = item.get("proc")
            if proc and proc.poll() is not None:
                exit_code = proc.returncode
                logger.warning("Process '%s' exited with code %s.", name, exit_code)

                if item.get("restart", True) and not is_shutting_down:
                    # Rate-limit fast restarts
                    item["restarts"] = item.get("restarts", 0) + 1
                    backoff = min(30, 2 ** min(item["restarts"], 5))
                    logger.info("Restarting '%s' in %d seconds (attempt #%d)...", name, backoff, item["restarts"])
                    time.sleep(backoff)
                    if not is_shutting_down:
                        new_proc = subprocess.Popen(item["cmd"], env=os.environ.copy())
                        item["proc"] = new_proc
                        logger.info("'%s' restarted (PID %d).", name, new_proc.pid)

if __name__ == "__main__":
    main()
