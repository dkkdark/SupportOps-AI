"""Run both local services; stop both cleanly with Ctrl+C."""

import os
import subprocess
import sys
import time
import urllib.request


def main():
    env = {
        **os.environ,
        "FAULT_INJECTION_ENABLED": os.environ.get("FAULT_INJECTION_ENABLED", "true"),
    }
    jobs = []
    try:
        jobs.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "supportops.orders_api:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8001",
                ],
                env=env,
            )
        )
        jobs.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "supportops.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8010",
                ],
                env=env,
            )
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if any(p.poll() is not None for p in jobs):
                raise RuntimeError("A local service failed before becoming ready")
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:8010/health/ready", timeout=1
                ) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("Local API did not become ready within 30 seconds")
        jobs.append(subprocess.Popen([sys.executable, "-m", "scripts.worker"], env=env))
        print("\nSupportOps AI: http://127.0.0.1:8010 · API docs: /docs\n", flush=True)
        while all(p.poll() is None for p in jobs):
            time.sleep(0.5)
        raise SystemExit(next((p.returncode for p in jobs if p.returncode), 1))
    except KeyboardInterrupt:
        pass
    finally:
        for p in jobs:
            if p.poll() is None:
                p.terminate()
        for p in jobs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
