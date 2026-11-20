"""Sessions, versioned edits, persistence, and simulation runs."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import secrets
import shutil
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import spec as S
from .codegen import generate_script, vizard_period
from .config import Settings
from .errors import BskMcpError, Conflict, LimitExceeded, NotFound, RunFailed, SpecInvalid, ValidationError
from .results import RunData
from .runner import run_script
from .templates import TEMPLATES, template_spec

log = logging.getLogger(__name__)

SESSION_ID_RE = re.compile(r"^s[0-9a-f]{8}$")
RUN_ID_RE = re.compile(r"^r[0-9]{3,6}$")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:  # pragma: no cover - e.g. some Windows filesystems
        pass
    return path


@dataclass
class Session:
    id: str
    name: str
    description: str
    created: str
    spec: dict[str, Any]
    version: int = 1
    template: str | None = None
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def info(self, include_spec: bool = False) -> dict[str, Any]:
        out = {
            "session": self.id,
            "name": self.name,
            "description": self.description,
            "created": self.created,
            "template": self.template,
            "version": self.version,
            "modules": {n: m["type"] for n, m in self.spec["modules"].items()},
            "recorders": sorted(self.spec["recorders"]),
            "runs": len(self.runs),
        }
        if include_spec:
            out["spec"] = copy.deepcopy(self.spec)
        return out

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "description": self.description, "created": self.created,
                "template": self.template, "version": self.version, "spec": self.spec, "runs": self.runs}


def replay(raw: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Rebuild a spec through the validating edit operations (never trust stored JSON blindly)."""
    spec = S.new_spec()
    sim = raw.get("simulation", {}) or {}
    S.set_simulation(spec, dt_s=sim.get("dt_s"), fsw_dt_s=sim.get("fsw_dt_s"), integrator=sim.get("integrator"))
    for name, mod in (raw.get("modules") or {}).items():
        S.add_module(spec, name, mod.get("type"), mod.get("params"), settings)
    for conn in raw.get("connections") or []:
        S.connect(spec, conn.get("source"), conn.get("target"))
    for name, rec in (raw.get("recorders") or {}).items():
        S.add_recorder(spec, name, rec.get("source"), rec.get("sample_dt_s"), settings)
    return spec


