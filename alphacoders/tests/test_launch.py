"""Exercise the evaluator's launcher on both operating systems."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def test_launcher_uses_selected_database_and_serves_dossier(tmp_path):
    path = tmp_path / "review data" / "desk.db"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [sys.executable, "-m", "app", "--port", str(port)],
        cwd=ROOT,
        env={**os.environ, "MULEX_DB": str(path)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(process.stdout.read())
            try:
                with urlopen(base + "/api/health", timeout=2) as response:
                    assert json.load(response)["status"] == "ready"
                break
            except (URLError, TimeoutError):
                time.sleep(0.15)
        else:
            raise AssertionError("Workbench startup timed out")
        assert path.exists()
        with urlopen(base + "/api/datasets", timeout=5) as response:
            key = json.load(response)[0]["id"]
        with urlopen(
            base + f"/api/datasets/{key}/accounts/ACC-2847", timeout=10
        ) as response:
            assert len(json.load(response)["filings"]) == 6
    finally:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
