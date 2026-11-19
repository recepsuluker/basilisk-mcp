"""Run a generated scenario in an isolated, resource-limited worker process.

Isolation measures:

* ``python -I -B``: isolated mode ignores ``PYTHON*`` environment variables, the user
  site-packages directory and the script's own directory on ``sys.path``.
* A minimal environment allow-list; ``HOME`` points at the run directory.
* Its own session / process group, killed as a group on timeout.
* POSIX resource limits: address space, CPU time, file size, no core dumps.
* stdout/stderr go to capped log files inside the run directory.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Settings

log = logging.getLogger(__name__)

ENV_ALLOW = ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "COMSPEC")


@dataclass
class RunOutcome:
    ok: bool
    returncode: int | None
    wall_time_s: float
    timed_out: bool
    stdout_tail: str
    stderr_tail: str


def worker_env(run_dir: Path) -> dict[str, str]:
    env = {k: os.environ[k] for k in ENV_ALLOW if k in os.environ}
    env.setdefault("PATH", os.defpath)
    env.update({
        "HOME": str(run_dir),
        "USERPROFILE": str(run_dir),
        "MPLBACKEND": "Agg",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    })
    if sys.platform == "win32":
        env["OS"] = "Windows_NT"
    return env


def _limits(settings: Settings):
    """Return a preexec function that applies POSIX resource limits in the child."""
    if sys.platform == "win32":
        return None

    import resource

    cpu = int(settings.run_timeout_s) + 5
    mem = settings.memory_limit_mb * 1024 * 1024
    fsize = settings.max_file_size_mb * 1024 * 1024

    def apply() -> None:  # runs in the child between fork and exec
        def setlimit(kind, value):
            try:
                soft, hard = resource.getrlimit(kind)
                if hard != resource.RLIM_INFINITY:
                    value = min(value, hard)
                resource.setrlimit(kind, (value, hard))
            except (ValueError, OSError):
                pass  # some platforms (e.g. macOS RLIMIT_AS) refuse; the timeout still applies

        setlimit(resource.RLIMIT_CORE, 0)
        setlimit(resource.RLIMIT_CPU, cpu)
        if fsize:
            setlimit(resource.RLIMIT_FSIZE, fsize)
        if mem and sys.platform.startswith("linux"):
            setlimit(resource.RLIMIT_AS, mem)

    return apply


def _tail(path: Path, limit: int) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > limit:
                handle.seek(size - limit)
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return ""


async def _kill_group(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        if sys.platform == "win32":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(proc.wait(), 10)
    except TimeoutError:  # pragma: no cover
        log.error("worker %s did not exit after SIGKILL", proc.pid)


async def run_script(script: Path, run_dir: Path, settings: Settings) -> RunOutcome:
    """Execute ``script`` with ``settings.python``; results land in ``run_dir``."""
    stdout_path, stderr_path = run_dir / "stdout.log", run_dir / "stderr.log"
    command = [settings.python, "-I", "-B", str(script), "--output", str(run_dir)]
    kwargs: dict = {"cwd": str(run_dir), "env": worker_env(run_dir), "stdin": asyncio.subprocess.DEVNULL}
    if sys.platform == "win32":
        import subprocess

        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
        kwargs["preexec_fn"] = _limits(settings)

    start = time.perf_counter()
    timed_out = False
    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        proc = await asyncio.create_subprocess_exec(*command, stdout=out, stderr=err, **kwargs)
        try:
            await asyncio.wait_for(proc.wait(), timeout=settings.run_timeout_s)
        except TimeoutError:
            timed_out = True
            await _kill_group(proc)
        except asyncio.CancelledError:
            await _kill_group(proc)
            raise
        finally:
            # A crashed worker may leave children in its group; clean them up.
            if sys.platform != "win32":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
    wall = time.perf_counter() - start
    ok = (not timed_out and proc.returncode == 0 and (run_dir / "results.npz").is_file()
          and (run_dir / "summary.json").is_file())
    return RunOutcome(
        ok=ok,
        returncode=proc.returncode,
        wall_time_s=wall,
        timed_out=timed_out,
        stdout_tail=_tail(stdout_path, settings.max_log_bytes),
        stderr_tail=_tail(stderr_path, settings.max_log_bytes),
    )


async def probe_basilisk(settings: Settings, timeout_s: float = 120.0) -> dict:
    """Check that the worker interpreter can import Basilisk; return version info."""
    code = (
        "import json, platform, sys\n"
        "from Basilisk import __version__ as v\n"
        "from Basilisk.simulation import spacecraft\n"
        "print(json.dumps({'basilisk': v, 'python': platform.python_version(), 'executable': sys.executable}))\n"
    )
    proc = await asyncio.create_subprocess_exec(
        settings.python, "-I", "-B", "-c", code,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL,
        env=worker_env(settings.data_dir),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        proc.kill()
        return {"ok": False, "error": f"timed out after {timeout_s} s importing Basilisk"}
    if proc.returncode != 0:
        return {"ok": False, "error": err.decode("utf-8", "replace")[-2000:]}
    info = json.loads(out.decode().strip().splitlines()[-1])
    info["ok"] = True
    return info