class Manager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.sessions: dict[str, Session] = {}
        self.root = _private_dir(settings.data_dir)
        self.sessions_dir = _private_dir(self.root / "sessions")
        self._run_slots = asyncio.Semaphore(settings.max_concurrent_runs)
        self._locks: dict[str, asyncio.Lock] = {}
        self._cache: OrderedDict[tuple[str, str], RunData] = OrderedDict()
        self._load()

    # ---- persistence ------------------------------------------------------------------

    def _load(self) -> None:
        for path in sorted(self.sessions_dir.glob("s*/session.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not SESSION_ID_RE.match(raw.get("id", "")) or raw["id"] != path.parent.name:
                    raise ValidationError("bad session id")
                session = Session(
                    id=raw["id"], name=str(raw.get("name", ""))[:80], description=str(raw.get("description", ""))[:500],
                    created=str(raw.get("created", "")), spec=replay(raw["spec"], self.settings),
                    version=int(raw.get("version", 1)), template=raw.get("template"),
                    runs={k: v for k, v in (raw.get("runs") or {}).items() if RUN_ID_RE.match(k)},
                )
                self.sessions[session.id] = session
            except (BskMcpError, KeyError, ValueError, TypeError, OSError) as exc:
                log.warning("Skipping unreadable session %s: %s", path.parent.name, exc)
        if self.sessions:
            log.info("Loaded %d stored session(s) from %s", len(self.sessions), self.sessions_dir)

    def _save(self, session: Session) -> None:
        directory = _private_dir(self.sessions_dir / session.id)
        _write_json(directory / "session.json", session.to_json())

    def session_dir(self, session: Session) -> Path:
        return _private_dir(self.sessions_dir / session.id)

    # ---- sessions -----------------------------------------------------------------------

    def get(self, session_id: Any) -> Session:
        if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
            raise ValidationError(f"Invalid session_id {session_id!r}.", "Session ids look like 's1a2b3c4d'.")
        if session_id not in self.sessions:
            raise NotFound("Session", session_id, sorted(self.sessions))
        return self.sessions[session_id]

    def create_session(self, name: str | None = None, description: str | None = None,
                       template: str | None = None, overrides: dict[str, dict[str, Any]] | None = None,
                       simulation: dict[str, Any] | None = None) -> Session:
        if len(self.sessions) >= self.settings.max_sessions:
            raise LimitExceeded(f"At most {self.settings.max_sessions} sessions may be open.",
                                "Close unused sessions with close_session.")
        name = (name or template or "session").strip()[:80]
        description = (description or "").strip()[:500]
        if template is not None:
            raw = template_spec(template)
            spec = replay(raw, self.settings)
        else:
            spec = S.new_spec()
        if simulation:
            if not isinstance(simulation, dict):
                raise ValidationError("simulation must be an object with dt_s, fsw_dt_s and/or integrator.")
            unknown = set(simulation) - {"dt_s", "fsw_dt_s", "integrator"}
            if unknown:
                raise ValidationError(f"Unknown simulation setting(s) {sorted(unknown)}.")
            S.set_simulation(spec, **simulation)
        for module, params in (overrides or {}).items():
            S.set_params(spec, module, params)
        session_id = "s" + secrets.token_hex(4)
        while session_id in self.sessions:  # pragma: no cover
            session_id = "s" + secrets.token_hex(4)
        session = Session(id=session_id, name=name, description=description, created=_now(), spec=spec,
                          template=template)
        self.sessions[session_id] = session
        self._save(session)
        return session

    def mutate(self, session_id: str, expected_version: int | None,
               edit: Callable[[dict[str, Any]], Any]) -> tuple[Session, Any]:
        """Apply ``edit`` to a copy of the spec; commit only if it succeeds."""
        session = self.get(session_id)
        if expected_version is not None and expected_version != session.version:
            raise Conflict(f"Session is at version {session.version}, not {expected_version}.",
                           "Re-read the session with get_session and retry.")
        draft = copy.deepcopy(session.spec)
        result = edit(draft)
        session.spec = draft
        session.version += 1
        self._save(session)
        return session, result

    def close_session(self, session_id: str, delete_files: bool = False) -> dict[str, Any]:
        session = self.get(session_id)
        del self.sessions[session_id]
        self._cache = OrderedDict((k, v) for k, v in self._cache.items() if k[0] != session_id)
        directory = self.sessions_dir / session.id
        if delete_files:
            shutil.rmtree(directory, ignore_errors=True)
        else:
            # Keep files but stop reloading the session on the next start.
            (directory / "session.json").rename(directory / "session.closed.json")
        return {"closed": session_id, "files_deleted": delete_files,
                "files": None if delete_files else str(directory)}

    # ---- scripts and runs ---------------------------------------------------------------

    def validate(self, session: Session, duration_s: float | None = None) -> list[str]:
        return S.validate_spec(session.spec, self.settings, duration_s)

    def script(self, session: Session, duration_s: float, vizard: bool = False) -> str:
        problems = self.validate(session, duration_s)
        if problems:
            raise SpecInvalid(problems)
        vizard_dt = vizard_period(session.spec, duration_s, self.settings.max_vizard_frames) if vizard else None
        return generate_script(session.spec, duration_s, session_id=session.id, spec_version=session.version,
                               vizard=vizard, vizard_dt_s=vizard_dt)

    def _lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    async def run(self, session_id: str, duration_s: float, label: str | None = None,
                  vizard: bool = False) -> dict[str, Any]:
        session = self.get(session_id)
        if len(session.runs) >= self.settings.max_runs_per_session:
            raise LimitExceeded(f"A session may hold {self.settings.max_runs_per_session} runs.",
                                "Create a new session (templates make this quick).")
        spec_snapshot = copy.deepcopy(session.spec)
        version = session.version
        source = self.script(session, duration_s, vizard=vizard)
        async with self._lock(session_id):
            # Skip ids already used in memory *or* on disk (another process may share the data directory).
            runs_root = self.session_dir(session) / "runs"
            number = len(session.runs) + 1
            while f"r{number:03d}" in session.runs or (runs_root / f"r{number:03d}").exists():
                number += 1
            run_id = f"r{number:03d}"
            run_dir = _private_dir(self.session_dir(session) / "runs" / run_id)
            (run_dir / "scenario.py").write_text(source, encoding="utf-8")
            _write_json(run_dir / "spec.json", spec_snapshot)
            record = {"run_id": run_id, "label": (label or "").strip()[:80], "status": "running",
                      "duration_s": duration_s, "spec_version": version, "created": _now()}
            session.runs[run_id] = record
            self._save(session)
        async with self._run_slots:
            outcome = await run_script(run_dir / "scenario.py", run_dir, self.settings)
        record["wall_time_s"] = round(outcome.wall_time_s, 3)
        if not outcome.ok:
            record["status"] = "timeout" if outcome.timed_out else "failed"
            record["returncode"] = outcome.returncode
            self._save(session)
            reason = (f"timed out after {self.settings.run_timeout_s} s" if outcome.timed_out
                      else f"worker exited with code {outcome.returncode}")
            raise RunFailed(
                f"Run {run_id} failed: {reason}.",
                "Shorten duration_s, increase dt_s, or check the parameters; the stderr tail is attached.",
                run_id=run_id, stderr_tail=outcome.stderr_tail[-4000:], stdout_tail=outcome.stdout_tail[-1000:],
            )
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        record["status"] = "completed"
        record["basilisk_version"] = summary.get("basilisk_version")
        record["simulation_wall_time_s"] = round(float(summary.get("wall_time_s", 0.0)), 3)
        record["recorders"] = {k: v["samples"] for k, v in summary["recorders"].items()}
        if vizard:
            vizard_file = run_dir / "vizard.bin"
            record["vizard_file"] = str(vizard_file) if vizard_file.is_file() else None
        self._save(session)
        return record

    def run_record(self, session: Session, run_id: Any) -> dict[str, Any]:
        if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
            raise ValidationError(f"Invalid run_id {run_id!r}.", "Run ids look like 'r001'.")
        if run_id not in session.runs:
            raise NotFound("Run", run_id, sorted(session.runs))
        return session.runs[run_id]

    def run_dir(self, session: Session, run_id: str) -> Path:
        self.run_record(session, run_id)
        return self.sessions_dir / session.id / "runs" / run_id

    def run_data(self, session_id: str, run_id: str | None = None) -> tuple[Session, str, RunData]:
        session = self.get(session_id)
        if run_id is None:
            done = [r for r, rec in session.runs.items() if rec.get("status") == "completed"]
            if not done:
                raise NotFound("Completed run in session", session_id)
            run_id = done[-1]
        record = self.run_record(session, run_id)
        if record.get("status") != "completed":
            raise ValidationError(f"Run {run_id} has status '{record.get('status')}' and has no results.")
        key = (session.id, run_id)
        if key in self._cache:
            self._cache.move_to_end(key)
            return session, run_id, self._cache[key]
        data = RunData(self.run_dir(session, run_id))
        self._cache[key] = data
        while len(self._cache) > 8:
            self._cache.popitem(last=False)
        return session, run_id, data

    def run_spec(self, session: Session, run_id: str) -> dict[str, Any]:
        path = self.run_dir(session, run_id) / "spec.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else session.spec

    def vizard_file(self, session_id: str, run_id: str | None = None) -> Path:
        """The vizard.bin of a run (default: the latest run of the session that has one)."""
        session = self.get(session_id)
        if run_id is None:
            with_file = [r for r, rec in session.runs.items() if rec.get("vizard_file")]
            if not with_file:
                raise NotFound("Run with a Vizard file in session", session_id)
            run_id = with_file[-1]
        path = self.run_dir(session, run_id) / "vizard.bin"
        if not path.is_file():
            raise ValidationError(f"Run {run_id} has no Vizard file.",
                                  "Run the simulation again with run_simulation(..., vizard=true).")
        return path

    def latest_vizard_file(self) -> Path | None:
        files = sorted(self.sessions_dir.glob("s*/runs/r*/vizard.bin"), key=lambda p: p.stat().st_mtime)
        return files[-1] if files else None

    @staticmethod
    def templates() -> dict[str, Any]:
        return TEMPLATES
