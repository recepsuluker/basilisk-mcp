"""MCP surface: tools, prompts and resources exposed to the client."""

from __future__ import annotations

import inspect
import json
import logging
import time
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ImageContent, TextContent, ToolAnnotations

from . import __version__
from . import results as R
from . import spec as S
from .config import Settings
from .errors import BskMcpError, LimitExceeded, ValidationError
from .manager import Manager
from .registry import MODULE_TYPES, catalog, get_module_type
from .runner import probe_basilisk
from .templates import list_templates as template_catalog
from .vizard import find_vizard
from .vizard import launch as launch_vizard

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
This server builds and runs Basilisk astrodynamics simulations (https://github.com/AVSLab/basilisk).

Typical workflow:
1. list_templates / list_module_types to see what exists. describe_module_type gives parameters, units and ports.
2. create_session (optionally from a template such as 'basic_orbit' or 'attitude_feedback_rw').
3. Edit: add_module, set_params, connect '<module>.<output>' -> '<module>.<input>', add_recorder.
4. validate_session, then run_simulation(duration_s).
5. analyze_run ('orbit', 'attitude', 'actuators'), query_results, plot_results, compare_runs.
6. export_script returns the exact standalone Basilisk Python script that was run.
7. For a 3-D view, run_simulation(..., vizard=true) and then open_in_vizard to launch Basilisk's Vizard app.

Conventions: SI units with the unit in every parameter name (mass_kg, inclination_deg, torque_B_Nm);
attitudes are MRPs; frames: _B body, _N inertial. Errors include a hint describing a valid call.
No arbitrary code is executed: only catalogued modules with validated parameters.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
EDIT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False,
                              open_world_hint=False)
RUN = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)


