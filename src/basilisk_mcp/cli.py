"""Command-line interface: serve, doctor, init-config, token, print-config."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import logging
import os
import secrets
import shutil
import sys
import tempfile
from pathlib import Path

from . import __version__
from .config import EXAMPLE_CONFIG, load_settings
from .errors import BskMcpError

TOKEN_ENV = "BASILISK_MCP_TOKEN"  # noqa: S105 - environment variable name, not a secret
MIN_TOKEN_LENGTH = 32


def _setup_logging(level: str) -> None:
    # stdout carries JSON-RPC in stdio mode: log to stderr only.
    logging.basicConfig(level=level, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class BearerAuth:
    """ASGI middleware: constant-time bearer token check for the local HTTP transport."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if not secrets.compare_digest(headers.get("authorization", ""), f"Bearer {self.token}"):
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"text/plain"), (b"www-authenticate", b"Bearer")]})
            await send({"type": "http.response.body", "body": b"Bearer token required"})
            return
        return await self.app(scope, receive, send)


def cmd_serve(args) -> int:
    settings = load_settings(args.config, data_dir=args.data_dir, python=args.python, log_level=args.log_level)
    _setup_logging(settings.log_level)
    from .server import create_server

    server = create_server(settings)
    if args.transport == "stdio":
        server.run("stdio")
        return 0

    host, port = args.host, args.port
    if not _is_loopback(host):
        print(f"Refusing to bind to non-loopback address {host!r}. basilisk-mcp is a single-user local server; "
              "put a reverse proxy with TLS and authentication in front of it if you need remote access.",
              file=sys.stderr)
        return 2
    token = os.environ.get(TOKEN_ENV, "")
    if len(token) < MIN_TOKEN_LENGTH:
        print(f"HTTP transport requires {TOKEN_ENV} with at least {MIN_TOKEN_LENGTH} characters. "
              "Generate one with: basilisk-mcp token", file=sys.stderr)
        return 2

    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    hosts = [f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=[f"http://{h}" for h in hosts],
    )
    app = server.streamable_http_app(transport_security=security, host=host)
    logging.getLogger(__name__).info("Serving MCP on http://%s:%d/mcp (bearer token required)", host, port)
    uvicorn.run(BearerAuth(app, token), host=host, port=port, log_level=settings.log_level.lower())
    return 0


def cmd_doctor(args) -> int:
    settings = load_settings(args.config, data_dir=args.data_dir, python=args.python)
    ok = True

    def report(name: str, passed: bool, detail: str) -> None:
        nonlocal ok
        ok &= passed
        print(f"[{'ok' if passed else 'FAIL'}] {name}: {detail}")

    print(f"basilisk-mcp {__version__}")
    report("server python", sys.version_info >= (3, 11), sys.version.split()[0])
    worker = shutil.which(settings.python) or settings.python
    report("worker python", Path(worker).exists(), worker)

    from .runner import probe_basilisk, run_script

    probe = asyncio.run(probe_basilisk(settings))
    report("basilisk import", probe.get("ok", False),
           f"Basilisk {probe.get('basilisk')} on Python {probe.get('python')}" if probe.get("ok")
           else probe.get("error", "").strip().splitlines()[-1:] or "failed")

    try:
        from .manager import _private_dir

        _private_dir(settings.data_dir)
        probe_file = settings.data_dir / ".write-test"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink()
        mode = oct(settings.data_dir.stat().st_mode & 0o777)
        report("data directory", True, f"{settings.data_dir} (mode {mode})")
    except OSError as exc:
        report("data directory", False, str(exc))

    if probe.get("ok"):
        from .codegen import generate_script
        from .manager import replay
        from .templates import template_spec

        spec = replay(template_spec("basic_orbit"), settings)
        source = generate_script(spec, 600.0)
        from .vizard import download_url, find_vizard

        exe = find_vizard(settings)
        print(f"[{'ok' if exe else 'info'}] vizard: {exe or 'not found (optional, 3-D view). Download: ' + download_url()}")
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "scenario.py").write_text(source, encoding="utf-8")
            outcome = asyncio.run(run_script(run_dir / "scenario.py", run_dir, settings))
            report("smoke simulation", outcome.ok,
                   f"basic_orbit for 600 s in {outcome.wall_time_s:.2f} s" if outcome.ok
                   else outcome.stderr_tail[-500:])
    print("All checks passed." if ok else "Some checks failed.")
    return 0 if ok else 1


