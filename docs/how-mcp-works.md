# How an MCP server is built — a walkthrough using basilisk-mcp

This guide explains the Model Context Protocol (MCP) from the ground up and shows,
file by file, how basilisk-mcp turns a scientific Python library into a safe set of
tools that Claude can use. It is written for engineers who want to understand the
design or build a similar server for another library (GMAT, Orekit, poliastro, …).

---

## 1. The problem MCP solves

A language model only produces text. To *do* something — run a simulation, read a
file, query a database — it needs a host application that executes actions on its
behalf. Before MCP, every host invented its own plugin format. MCP standardizes the
connection:

```
 ┌──────────────┐      MCP (JSON-RPC 2.0)      ┌──────────────┐      ┌────────────┐
 │ Host / client│ ◄──────────────────────────► │  MCP server  │ ───► │  Basilisk  │
 │ Claude app   │   tools, resources, prompts  │ basilisk-mcp │      │  (Python)  │
 └──────────────┘                              └──────────────┘      └────────────┘
```

* The **host** (Claude Desktop, Claude Code, another MCP client) owns the
  conversation, shows tool calls to the user, and asks for approval.
* The **server** advertises capabilities and executes them.
* The **protocol** is JSON-RPC 2.0 messages over a *transport*.

One server works with every MCP-compatible client.

## 2. The three server primitives

| Primitive | Who decides to use it | Example in basilisk-mcp |
|---|---|---|
| **Tool** | the model, during reasoning | `run_simulation(session, duration_s)` |
| **Resource** | the application or user (read-only context) | `basilisk://modules` (module catalog) |
| **Prompt** | the user (a reusable, parameterized workflow) | `attitude_control_study(goal)` |

Tools are by far the most important. Each tool has

* a **name** (`analyze_run`),
* a **description** — the model reads it to decide when and how to call the tool,
* an **input JSON Schema** — generated here from Python type hints,
* optional **annotations** (`readOnlyHint`, `destructiveHint`, …) that let the host
  decide how much confirmation to ask for,
* a result: text, images, and/or structured JSON.

## 3. A message exchange

When Claude connects, it performs a handshake, lists the tools, and later calls one:

```jsonc
// client → server
{"jsonrpc": "2.0", "id": 7, "method": "tools/call",
 "params": {"name": "analyze_run",
            "arguments": {"session": "s1a2b3c4d", "analysis": "attitude"}}}

// server → client
{"jsonrpc": "2.0", "id": 7, "result": {
  "content": [{"type": "text", "text": "{\"final_error_deg\": 0.1378, ...}"}],
  "structuredContent": {"session": "s1a2b3c4d", "analysis": "attitude",
                        "result": {"final_error_deg": 0.1378, "settling_time_s": 418.0}},
  "isError": false}}
```

Errors are returned as tool results with `isError: true` and a message the model can
read and act on. basilisk-mcp always returns JSON with a `code`, a `message`, and a
`hint` showing what a valid call looks like:

```json
{"code": "INVALID_ARGUMENT",
 "message": "Message type mismatch: sc.state produces SCStatesMsg but att_error.ref expects AttRefMsg.",
 "hint": "Compatible AttRefMsg outputs in this session: guidance.att_ref"}
```

The hint lets the model fix the call on its own.

## 4. Transports

| Transport | How it works | When to use |
|---|---|---|
| **stdio** | The host launches the server as a child process and exchanges newline-delimited JSON over stdin/stdout. | Local tools. Default for Claude Desktop and Claude Code. Nothing listens on the network. |
| **Streamable HTTP** | The server listens on an HTTP endpoint (`/mcp`); requests are POSTs, server messages can stream back. | Long-lived or shared servers, or when the host connects by URL. |

Two rules matter for stdio servers:

1. **Never print to stdout.** stdout *is* the protocol channel; a stray `print()`
   corrupts it. basilisk-mcp logs to stderr only (`cli._setup_logging`) and runs
   Basilisk, which prints freely, in a separate worker process.
2. The host owns the server's lifetime: it starts it with the command from its
   configuration file and stops it on exit.

For HTTP, the server is exposed to anything that can reach the port, so it needs
authentication and DNS-rebinding protection (see §8).

## 5. Anatomy of this repository