def create_server(settings: Settings, manager: Manager | None = None) -> MCPServer:
    manager = manager or Manager(settings)
    started = time.time()
    probe_cache: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(server):
        yield {}

    server = MCPServer(
        "basilisk",
        title="Basilisk astrodynamics",
        version=__version__,
        instructions=INSTRUCTIONS,
        website_url="https://github.com/recepsuluker/basilisk-mcp",
        lifespan=lifespan,
        log_level=settings.log_level,
    )

    def tool(annotations: ToolAnnotations):
        """Register a tool and convert internal errors into structured MCP tool errors."""

        def decorate(function):
            @wraps(function)
            async def wrapped(*args, **kwargs):
                try:
                    result = function(*args, **kwargs)
                    return await result if inspect.isawaitable(result) else result
                except BskMcpError as exc:
                    raise ToolError(json.dumps(exc.as_dict())) from exc
                except (ValueError, TypeError, KeyError) as exc:
                    log.debug("invalid argument", exc_info=True)
                    raise ToolError(json.dumps({"code": "INVALID_ARGUMENT", "message": str(exc)})) from exc

            return server.tool(annotations=annotations)(wrapped)

        return decorate

    def edited(session, result=None) -> dict[str, Any]:
        out = {"session_id": session.id, "version": session.version}
        if result is not None:
            out["result"] = result
        return out

    def clamp_points(max_points: int) -> int:
        if max_points < 2:
            raise ValidationError("max_points must be >= 2.")
        return min(max_points, settings.max_query_points)

    # ---------------------------------------------------------------- discovery

    @tool(READ_ONLY)
    async def server_info(check_basilisk: bool = True) -> dict[str, Any]:
        """Server version, limits, and (optionally) whether the worker interpreter can import Basilisk."""
        info: dict[str, Any] = {
            "server": "basilisk-mcp",
            "version": __version__,
            "uptime_s": round(time.time() - started, 1),
            "open_sessions": len(manager.sessions),
            "limits": {k: v for k, v in settings.public().items() if k.startswith("max_") or k.endswith("_s")},
            "data_dir": str(settings.data_dir),
            "vizard": str(find_vizard(settings) or "not found"),
        }
        if check_basilisk:
            if "result" not in probe_cache:
                probe_cache["result"] = await probe_basilisk(settings)
            info["basilisk"] = probe_cache["result"]
        return info

    @tool(READ_ONLY)
    def list_module_types() -> list[dict[str, Any]]:
        """List the catalogued Basilisk module types with their input/output message ports."""
        return catalog()

    @tool(READ_ONLY)
    def describe_module_type(module_type: str) -> dict[str, Any]:
        """Full description of a module type: parameters (kind, unit, default, bounds), ports and recordable variables."""
        return get_module_type(module_type).describe()

    @tool(READ_ONLY)
    def list_templates() -> list[dict[str, Any]]:
        """List ready-made scenarios (mirroring official Basilisk examples) usable with create_session(template=...)."""
        return template_catalog()

    # ---------------------------------------------------------------- sessions

    @tool(EDIT)
    def create_session(
        name: str | None = None,
        template: str | None = None,
        description: str | None = None,
        overrides: dict[str, dict[str, Any]] | None = None,
        simulation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a simulation session, empty or from a template.

        overrides: per-module parameter changes applied to the template, e.g.
          {"sc": {"orbit": {"altitude_km": 700, "inclination_deg": 98}}, "earth": {"j2": true}}
        simulation: {"dt_s": 0.1, "fsw_dt_s": 0.5, "integrator": "rk4" | "rkf45" | "rk2" | "euler" | "rkf78"}
        """
        session = manager.create_session(name, description, template, overrides, simulation)
        out = session.info(include_spec=True)
        out["validation_problems"] = manager.validate(session)
        if template:
            out["suggested_duration_s"] = round(manager.templates()[template]["suggested_duration_s"], 3)
        return out

    @tool(READ_ONLY)
    def list_sessions() -> list[dict[str, Any]]:
        """List open sessions."""
        return [s.info() for s in manager.sessions.values()]

    @tool(READ_ONLY)
    def get_session(session_id: str) -> dict[str, Any]:
        """Full session spec (modules, parameters, connections, recorders), version, and runs."""
        session = manager.get(session_id)
        out = session.info(include_spec=True)
        out["runs"] = list(session.runs.values())
        return out

    @tool(DESTRUCTIVE)
    def close_session(session_id: str, delete_files: bool = False) -> dict[str, Any]:
        """Close a session. Files (scripts, results) are kept unless delete_files is true."""
        return manager.close_session(session_id, delete_files)

    # ---------------------------------------------------------------- editing

    @tool(EDIT)
    def add_module(session_id: str, name: str, module_type: str, params: dict[str, Any] | None = None,
                   expected_version: int | None = None) -> dict[str, Any]:
        """Add a catalogued module. Missing parameters take their defaults (see describe_module_type).

        Example: add_module(session_id, "rw", "reaction_wheels", {"attach_to": "sc", "max_momentum_Nms": 50})
        """
        session, result = manager.mutate(
            session_id, expected_version, lambda sp: S.add_module(sp, name, module_type, params, settings))
        return edited(session, {"name": name, **result})

    @tool(EDIT)
    def set_params(session_id: str, name: str, params: dict[str, Any],
                   expected_version: int | None = None) -> dict[str, Any]:
        """Change some parameters of an existing module; others keep their current values.

        For 'orbit' give the complete orbit object (one of semi_major_axis_km / altitude_km plus elements).
        """
        session, result = manager.mutate(session_id, expected_version, lambda sp: S.set_params(sp, name, params))
        return edited(session, {"name": name, **result})

    @tool(EDIT)
    def remove_module(session_id: str, name: str, expected_version: int | None = None) -> dict[str, Any]:
        """Remove a module together with its connections and recorders."""
        session, result = manager.mutate(session_id, expected_version, lambda sp: S.remove_module(sp, name))
        return edited(session, result)

    @tool(EDIT)
    def connect(session_id: str, source: str, target: str, expected_version: int | None = None) -> dict[str, Any]:
        """Connect an output port to an input port with the same message type.

        source: '<module>.<output port>', target: '<module>.<input port>', e.g. connect(sid, "sc.state", "nav.state").
        """
        session, result = manager.mutate(session_id, expected_version, lambda sp: S.connect(sp, source, target))
        return edited(session, result)

    @tool(EDIT)
    def disconnect(session_id: str, target: str, expected_version: int | None = None) -> dict[str, Any]:
        """Remove the connection feeding the input port 'target' ('<module>.<input port>')."""
        session, result = manager.mutate(session_id, expected_version, lambda sp: S.disconnect(sp, target))
        return edited(session, result)

    @tool(EDIT)
    def add_recorder(session_id: str, name: str, source: str, sample_dt_s: float,
                     expected_version: int | None = None) -> dict[str, Any]:
        """Record an output port every sample_dt_s seconds (an integer multiple of its task step).

        Example: add_recorder(sid, "states", "sc.state", 10.0)
        """
        session, result = manager.mutate(
            session_id, expected_version, lambda sp: S.add_recorder(sp, name, source, sample_dt_s, settings))
        return edited(session, {"name": name, **result})

    @tool(EDIT)
    def remove_recorder(session_id: str, name: str, expected_version: int | None = None) -> dict[str, Any]:
        """Remove a recorder."""
        session, _ = manager.mutate(session_id, expected_version, lambda sp: S.remove_recorder(sp, name))
        return edited(session)

    @tool(EDIT)
    def set_simulation(session_id: str, dt_s: float | None = None, fsw_dt_s: float | None = None,
                       integrator: Literal["rk4", "rk2", "euler", "rkf45", "rkf78"] | None = None,
                       reset_fsw_dt: bool = False, expected_version: int | None = None) -> dict[str, Any]:
        """Set the dynamics step dt_s, the flight-software period fsw_dt_s (reset_fsw_dt makes it follow dt_s) and the integrator."""
        session, result = manager.mutate(
            session_id, expected_version,
            lambda sp: S.set_simulation(sp, dt_s, fsw_dt_s, integrator, clear_fsw_dt=reset_fsw_dt))
        return edited(session, result)

    # ---------------------------------------------------------------- checking

    @tool(READ_ONLY)
    def validate_session(session_id: str, duration_s: float | None = None) -> dict[str, Any]:
        """Check the whole session (references, required connections, rates, limits for duration_s)."""
        session = manager.get(session_id)
        problems = manager.validate(session, duration_s)
        return {"session_id": session.id, "version": session.version, "valid": not problems, "problems": problems}

    @tool(READ_ONLY)
    def export_script(session_id: str, duration_s: float, vizard: bool = False) -> dict[str, Any]:
        """Return the standalone Basilisk Python script for this session (exactly what run_simulation executes).

        vizard=true includes the lines that write a Vizard 3-D visualization file.
        """
        session = manager.get(session_id)
        source = manager.script(session, duration_s, vizard=vizard)
        path = manager.session_dir(session) / f"scenario_v{session.version}.py"
        path.write_text(source, encoding="utf-8")
        return {"session_id": session.id, "version": session.version, "path": str(path),
                "lines": source.count("\n") + 1, "script": source}

    # ---------------------------------------------------------------- running

    @tool(RUN)
    async def run_simulation(session_id: str, duration_s: float, label: str | None = None,
                             vizard: bool = False) -> dict[str, Any]:
        """Validate, generate and run the simulation for duration_s simulated seconds in an isolated worker.

        vizard=true also records a Vizard 3-D visualization file (vizard.bin); open it with open_in_vizard.
        Returns the run record and a compact overview (initial/final/min/max) of every recorded variable.
        """
        record = await manager.run(session_id, duration_s, label, vizard=vizard)
        _, _, data = manager.run_data(session_id, record["run_id"])
        out = {"session_id": session_id, "run": record, "overview": R.run_overview(data)}
        if vizard and record.get("vizard_file") and settings.vizard_auto_open:
            try:
                out["vizard"] = launch_vizard(settings, manager.vizard_file(session_id, record["run_id"]))
            except BskMcpError as exc:
                out["vizard"] = exc.as_dict()
        return out

    @tool(RUN)
    def open_in_vizard(session_id: str, run_id: str | None = None) -> dict[str, Any]:
        """Open a run's 3-D visualization in Basilisk's Vizard app on this computer (default: latest Vizard run).

        The run must have been made with run_simulation(..., vizard=true).
        """
        return launch_vizard(settings, manager.vizard_file(session_id, run_id))

    @tool(READ_ONLY)
    def list_runs(session_id: str) -> list[dict[str, Any]]:
        """List the runs of a session."""
        return list(manager.get(session_id).runs.values())

    @tool(READ_ONLY)
    def get_run(session_id: str, run_id: str) -> dict[str, Any]:
        """Run record, recorded variables with units, and an overview of the results."""
        session, run_id, data = manager.run_data(session_id, run_id)
        return {"run": session.runs[run_id],
                "recorders": {r: data.variables(r) for r in data.recorders()},
                "overview": R.run_overview(data)}

    # ---------------------------------------------------------------- results

    @tool(READ_ONLY)
    def query_results(session_id: str, recorder: str, run_id: str | None = None,
                      variables: list[str] | None = None, max_points: int = 200,
                      t_start_s: float | None = None, t_end_s: float | None = None) -> dict[str, Any]:
        """Down-sampled time series of recorded (or derived) variables. run_id defaults to the latest run.

        Derived variables include attitude_error_deg, radius_km, speed_kmps, wheel_speeds_rpm, torque_norm_Nm.
        """
        _, rid, data = manager.run_data(session_id, run_id)
        out = R.query(data, recorder, variables, clamp_points(max_points), t_start_s, t_end_s)
        out["run_id"] = rid
        return out

    @tool(READ_ONLY)
    def analyze_run(session_id: str, analysis: Literal["orbit", "attitude", "actuators"],
                    recorder: str | None = None, run_id: str | None = None,
                    threshold_deg: float = 1.0) -> dict[str, Any]:
        """Physics analysis of a run.

        orbit: osculating elements, drift and mean rates (with J2 analytic rates) from an SCStatesMsg recorder.
        attitude: error, settling time below threshold_deg, final rate error from an AttGuidMsg recorder.
        actuators: wheel speed vs. limit, motor torque peaks/RMS, commanded torque and impulse.
        """
        session, rid, data = manager.run_data(session_id, run_id)
        spec = manager.run_spec(session, rid)
        wanted = {"orbit": ("SCStatesMsg", "NavTransMsg"), "attitude": ("AttGuidMsg",)}.get(analysis)
        if analysis != "actuators" and recorder is None:
            candidates = [r for r, m in data.recorders().items() if m["msg_type"] in wanted]
            if not candidates:
                raise ValidationError(f"No recorder of type {wanted} in run {rid}.")
            recorder = candidates[0]
        if analysis == "orbit":
            result = R.analyze_orbit(data, recorder, spec)
        elif analysis == "attitude":
            if not (0 < threshold_deg <= 180):
                raise ValidationError("threshold_deg must be in (0, 180].")
            result = R.analyze_attitude(data, recorder, threshold_deg)
        else:
            result = R.analyze_actuators(data, spec)
        return {"session_id": session.id, "run_id": rid, "analysis": analysis, "result": result}

    @tool(READ_ONLY)
    def plot_results(session_id: str, series: list[str], run_ids: list[str] | None = None,
                     title: str | None = None,
                     time_unit: Literal["s", "min", "h", "day"] = "s") -> list[ImageContent | TextContent]:
        """Plot one or more series ('recorder.variable' or 'recorder.variable[i]') as a PNG.

        Several run_ids overlay the same series for comparison. Series with different units get separate panels.
        """
        if not series or len(series) > 8:
            raise ValidationError("Give between 1 and 8 series.")
        session = manager.get(session_id)
        runs = _load_runs(session, run_ids)
        directory = manager.run_dir(session, next(iter(runs))) / "plots"
        directory.mkdir(exist_ok=True)
        path = directory / f"plot_{int(time.time() * 1000)}.png"
        png = R.plot(runs, series, title, path, time_unit)
        import base64

        return [
            ImageContent(type="image", data=base64.b64encode(png).decode(), mime_type="image/png"),
            TextContent(type="text", text=json.dumps({"saved_to": str(path), "series": series,
                                                     "runs": list(runs)})),
        ]

    def _load_runs(session, run_ids):
        if not run_ids:
            _, rid, data = manager.run_data(session.id, None)
            return {rid: data}
        if len(run_ids) > 6:
            raise LimitExceeded("Compare at most 6 runs at once.")
        out = {}
        for rid in run_ids:
            _, rid2, data = manager.run_data(session.id, rid)
            out[rid2] = data
        return out

    @tool(READ_ONLY)
    def compare_runs(session_id: str, run_ids: list[str], series: str) -> dict[str, Any]:
        """Compare one series across runs (the first run is the baseline): finals, peaks, RMS/max differences."""
        if len(run_ids) < 2:
            raise ValidationError("Give at least two run_ids.")
        session = manager.get(session_id)
        return R.compare(_load_runs(session, run_ids), series)

    @tool(READ_ONLY)
    def export_results(session_id: str, recorder: str, run_id: str | None = None) -> dict[str, Any]:
        """Write a recorder's full-resolution data to CSV inside the server data directory and return the path."""
        session, rid, data = manager.run_data(session_id, run_id)
        data.recorder(recorder)
        directory = manager.run_dir(session, rid) / "exports"
        directory.mkdir(exist_ok=True)
        out = R.export_csv(data, recorder, directory / f"{recorder}.csv")
        out["run_id"] = rid
        return out

    # ---------------------------------------------------------------- resources

    @server.resource("basilisk://modules", name="module-catalog", mime_type="application/json",
                     description="Full catalog of module types with parameters and ports.")
    def module_catalog() -> str:
        return json.dumps({name: mt.describe() for name, mt in MODULE_TYPES.items()}, indent=2)

    @server.resource("basilisk://sessions/{session_id}/spec", name="session-spec", mime_type="application/json",
                     description="Current spec of a session.")
    def session_spec(session_id: str) -> str:
        return json.dumps(manager.get(session_id).info(include_spec=True), indent=2)

    # ---------------------------------------------------------------- prompts

    @server.prompt(title="Orbit propagation study")
    def orbit_study(orbit: str = "500 km sun-synchronous", duration: str = "one day") -> str:
        """Guided workflow: propagate an orbit with and without J2 and compare the element drift."""
        return (
            f"Using the Basilisk MCP tools, study a {orbit} orbit over {duration}.\n"
            "1. create_session from the 'basic_orbit' template with overrides for the requested orbit "
            "(use altitude_km or semi_major_axis_km); pick a recorder sample_dt_s that keeps samples reasonable.\n"
            "2. Run once with point-mass gravity, then set_params on the gravity module with j2=true and run again.\n"
            "3. analyze_run(analysis='orbit') for both runs; compare RAAN and argument-of-periapsis rates with the "
            "J2 analytic rates.\n"
            "4. plot_results for 'states.radius_km' and compare_runs for 'states.r_BN_N'.\n"
            "5. Summarize the physics and give export_script output if the user wants to reproduce it."
        )

    @server.prompt(title="Attitude controller study")
    def attitude_control_study(goal: str = "settle within 1 degree in under 5 minutes") -> str:
        """Guided workflow: run the reaction-wheel attitude scenario and tune the MRP feedback gains."""
        return (
            f"Using the Basilisk MCP tools, tune an attitude controller so that it can {goal}.\n"
            "1. create_session from 'attitude_feedback_rw' and run it for its suggested duration.\n"
            "2. analyze_run(analysis='attitude') and analyze_run(analysis='actuators').\n"
            "3. Adjust control gains K and P with set_params (keep the wheels below their speed limit), rerun, "
            "and use compare_runs on 'att_error_log.attitude_error_deg'.\n"
            "4. plot_results with the error and 'rw_speed_log.wheel_speeds_rpm' for the best run.\n"
            "5. Report the gains, settling time, peak wheel speed and torque, and the trade-offs."
        )

    return server