def _summarize(manager, session_id: str, run_id: str) -> list[str]:
    """Short human-readable summary of a run for the console."""
    from . import results as R

    session, _, data = manager.run_data(session_id, run_id)
    spec = manager.run_spec(session, run_id)
    lines = []
    for rec, meta in data.recorders().items():
        if meta["msg_type"] == "AttGuidMsg":
            a = R.analyze_attitude(data, rec, 1.0)
            settle = f"{a['settling_time_s']:.0f} s" if a["settled"] else "not settled"
            lines.append(f"  attitude error  {a['initial_error_deg']:.2f} deg -> {a['final_error_deg']:.4f} deg "
                         f"(below 1 deg after {settle})")
        elif meta["msg_type"] == "SCStatesMsg":
            try:
                o = R.analyze_orbit(data, rec, spec)
            except BskMcpError:  # e.g. no gravity attached: nothing to summarize
                continue
            raan = o["elements"]["raan_deg"].get("mean_rate_deg_per_day")
            if o["time_span_s"] < 3 * o["orbital_period_s"]:
                raan = None  # a mean rate from less than three orbits is dominated by short-period terms
            lines.append(f"  orbit           period {o['orbital_period_s'] / 60:.2f} min, altitude "
                         f"{o['periapsis_altitude_km']:.1f}-{o['apoapsis_altitude_km']:.1f} km"
                         + (f", RAAN rate {raan:.4f} deg/day" if raan is not None else ""))
        elif meta["msg_type"] == "RWSpeedMsg":
            rpm = data.get(rec, "wheel_speeds_rpm")
            lines.append(f"  wheels          peak speed {abs(rpm).max():.0f} rpm")
    return lines


def cmd_run(args) -> int:
    """One click: run a template or a saved session, print a summary, open the result in Vizard."""
    settings = load_settings(args.config, data_dir=args.data_dir, python=args.python)
    from .manager import SESSION_ID_RE, Manager, replay
    from .templates import TEMPLATES
    from .vizard import launch

    def finish(code: int) -> int:
        if args.pause:
            try:
                input("\nPress Enter to close...")
            except EOFError:
                pass
        return code

    manager = Manager(settings)
    scenario = args.scenario
    if SESSION_ID_RE.match(scenario):
        # Run a fresh copy of the session's current spec in a dedicated one-click session, so a session that
        # Claude's running server holds in memory is never modified from outside.
        source = manager.get(scenario)
        previous = [r["duration_s"] for r in source.runs.values() if r.get("status") == "completed"]
        duration = args.duration or (previous[-1] if previous else None)
        if duration is None:
            print("This session has no completed run yet; pass --duration SECONDS.", file=sys.stderr)
            return finish(2)
        name = f"one-click {source.id}"
        session = next((s for s in manager.sessions.values() if s.name == name), None)
        if session is None:
            session = manager.create_session(name=name, description=f"One-click copy of '{source.name}'.")
        manager.mutate(session.id, None, lambda spec: spec.update(replay(source.spec, settings)))
    elif scenario in TEMPLATES:
        name = f"one-click {scenario}"
        session = next((s for s in manager.sessions.values() if s.name == name and s.template == scenario), None)
        if session is None:
            session = manager.create_session(name=name, template=scenario,
                                             description="Created by `basilisk-mcp run`.")
        duration = args.duration or TEMPLATES[scenario]["suggested_duration_s"]
    else:
        print(f"Unknown scenario {scenario!r}. Use a template ({', '.join(TEMPLATES)}) "
              "or a session id such as s1a2b3c4d (ask Claude for the id of a session you built).", file=sys.stderr)
        return finish(2)

    vizard = not args.no_vizard
    print(f"Running '{session.name}' ({session.id}) for {duration:.0f} s"
          + (" with Vizard" if vizard else "") + "...")
    record = asyncio.run(manager.run(session.id, float(duration), label="one-click", vizard=vizard))
    print(f"Run {record['run_id']} completed in {record['wall_time_s']:.1f} s (Basilisk {record['basilisk_version']})")
    for line in _summarize(manager, session.id, record["run_id"]):
        print(line)
    if vizard and record.get("vizard_file") and not args.no_open:
        result = launch(settings, manager.vizard_file(session.id, record["run_id"]))
        print(f"Opened Vizard: {result['file']}")
    return finish(0)


