import asyncio
import sys
import time

import pytest

from basilisk_mcp.runner import run_script, worker_env


def write(tmp_path, body):
    script = tmp_path / "scenario.py"
    script.write_text(body)
    return script


def test_timeout_kills_process_group(tmp_path, settings):
    settings.run_timeout_s = 1.0
    script = write(tmp_path, "import subprocess, sys, time\n"
                             "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                             "time.sleep(60)\n")
    start = time.time()
    outcome = asyncio.run(run_script(script, tmp_path, settings))
    assert outcome.timed_out and not outcome.ok
    assert time.time() - start < 15


def test_failure_reports_stderr(tmp_path, settings):
    script = write(tmp_path, "raise RuntimeError('boom from worker')\n")
    outcome = asyncio.run(run_script(script, tmp_path, settings))
    assert not outcome.ok and outcome.returncode != 0
    assert "boom from worker" in outcome.stderr_tail


def test_worker_is_isolated(tmp_path, settings, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/somewhere/evil")
    monkeypatch.setenv("SECRET_TOKEN", "hunter2")
    (tmp_path / "json.py").write_text("raise SystemExit('planted module imported')\n")
    script = write(tmp_path, "import json, os, sys\n"
                             "print(json.dumps({'secret': os.environ.get('SECRET_TOKEN'),"
                             " 'pp': os.environ.get('PYTHONPATH'), 'isolated': sys.flags.isolated}))\n")
    outcome = asyncio.run(run_script(script, tmp_path, settings))
    assert outcome.returncode == 0, outcome.stderr_tail
    assert '"secret": null' in outcome.stdout_tail and '"pp": null' in outcome.stdout_tail
    assert '"isolated": 1' in outcome.stdout_tail
    assert "SECRET_TOKEN" not in worker_env(tmp_path)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_AS is enforced on Linux")
def test_memory_limit(tmp_path, settings):
    settings.memory_limit_mb = 256
    script = write(tmp_path, "x = bytearray(1024 * 1024 * 1024)\n")
    outcome = asyncio.run(run_script(script, tmp_path, settings))
    assert not outcome.ok and "MemoryError" in outcome.stderr_tail
