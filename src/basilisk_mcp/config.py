"""Server configuration: defaults < TOML file < environment variables < CLI flags."""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .errors import ValidationError

ENV_PREFIX = "BASILISK_MCP_"


def default_data_dir() -> Path:
    return Path.home() / ".basilisk-mcp"


@dataclass
class Settings:
    """All tunable limits. Every limit exists to bound what a single tool call can cost."""

    #: Where sessions, generated scripts and results are stored.
    data_dir: Path = field(default_factory=default_data_dir)
    #: Interpreter used for simulation workers. It must be able to `import Basilisk`.
    python: str = sys.executable
    #: Maximum number of open sessions.
    max_sessions: int = 16
    #: Maximum modules in one session.
    max_modules: int = 32
    #: Maximum recorders in one session.
    max_recorders: int = 16
    #: Maximum stored runs per session.
    max_runs_per_session: int = 200
    #: Simulations allowed to run at the same time.
    max_concurrent_runs: int = 2
    #: Wall-clock timeout for one run [s].
    run_timeout_s: float = 300.0
    #: Longest simulated time accepted for one run [s] (default: one year).
    max_sim_duration_s: float = 31_557_600.0
    #: Maximum integration steps of one task in one run (duration / dt).
    max_steps: int = 5_000_000
    #: Maximum samples one recorder may store in one run (duration / sample_dt).
    max_recorded_samples: int = 200_000
    #: Maximum points returned by query_results.
    max_query_points: int = 2_000
    #: Address-space limit for worker processes [MiB] (POSIX only, 0 disables).
    memory_limit_mb: int = 4_096
    #: Largest file a worker may write [MiB] (POSIX only, 0 disables).
    max_file_size_mb: int = 512
    #: Bytes of worker stdout/stderr returned to the client on failure.
    max_log_bytes: int = 16_384
    #: Log level for the server's own stderr logging.
    log_level: str = "INFO"

    def validate(self) -> Settings:
        positive_ints = (
            "max_sessions", "max_modules", "max_recorders", "max_runs_per_session",
            "max_concurrent_runs", "max_steps", "max_recorded_samples", "max_query_points",
            "max_log_bytes",
        )
        for name in positive_ints:
            if int(getattr(self, name)) < 1:
                raise ValidationError(f"Setting '{name}' must be >= 1.")
        for name in ("run_timeout_s", "max_sim_duration_s"):
            if float(getattr(self, name)) <= 0:
                raise ValidationError(f"Setting '{name}' must be > 0.")
        if self.memory_limit_mb < 0 or self.max_file_size_mb < 0:
            raise ValidationError("memory_limit_mb and max_file_size_mb must be >= 0.")
        if self.log_level.upper() not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValidationError("log_level must be DEBUG, INFO, WARNING, ERROR or CRITICAL.")
        self.log_level = self.log_level.upper()
        self.data_dir = Path(self.data_dir).expanduser().resolve()
        return self

    def public(self) -> dict:
        out = asdict(self)
        out["data_dir"] = str(self.data_dir)
        return out


def _coerce(name: str, raw, current):
    if isinstance(current, bool):
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, Path):
        return Path(str(raw)).expanduser()
    return str(raw)


def load_settings(config_path: str | os.PathLike | None = None, **overrides) -> Settings:
    settings = Settings()
    known = {f.name for f in fields(Settings)}

    if config_path:
        path = Path(config_path).expanduser()
        if not path.is_file():
            raise ValidationError(f"Config file not found: {path}")
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        section = data.get("server", data)
        for key, value in section.items():
            if key not in known:
                raise ValidationError(f"Unknown config key '{key}' in {path}.", f"Known keys: {sorted(known)}")
            setattr(settings, key, _coerce(key, value, getattr(settings, key)))

    for key in known:
        env_value = os.environ.get(ENV_PREFIX + key.upper())
        if env_value is not None and env_value != "":
            setattr(settings, key, _coerce(key, env_value, getattr(settings, key)))

    for key, value in overrides.items():
        if value is not None:
            if key not in known:
                raise ValidationError(f"Unknown setting '{key}'.")
            setattr(settings, key, _coerce(key, value, getattr(settings, key)))

    return settings.validate()


EXAMPLE_CONFIG = """\
# basilisk-mcp configuration. All keys are optional; values shown are the defaults.
# Precedence: defaults < this file < BASILISK_MCP_* environment variables < CLI flags.

[server]
# Where sessions, generated scripts, results and plots are stored (created with 0700 permissions).
# data_dir = "~/.basilisk-mcp"

# Python interpreter used for simulation workers. It must have Basilisk installed (pip install bsk).
# Defaults to the interpreter running the server.
# python = "/path/to/venv/bin/python"

max_sessions = 16
max_modules = 32
max_recorders = 16
max_runs_per_session = 200
max_concurrent_runs = 2
run_timeout_s = 300.0            # wall-clock seconds per run
max_sim_duration_s = 31557600.0  # simulated seconds per run (one year)
max_steps = 5000000              # integration steps per task per run
max_recorded_samples = 200000    # samples per recorder per run
max_query_points = 2000          # points returned by query_results
memory_limit_mb = 4096           # worker address-space limit (POSIX), 0 disables
max_file_size_mb = 512           # largest file a worker may write (POSIX), 0 disables
max_log_bytes = 16384
log_level = "INFO"
"""