def cmd_vizard(args) -> int:
    """Open the latest (or a chosen) run in Vizard: suitable for a desktop shortcut."""
    settings = load_settings(args.config, data_dir=args.data_dir, python=args.python)
    from .manager import Manager
    from .vizard import download_url, find_vizard, launch

    if args.find:
        exe = find_vizard(settings)
        print(exe if exe else f"Vizard not found. Download: {download_url()}")
        return 0 if exe else 1
    manager = Manager(settings)
    if args.session:
        path = manager.vizard_file(args.session, args.run)
    else:
        path = manager.latest_vizard_file()
        if path is None:
            print("No run with a Vizard file yet. Ask Claude to run a simulation with vizard=true.", file=sys.stderr)
            return 1
    result = launch(settings, path)
    print(f"Opened {result['file']} in {result['vizard']}")
    return 0


def claude_config_candidates() -> list[Path]:
    """Where Claude Desktop keeps claude_desktop_config.json on each platform."""
    home = Path.home()
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        found = [appdata / "Claude" / "claude_desktop_config.json"]
        found += sorted(local.glob("Packages/*Claude*/LocalCache/Roaming/Claude/claude_desktop_config.json"))
        return found
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"]
    return [home / ".config" / "Claude" / "claude_desktop_config.json"]


def server_executable() -> str:
    exe = shutil.which("basilisk-mcp")
    if exe:
        return str(Path(exe).resolve())
    scripts = Path(sys.executable).parent
    for name in ("basilisk-mcp.exe", "basilisk-mcp"):
        if (scripts / name).exists():
            return str(scripts / name)
    return str(Path(sys.argv[0]).resolve())


def cmd_install_claude(args) -> int:
    """Add (or update) the basilisk server in Claude Desktop's configuration, keeping everything else."""
    if args.config_file:
        path = Path(args.config_file).expanduser()
    else:
        existing = [p for p in claude_config_candidates() if p.is_file()]
        path = existing[0] if existing else claude_config_candidates()[0]
    data = {}
    if path.is_file():
        text = path.read_text(encoding="utf-8-sig").strip()
        if text:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                print(f"{path} is not valid JSON ({exc}); fix or remove it first.", file=sys.stderr)
                return 1
    if not isinstance(data, dict):
        print(f"{path} does not contain a JSON object.", file=sys.stderr)
        return 1
    serve_args = ["serve"]
    if args.config:
        serve_args = ["--config", str(Path(args.config).expanduser().resolve()), "serve"]
    entry: dict = {"command": server_executable(), "args": serve_args}
    if args.vizard_auto_open:
        entry["env"] = {"BASILISK_MCP_VIZARD_AUTO_OPEN": "true"}
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        print(f"'mcpServers' in {path} is not an object.", file=sys.stderr)
        return 1
    action = "Updated" if "basilisk" in servers else "Added"
    servers["basilisk"] = entry
    if args.dry_run:
        print(json.dumps({"mcpServers": {"basilisk": entry}}, indent=2))
        print(f"(dry run: {path} not changed)")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")  # UTF-8 without BOM
    others = sorted(k for k in servers if k != "basilisk")
    print(f"{action} the 'basilisk' server in {path}")
    if others:
        print(f"Kept your other servers: {', '.join(others)}")
    print("Now quit Claude Desktop completely (tray/menu-bar icon -> Quit) and start it again.")
    return 0


