# basilisk-mcp — Design and Implementation Plan

This document is the engineering plan for **basilisk-mcp**, a
[Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that lets
an AI assistant such as Claude build, run, and analyze
[Basilisk](https://github.com/AVSLab/basilisk) astrodynamics simulations through
a small set of well-defined, validated tools.

It records *what* we are building, *why* each design decision was made, and the
order in which the work is delivered. It is kept in the repository so that
contributors and reviewers can follow the reasoning.

---

## 1. Goals and non-goals

### Goals

1. **Natural-language simulation.** A user describes a scenario ("a 500 km
   LEO spacecraft with three reaction wheels pointing at nadir") and Claude
   assembles, runs, and analyzes it with Basilisk.
2. **Safety by construction.** The server never executes code supplied by the
   model or the user. Every simulation is assembled from a whitelisted catalog
   of Basilisk modules whose parameters are type-, shape- and range-checked.
3. **Reproducibility.** Every run is backed by a standalone, human-readable
   Basilisk Python script that the server generates and stores next to the
   results. *What runs is exactly what you can export.*
4. **Faithfulness to Basilisk.** Scenarios built through the server reproduce
   the official Basilisk example scenarios (`scenarioBasicOrbit`,
   `scenarioAttitudeFeedbackRW`) numerically. This is enforced by tests.
5. **Isolation.** Basilisk runs in a separate, resource-limited worker process
   per run. A crash, hang, or memory blow-up in a simulation cannot take down
   the MCP server.
6. **Context-friendly results.** Large time histories stay on disk. Claude
   receives summaries, down-sampled series, analyses, and plots.

### Non-goals (for v0.1)

* Exposing every Basilisk module. The catalog starts with the modules needed
  for orbit propagation and attitude control and grows deliberately.
* A "run arbitrary Python" tool. This is intentionally excluded (see §6).
* SPICE ephemerides, third-body perturbations, atmospheric drag, Vizard
  streaming, Monte Carlo. These are on the roadmap (§10).
* Remote/multi-user hosting. The server is designed for a single local user.

---

## 2. Background: what an MCP server is

MCP is an open protocol (JSON-RPC 2.0) that connects an AI application (the
*client/host*, e.g. Claude Desktop or Claude Code) to external capabilities
(the *server*). A server can expose:

| Primitive     | Controlled by | Used here for                                     |
|---------------|---------------|---------------------------------------------------|
| **Tools**     | the model     | building sessions, running simulations, analysis  |
| **Resources** | the app/user  | the module catalog, generated scripts             |
| **Prompts**   | the user      | guided workflows ("orbit study", "tune a controller") |

Two transports are supported:

* **stdio** — the client launches the server as a child process and talks over
  stdin/stdout. Nothing listens on the network. This is the default and the
  recommended way to use the server with Claude Desktop and Claude Code.
* **Streamable HTTP** — the server listens on a local port. We bind to the
  loopback interface only and require a bearer token.

A full tutorial is in [`docs/how-mcp-works.md`](docs/how-mcp-works.md).

---

## 3. Architecture

```
┌──────────────────────┐   MCP (stdio or local HTTP)   ┌───────────────────────────────┐
│ Claude Desktop /     │ ◄───────────────────────────► │ basilisk-mcp server (Python)  │
│ Claude Code / other  │                               │                               │
│ MCP client           │                               │  server.py   MCP tools        │
└──────────────────────┘                               │  manager.py  sessions, runs   │
                                                       │  spec.py     validation       │
                                                       │  registry.py module catalog   │
                                                       │  codegen.py  spec → script    │
                                                       │  results.py  query / analyze  │
                                                       └──────────────┬────────────────┘
                                                                      │ writes scenario.py
                                                                      │ spawns (isolated, rlimited)
                                                                      ▼
                                                       ┌───────────────────────────────┐
                                                       │ worker process                │
                                                       │ python -I scenario.py         │
                                                       │   import Basilisk             │
                                                       │   build → run → results.npz   │
                                                       └───────────────────────────────┘
```

### 3.1 Declarative session spec

A **session** is a JSON document (the *spec*) describing one simulation:

```json
{
  "simulation": {"dt_s": 0.1, "fsw_dt_s": 0.1, "integrator": "rk4"},
  "modules": {
    "sc":      {"type": "spacecraft", "params": {"mass_kg": 750.0, "orbit": {...}}},
    "gravity": {"type": "gravity", "params": {"central_body": "earth", "attach_to": ["sc"]}},
    "nav":     {"type": "simple_nav", "params": {}}
  },
  "connections": [{"source": "sc.state", "target": "nav.state"}],
  "recorders":   {"states": {"source": "sc.state", "sample_dt_s": 10.0}}
}
```

Tools edit the spec (add a module, set parameters, connect message ports, add
a recorder). The spec is versioned; every mutation increments `version`, and
mutating tools accept an optional `expected_version` for optimistic
concurrency.

### 3.2 Code generation instead of a live object graph

When a run is requested the server:

1. validates the full spec (types, references, required connections, limits),
2. generates a standalone Basilisk script from it (`codegen.py`),
3. parses the script with `ast` as a final syntax gate,
4. writes it to `runs/<run_id>/scenario.py`,
5. executes it in an isolated worker process,
6. loads `results.npz` (with `allow_pickle=False`) and `summary.json`.

Why generate code instead of keeping a live Basilisk object graph in a worker?

* **Single source of truth:** the exported script *is* the run.
* **Auditable:** users and reviewers can read exactly what was simulated.
* **Stateless workers:** each run starts from a clean interpreter, so there is
  no hidden state between runs, and Basilisk's process-global factories
  (gravity, reaction wheels) cannot leak across runs.
* **Injection-resistant:** the generator only emits literals produced by
  `repr()` of validated numbers/booleans, enum values taken from fixed tables,
  and names matching `^[a-z][a-z0-9_]{0,31}$`. No free-form string from the
  client ever reaches the script.

### 3.3 Message wiring

Basilisk modules communicate through typed messages. The registry declares, for
every module type, its input and output **ports** with their message type
(e.g. `simple_nav.att` → `NavAttMsg`). `connect` only accepts a pair of ports
with the same message type and refuses to connect an input twice. Validation
reports every required input that is still unconnected, with a hint naming the
compatible outputs that exist in the session.

### 3.4 Tasks and execution order

Each run has one process with two tasks: `dynamicsTask` (`dt_s`) and `fswTask`
(`fsw_dt_s`, defaults to `dt_s`). Module priorities mirror the official
Basilisk examples (reaction wheels → spacecraft → navigation → guidance →
tracking error → control → motor torque mapping), so results match them.

---

## 4. Module catalog (v0.1)

| Type                 | Basilisk class                                  | Role |
|----------------------|--------------------------------------------------|------|
| `spacecraft`         | `simulation.spacecraft.Spacecraft`               | rigid-body hub, initial orbit and attitude |
| `gravity`            | `utilities.simIncludeGravBody.gravBodyFactory`   | point-mass gravity, optional J2 (Earth, Mars) |
| `ext_force_torque`   | `simulation.extForceTorque.ExtForceTorque`       | constant or commanded body torque/force |
| `simple_nav`         | `simulation.simpleNav.SimpleNav`                 | ideal navigation (attitude + translation) |
| `reaction_wheels`    | `simulation.reactionWheelStateEffector` + `simIncludeRW` | Honeywell HR12/14/16 wheel clusters |
| `inertial3d`         | `fswAlgorithms.inertial3D`                       | inertial pointing reference |
| `hill_point`         | `fswAlgorithms.hillPoint`                        | nadir (Hill frame) pointing reference |
| `att_tracking_error` | `fswAlgorithms.attTrackingError`                 | attitude guidance error |
| `mrp_feedback`       | `fswAlgorithms.mrpFeedback`                      | MRP PD/PID attitude control law |
| `rw_motor_torque`    | `fswAlgorithms.rwMotorTorque`                    | maps body torque to wheel motor torques |

Recordable message types: `SCStatesMsg`, `NavAttMsg`, `NavTransMsg`,
`AttRefMsg`, `AttGuidMsg`, `CmdTorqueBodyMsg`, `RWSpeedMsg`,
`ArrayMotorTorqueMsg`. Derived variables (e.g. `attitude_error_deg`,
`altitude_km`, `wheel_speeds_rpm`) are computed server-side.

Units are SI and explicit in every parameter name (`mass_kg`,
`torque_B_Nm`, `inclination_deg`, `omega_BN_B_radps`). Frame suffixes follow
Basilisk conventions (`_B` body, `_N` inertial).

---

## 5. Tool catalog

| Group      | Tool | Read-only |
|------------|------|-----------|
| Discovery  | `server_info`, `list_module_types`, `describe_module_type`, `list_templates` | yes |
| Sessions   | `create_session`, `list_sessions`, `get_session`, `close_session` | no / yes |
| Editing    | `add_module`, `set_params`, `remove_module`, `connect`, `disconnect`, `add_recorder`, `remove_recorder`, `set_simulation` | no |
| Checking   | `validate_session`, `export_script` | yes |
| Running    | `run_simulation`, `list_runs`, `get_run` | no / yes |
| Results    | `query_results`, `analyze_run`, `plot_results`, `compare_runs`, `export_results` | yes* |

\* `export_results` and `plot_results` write files inside the server's data
directory only.

Templates bootstrap common scenarios in one call:
`basic_orbit` (mirrors `scenarioBasicOrbit`),
`attitude_feedback_rw` (mirrors `scenarioAttitudeFeedbackRW`),
`attitude_feedback_torque` (mirrors `scenarioAttitudeFeedback`, direct torque),
`nadir_pointing_rw` (Hill-frame pointing with reaction wheels).

Prompts: `orbit_study`, `attitude_control_study`.
Resources: `basilisk://modules`, `basilisk://sessions/{session_id}/script`.

---

## 6. Security model

Threat model: the MCP client relays text generated by a language model, which
may be influenced by untrusted content (prompt injection). The server must
therefore treat **every tool argument as untrusted input**.

| Risk | Mitigation |
|------|------------|
| Arbitrary code execution | No eval/exec of client input; no "run Python" tool; code generated only from validated literals; names restricted by regex; enums from fixed tables; generated script re-parsed with `ast` before running. |
| Path traversal / arbitrary file access | Clients never supply paths. Session and run IDs are server-generated and looked up in memory; all output goes under the data directory (created with `0700` permissions). |
| Resource exhaustion | Limits on sessions, modules, recorders, runs, simulation duration, integration steps, recorded samples, returned points; per-run wall-clock timeout; concurrency semaphore; POSIX `RLIMIT_AS`, `RLIMIT_CPU`, `RLIMIT_FSIZE`, `RLIMIT_CORE=0` on workers. |
| Crashing the server | Basilisk only runs in the worker process; the worker gets its own process group and is killed as a group on timeout. |
| Environment leakage into the worker | Worker started with `python -I` (isolated mode: ignores `PYTHON*` env vars, user site-packages and the script directory) and a minimal environment allow-list. |
| Unsafe deserialization | Results loaded with `numpy.load(allow_pickle=False)`; JSON only otherwise. |
| Network exposure (HTTP mode) | Refuses to bind to non-loopback addresses; requires `BASILISK_MCP_TOKEN` (≥ 32 chars) compared in constant time; Host/Origin validation against DNS rebinding. |
| Corrupting the MCP stream | All logging goes to stderr; stdout is reserved for JSON-RPC in stdio mode. |

Residual risks (documented in `SECURITY.md`): the worker is not a full OS
sandbox (no seccomp/containers); a malicious *Basilisk installation* is out of
scope; Windows lacks POSIX rlimits (timeouts still apply).

---

## 7. Configuration

Optional TOML file (`basilisk-mcp init-config`), environment variables, and
CLI flags, in increasing precedence. Key settings: `data_dir`, `python`
(interpreter used for workers, must have `bsk` installed), `max_sessions`,
`max_concurrent_runs`, `run_timeout_s`, `max_sim_duration_s`, `max_steps`,
`max_recorded_samples`, `max_query_points`, `memory_limit_mb`. See
[`docs/configuration.md`](docs/configuration.md).

---

## 8. Testing strategy

| Layer | What is tested |
|-------|----------------|
| Unit — validation | every parameter kind, bounds, enums, references, port typing, limits, error messages |
| Unit — security | hostile names, path-like IDs, non-finite numbers, oversized inputs, code-injection strings, no client string reaching generated code |
| Unit — codegen | scripts compile, are deterministic, contain only expected imports |
| Integration — runner | timeouts kill the process group, failures are reported with logs |
| Integration — MCP | full tool flow through an in-process MCP client |
| Physics — reference | J2 RAAN drift vs. analytic rate; reproduction of official `scenarioBasicOrbit` and `scenarioAttitudeFeedbackRW` outputs |

Basilisk-dependent tests are skipped automatically when `bsk` is not
installed. CI runs on Linux and macOS for Python 3.11–3.13.

---

## 9. Delivery milestones

1. **M1 — Skeleton:** package layout, config, CLI, logging, empty MCP server.
2. **M2 — Catalog and validation:** registry, spec model, validation errors.
3. **M3 — Codegen and runner:** script generation, isolated worker, limits.
4. **M4 — Results:** query, derived variables, analysis, plots, comparison, export.
5. **M5 — MCP surface:** tools, annotations, prompts, resources, templates.
6. **M6 — Verification:** unit/security/integration/reference tests, demo client.
7. **M7 — Documentation and release:** README, docs, SECURITY, CI, v0.1.0 tag.
8. **M8 — Community:** announce in Basilisk GitHub Discussions; propose a link
   from Basilisk's External Sites page; discuss upstream adoption with AVSLab.

---

## 10. Roadmap after v0.1

* Atmospheric drag (`exponentialAtmosphere`, `dragDynamicEffector`).
* SPICE-backed ephemerides and third-body gravity.
* Thrusters and orbit maneuvers (impulsive Δv, Hohmann template).
* Sensor noise models (`simpleNav` noise, coarse sun sensors, star tracker).
* Monte Carlo / parameter sweeps as background studies.
* Live Vizard streaming (`liveStream`); recorded Vizard files are available since v0.2.
* Formation flying (multiple spacecraft templates).
