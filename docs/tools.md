# Tool reference

All tools return JSON (`structuredContent`) unless noted. Errors are tool results with
`isError: true` and a JSON body `{"code", "message", "hint"?, "details"?}`.

Error codes: `INVALID_ARGUMENT`, `NOT_FOUND`, `CONFLICT`, `LIMIT_EXCEEDED`, `VIZARD_NOT_FOUND`,
`SPEC_INVALID` (with the full `problems` list), `RUN_FAILED` (with the worker's
stderr tail).

Mutating tools accept an optional `expected_version`; if the session has changed
since you read it, the call fails with `CONFLICT` instead of overwriting.

## Discovery (read-only)

| Tool | Arguments | Returns |
|---|---|---|
| `server_info` | `check_basilisk=true` | version, limits, data directory, Basilisk version in the worker interpreter |
| `list_module_types` | – | module types with their input/output ports |
| `describe_module_type` | `module_type` | parameters (kind, unit, default, bounds), ports, recordable variables |
| `list_templates` | – | ready-made scenarios with their Basilisk example and suggested duration |

## Sessions

| Tool | Arguments | Notes |
|---|---|---|
| `create_session` | `name?`, `template?`, `description?`, `overrides?`, `simulation?` | `overrides = {"<module>": {<params>}}`; returns the spec, validation problems and the template's suggested duration |
| `list_sessions` | – | read-only |
| `get_session` | `session_id` | full spec, version, runs |
| `close_session` | `session_id`, `delete_files=false` | **destructive**; keeps files unless `delete_files` |

## Editing

| Tool | Arguments | Example |
|---|---|---|
| `add_module` | `session_id`, `name`, `module_type`, `params?` | `add_module(sid, "rw", "reaction_wheels", {"attach_to": "sc"})` |
| `set_params` | `session_id`, `name`, `params` | `set_params(sid, "control", {"K": 7.0, "P": 40.0})` |
| `remove_module` | `session_id`, `name` | also removes its connections and recorders |
| `connect` | `session_id`, `source`, `target` | `connect(sid, "rw_torque.motor_torque", "rw.motor_torque")` |
| `disconnect` | `session_id`, `target` | `disconnect(sid, "att_error.ref")` |
| `add_recorder` | `session_id`, `name`, `source`, `sample_dt_s` | `add_recorder(sid, "states", "sc.state", 10.0)` |
| `remove_recorder` | `session_id`, `name` | |
| `set_simulation` | `session_id`, `dt_s?`, `fsw_dt_s?`, `integrator?`, `reset_fsw_dt?` | integrators: `rk4` (default), `rk2`, `euler`, `rkf45`, `rkf78` |

## Checking (read-only)

| Tool | Arguments | Returns |
|---|---|---|
| `validate_session` | `session_id`, `duration_s?` | `valid`, `problems[]` (each with a fix hint) |
| `export_script` | `session_id`, `duration_s`, `vizard=false` | the standalone Basilisk script (also saved in the data directory) |

## Running

| Tool | Arguments | Returns |
|---|---|---|
| `run_simulation` | `session_id`, `duration_s`, `label?`, `vizard=false` | run record and an overview (initial/final/min/max) of every recorded variable; with `vizard=true` the record has `vizard_file` |
| `list_runs` | `session_id` | run records |
| `get_run` | `session_id`, `run_id` | record, variables with units, overview |
| `open_in_vizard` | `session_id`, `run_id?` | launches Basilisk's Vizard on the run's `vizard.bin` (see [vizard.md](vizard.md)); error `VIZARD_NOT_FOUND` with a download link if Vizard is missing |

## Results (read-only)

`run_id` defaults to the latest completed run. A *series* is `"recorder.variable"`
or `"recorder.variable[i]"` for one component.

| Tool | Arguments | Returns |
|---|---|---|
| `query_results` | `session_id`, `recorder`, `run_id?`, `variables?`, `max_points=200`, `t_start_s?`, `t_end_s?` | down-sampled time series |
| `analyze_run` | `session_id`, `analysis` (`orbit`/`attitude`/`actuators`), `recorder?`, `run_id?`, `threshold_deg=1` | physics analysis (see below) |
| `plot_results` | `session_id`, `series[]` (1–8), `run_ids?` (≤6), `title?`, `time_unit` (`s`/`min`/`h`/`day`) | PNG image + saved path |
| `compare_runs` | `session_id`, `run_ids[]` (≥2), `series` | finals, peaks, RMS/max differences vs. the first run |
| `export_results` | `session_id`, `recorder`, `run_id?` | CSV path in the data directory |

### Analyses

* **orbit** — osculating elements over the run (semi-major axis, eccentricity,
  inclination, RAAN, argument of periapsis or, for near-circular orbits, argument of
  latitude), mean RAAN/periapsis rates, periapsis/apoapsis altitude, period, energy
  drift (point mass) and, with J2, the first-order analytic secular rates for
  comparison.
* **attitude** — initial/final/max error, settling time below `threshold_deg`,
  final rate error, error at 10/25/50/75 % of the run.
* **actuators** — wheel speed peaks and fraction of the wheel model's speed limit,
  motor torque peaks and RMS, commanded torque and angular impulse.

## Resources

| URI | Content |
|---|---|
| `basilisk://modules` | full module catalog (JSON) |
| `basilisk://sessions/{session_id}/spec` | current spec of a session |

## Prompts

| Name | Arguments | Purpose |
|---|---|---|
| `orbit_study` | `orbit`, `duration` | point-mass vs. J2 propagation study |
| `attitude_control_study` | `goal` | reaction-wheel controller tuning study |

See [`modules.md`](modules.md) for every module type, parameter and recordable variable.