def cmd_init_config(args) -> int:
    path = Path(args.path).expanduser()
    if path.exists() and not args.force:
        print(f"{path} exists; use --force to overwrite.", file=sys.stderr)
        return 1
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    print(f"Wrote {path}")
    return 0


def cmd_token(args) -> int:
    print(secrets.token_urlsafe(32))
    return 0


def cmd_print_config(args) -> int:
    exe = shutil.which("basilisk-mcp") or sys.argv[0]
    serve_args = ["serve"]
    if args.config:
        serve_args = ["--config", str(Path(args.config).expanduser().resolve()), "serve"]
    if args.client == "claude-desktop":
        print(json.dumps({"mcpServers": {"basilisk": {"command": exe, "args": serve_args}}}, indent=2))
    else:
        print("claude mcp add basilisk -- " + " ".join([exe, *serve_args]))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="basilisk-mcp", description="MCP server for the Basilisk astrodynamics framework")
    parser.add_argument("--version", action="version", version=f"basilisk-mcp {__version__}")
    parser.add_argument("--config", help="TOML configuration file")
    parser.add_argument("--data-dir", help="override data_dir")
    parser.add_argument("--python", help="interpreter for simulation workers (must have Basilisk installed)")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the MCP server (default)")
    serve.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--log-level", default=None)
    serve.set_defaults(func=cmd_serve)

    doctor = sub.add_parser("doctor", help="check the installation and run a smoke simulation")
    doctor.set_defaults(func=cmd_doctor)

    init = sub.add_parser("init-config", help="write an example configuration file")
    init.add_argument("path", nargs="?", default="basilisk-mcp.toml")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init_config)

    run = sub.add_parser("run", help="one click: run a template or saved session and open it in Vizard")
    run.add_argument("scenario", help="template name (e.g. attitude_feedback_rw) or session id (e.g. s1a2b3c4d)")
    run.add_argument("--duration", type=float, help="simulated seconds (default: template suggestion / last run)")
    run.add_argument("--no-vizard", action="store_true", help="do not record a Vizard file")
    run.add_argument("--no-open", action="store_true", help="record the Vizard file but do not open Vizard")
    run.add_argument("--pause", action="store_true", help="wait for Enter before closing (for desktop shortcuts)")
    run.set_defaults(func=cmd_run)

    viz = sub.add_parser("vizard", help="open the latest run (or --session/--run) in Vizard")
    viz.add_argument("--session", help="session id (default: newest Vizard file of any session)")
    viz.add_argument("--run", help="run id within --session (default: its latest Vizard run)")
    viz.add_argument("--find", action="store_true", help="only print where Vizard was found")
    viz.set_defaults(func=cmd_vizard)

    inst = sub.add_parser("install-claude", help="add this server to Claude Desktop's configuration")
    inst.add_argument("--vizard-auto-open", action="store_true", help="open Vizard automatically after Vizard runs")
    inst.add_argument("--config-file", help="path to claude_desktop_config.json (default: auto-detect)")
    inst.add_argument("--dry-run", action="store_true", help="print the entry without writing")
    inst.set_defaults(func=cmd_install_claude)

    token = sub.add_parser("token", help="print a random bearer token for the HTTP transport")
    token.set_defaults(func=cmd_token)

    pc = sub.add_parser("print-config", help="print client configuration for Claude Desktop or Claude Code")
    pc.add_argument("client", choices=["claude-desktop", "claude-code"])
    pc.set_defaults(func=cmd_print_config)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args([*(argv or sys.argv[1:]), "serve"])
    try:
        sys.exit(args.func(args))
    except BskMcpError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