```
src/basilisk_mcp/
├── cli.py        entry point: serve (stdio/http), doctor, init-config, token, print-config
├── config.py     settings and limits (TOML file, BASILISK_MCP_* env vars, CLI flags)
├── server.py     the MCP surface: tools, annotations, prompts, resources
├── manager.py    sessions, versioned edits, persistence, runs
├── spec.py       declarative session spec: edit operations and whole-session validation
├── registry.py   whitelisted catalog of Basilisk modules, parameters, ports, units
├── codegen.py    spec → standalone Basilisk Python script
├── runner.py     isolated, resource-limited worker process
├── results.py    query, derived variables, orbit/attitude analysis, plots, CSV
├── templates.py  scenarios mirroring official Basilisk examples
└── errors.py     structured errors with codes and hints
```

### 5.1 Declaring a tool (`server.py`)

basilisk-mcp uses the official Python SDK (`mcp`, v2) and its high-level
`MCPServer` class. A tool is an ordinary function; type hints become the input
schema and the docstring becomes the description:

```python
@tool(READ_ONLY)
def analyze_run(session: str, analysis: Literal["orbit", "attitude", "actuators"],
                recorder: str | None = None, run_id: str | None = None,
                threshold_deg: float = 1.0) -> dict[str, Any]:
    """Physics analysis of a run.

    orbit: osculating elements, drift and mean rates (with J2 analytic rates) ...
    """
```

`Literal[...]` becomes a JSON Schema `enum`, so the model sees the allowed values
before it calls. Returning a `dict` produces both text and `structuredContent`.
`plot_results` returns an `ImageContent` (PNG) the client renders inline.

The `tool(...)` wrapper does two jobs: it attaches annotations, and it converts
internal exceptions into structured `ToolError`s, so the model never sees a raw
Python traceback.

```python
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                            idempotent_hint=True, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, ...)
```

### 5.2 Designing the tool surface

The most important design decision is the *level of abstraction* of the tools.

* **Too low** (`exec_python(code)`): maximally flexible, but the model can do
  anything, including delete files or exfiltrate data, and results are not
  reproducible. basilisk-mcp deliberately has no such tool.
* **Too high** (`simulate_my_satellite()`): safe but useless for anything new.
* **The middle** — chosen here — is a *small language of validated building
  blocks*: add a catalogued module, set its parameters, connect typed ports, record
  outputs, run, analyze. This mirrors how Basilisk itself is structured (modules and
  messages), so everything a Basilisk user knows carries over.

Further guidelines followed here:

* **Units in names.** `inclination_deg`, `torque_B_Nm`, `omega_BN_B_radps`. The
  model never has to guess whether an angle is in degrees or radians.
* **Discoverability.** `list_module_types` → `describe_module_type` →
  `list_templates`. The model can learn the catalog at runtime.
* **Context economy.** Simulations produce millions of numbers. Tools return
  summaries, analyses, down-sampled series (`max_points`), and plots; full data stays
  on disk and can be exported as CSV.
* **Self-correcting errors.** Every error names the problem and a valid alternative.
* **Optimistic concurrency.** Edits accept `expected_version` so a stale plan cannot
  silently overwrite newer changes.

### 5.3 Validation pipeline (`registry.py`, `spec.py`)

Every argument is untrusted, because the model may have been influenced by text it
read elsewhere (prompt injection). Validation happens in layers:

1. **Names** must match `^[a-z][a-z0-9_]{0,31}$`.
2. **Parameters** are checked by kind (`float`, `vec3`, `mat3`, `enum`, `orbit`,
   `ref`…), finiteness, bounds, and physical consistency (for example, the inertia
   tensor must be symmetric positive definite and satisfy the triangle inequality;
   reaction-wheel momentum must be an option the chosen wheel model offers).
3. **Connections** require identical message types on both ends.
4. **Whole-session validation** checks references, required inputs, one gravity
   field per spacecraft, periapsis above the surface, recorder rates, and the cost
   of the requested run (steps, samples, duration) against configured limits.

An edit is applied to a *copy* of the spec and committed only if it succeeds, so a
failed call never leaves a half-edited session.

### 5.4 From spec to simulation (`codegen.py`, `runner.py`)

Instead of keeping a live Basilisk object graph, the server **generates a
standalone Python script** from the validated spec and runs it in a fresh process:

```
spec (JSON) ──validate──► codegen ──ast.parse──► scenario.py ──python -I──► results.npz
```

* The script contains only numbers written with `repr(float(...))`, enum values from
  fixed tables, and validated names. No free-form client text reaches it.
* `ast.parse` is a final syntax gate before execution.
* The same script is returned by `export_script`, so users can rerun any result
  without the server: *what runs is what you export*.
* The structure mirrors Basilisk's own example scenarios. The test suite proves that
  the `attitude_feedback_rw` and `basic_orbit` templates reproduce the official
  `scenarioAttitudeFeedbackRW.py` and `scenarioBasicOrbit.py` outputs.

The worker is isolated (`runner.py`):

* `python -I -B`: isolated mode ignores `PYTHONPATH` and other `PYTHON*` variables,
  the user site directory, and the script's own directory (so a planted `json.py`
  next to the script is not imported).
* A minimal environment: secrets in the server's environment are not inherited.
* Its own process group, killed as a whole on timeout.
* POSIX limits: address space, CPU time, file size, no core dumps.
* Results are read back with `numpy.load(allow_pickle=False)`.

### 5.5 Results (`results.py`)

Raw recorder data is turned into answers: derived variables (`attitude_error_deg`,
`raan_deg`, `wheel_speeds_rpm`), orbit analysis (osculating elements, secular rates
compared with analytic J2 rates), attitude analysis (settling time, final error),
actuator analysis (wheel speed against the model's limit), run comparison, and PNG
plots.

## 6. The life of a request

User: *"Simulate the Basilisk reaction-wheel example, then make it settle faster."*

1. Claude calls `list_templates`, picks `attitude_feedback_rw`, and calls
   `create_session(template="attitude_feedback_rw")`.
2. `run_simulation(session, 600)` → validate → generate `scenario.py` → isolated
   worker → `results.npz` → overview returned.
3. `analyze_run(..., "attitude")` → settling time 418 s, final error 0.14 deg.
4. `set_params(..., "control", {"K": 7, "P": 40})`, run again, `compare_runs`,
   `plot_results` → Claude explains the trade-off between settling time and wheel
   torque.
5. `export_script` gives the user the exact Basilisk script.

## 7. Testing an MCP server

The SDK can connect a client to a server object in-process, which makes end-to-end
tests fast:

```python
from mcp import Client
from basilisk_mcp.server import create_server

async with Client(create_server(settings)) as client:
    result = await client.call_tool("create_session", {"template": "basic_orbit"})
    assert not result.is_error
```

The suite covers validation, security (injection, path traversal, tampering,
isolation, limits), code generation, the runner, the full MCP flow, and physics
against the official Basilisk examples. `examples/demo_client.py` does the same over
real stdio, like Claude does.

You can also explore any MCP server interactively with the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector basilisk-mcp serve
```

## 8. Security checklist for MCP servers

| Question | basilisk-mcp's answer |
|---|---|
| Can a tool run arbitrary code? | No. Catalogued modules only; generated code from validated literals. |
| Can a tool read or write arbitrary paths? | No. Clients never pass paths; ids are server-generated and validated. |
| Can one call exhaust the machine? | Limits on sessions, modules, steps, samples, points, runtime, memory, file size. |
| Can a crash take down the server? | No. Basilisk runs in a separate, killable process group. |
| Does the server leak secrets to subprocesses? | No. The worker gets an environment allow-list. |
| Is anything exposed on the network? | stdio by default. HTTP binds to loopback only and requires a 32+ character bearer token, with Host/Origin checks against DNS rebinding. |
| Can logging corrupt the protocol? | No. Logs go to stderr. |
| Do hosts know which tools are risky? | Yes. Tool annotations mark read-only and destructive tools. |

See [`SECURITY.md`](../SECURITY.md) for the threat model and residual risks.

## 9. Building a similar server for another library

1. List the library's **building blocks** and their typed connections.
2. Write a **registry** of whitelisted blocks with unit-explicit parameters.
3. Choose an **execution model**: a generated script per run (best for isolation and
   reproducibility), or a persistent worker for interactive, stateful engines.
4. Run the library **out of process**, with limits.
5. Design **context-economical results**: summaries, analyses, plots.
6. Prove **faithfulness** against the library's own examples.
7. Document how to **connect** it ([`connecting-claude.md`](connecting-claude.md)).
