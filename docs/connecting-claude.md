# Connecting basilisk-mcp to Claude

basilisk-mcp is a **local** MCP server. The recommended setup is the **stdio**
transport: Claude starts the server as a child process on your machine, and nothing
listens on the network.

* [1. Install](#1-install)
* [2. Check the installation](#2-check-the-installation)
* [3. Claude Desktop](#3-claude-desktop)
* [4. Claude Code](#4-claude-code)
* [5. Local HTTP transport (optional)](#5-local-http-transport-optional)
* [6. Using a separate Basilisk interpreter](#6-using-a-separate-basilisk-interpreter)
* [7. First prompts to try](#7-first-prompts-to-try)
* [8. Troubleshooting](#8-troubleshooting)

---

## 1. Install

Requirements: Python 3.11+ and a platform with Basilisk wheels (Linux x86_64/arm64,
macOS, Windows). Basilisk is installed from PyPI as `bsk`.

```bash
git clone https://github.com/recepsuluker/basilisk-mcp.git
cd basilisk-mcp
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[basilisk]"         # installs basilisk-mcp + Basilisk (bsk)
```

With [uv](https://docs.astral.sh/uv/):

```bash
uv venv && uv pip install -e ".[basilisk]"
```

## 2. Check the installation

```bash
basilisk-mcp doctor
```

```
basilisk-mcp 0.1.0
[ok] server python: 3.13.1
[ok] worker python: /path/to/.venv/bin/python
[ok] basilisk import: Basilisk 2.12.2 on Python 3.13.1
[ok] data directory: /home/you/.basilisk-mcp (mode 0o700)
[ok] smoke simulation: basic_orbit for 600 s in 2.9 s
All checks passed.
```

`doctor` imports Basilisk in a worker exactly as the server will, and runs a short
orbit simulation.

Find the absolute path of the executable; Claude needs it because it does not
activate your virtual environment:

```bash
which basilisk-mcp        # macOS/Linux, e.g. /home/you/basilisk-mcp/.venv/bin/basilisk-mcp
where basilisk-mcp        # Windows,     e.g. C:\Users\you\basilisk-mcp\.venv\Scripts\basilisk-mcp.exe
```

`basilisk-mcp print-config claude-desktop` and `basilisk-mcp print-config claude-code`
print ready-to-paste configuration with these absolute paths filled in.

## 3. Claude Desktop

1. Open Claude Desktop → **Settings → Developer → Edit Config**. This opens
   `claude_desktop_config.json`:
   * macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   * Windows: `%APPDATA%\Claude\claude_desktop_config.json`
2. Add the server (keep any existing entries in `mcpServers`):

   ```json
   {
     "mcpServers": {
       "basilisk": {
         "command": "/home/you/basilisk-mcp/.venv/bin/basilisk-mcp",
         "args": ["serve"]
       }
     }
   }
   ```

   Windows (escape backslashes in JSON):

   ```json
   {
     "mcpServers": {
       "basilisk": {
         "command": "C:\\Users\\you\\basilisk-mcp\\.venv\\Scripts\\basilisk-mcp.exe",
         "args": ["serve"]
       }
     }
   }
   ```

   With a configuration file: `"args": ["--config", "/home/you/basilisk-mcp.toml", "serve"]`.
3. Quit Claude Desktop completely and start it again.
4. In a new chat, open the tools menu: the **basilisk** server and its tools should
   be listed. Claude asks for permission the first time each tool is used.

A copy of this file is in [`examples/claude_desktop_config.json`](../examples/claude_desktop_config.json).

## 4. Claude Code

Register the server with the `claude mcp add` command. Options for Claude Code
(`--scope`, `--env`) come before the server name; everything after `--` is the
command that starts the server.

```bash
# Only you, only the current project (default "local" scope)
claude mcp add basilisk -- /home/you/basilisk-mcp/.venv/bin/basilisk-mcp serve

# You, in all projects
claude mcp add --scope user basilisk -- /home/you/basilisk-mcp/.venv/bin/basilisk-mcp serve

# Pass settings through environment variables
claude mcp add --scope user -e BASILISK_MCP_RUN_TIMEOUT_S=600 basilisk -- \
    /home/you/basilisk-mcp/.venv/bin/basilisk-mcp serve
```

Check it:

```bash
claude mcp list
claude mcp get basilisk
```

Inside a Claude Code session, `/mcp` shows the server's status and tools.

**Share with a team (project scope).** `claude mcp add --scope project ...` writes a
`.mcp.json` file at the repository root that you can commit:

```json
{
  "mcpServers": {
    "basilisk": {
      "command": "basilisk-mcp",
      "args": ["serve"]
    }
  }
}
```

Team members are asked to approve project servers the first time they open the
project. Using a bare `basilisk-mcp` command works when it is on everyone's `PATH`.

Remove it with `claude mcp remove basilisk`.

## 5. Local HTTP transport (optional)

Use HTTP when you want one long-running server that several clients connect to by
URL. The server refuses to bind to anything but loopback and requires a bearer token.

```bash
export BASILISK_MCP_TOKEN="$(basilisk-mcp token)"      # 43-character random token
basilisk-mcp serve --transport http --port 8765
# → Serving MCP on http://127.0.0.1:8765/mcp (bearer token required)
```

Claude Code:

```bash
claude mcp add --transport http basilisk http://127.0.0.1:8765/mcp \
    --header "Authorization: Bearer $BASILISK_MCP_TOKEN"
```

Requests without the right token get `401`; requests with an unexpected `Host` or
`Origin` header are rejected (DNS-rebinding protection).

> claude.ai custom connectors connect from Anthropic's cloud and need a public HTTPS
> endpoint with OAuth. basilisk-mcp is intentionally a local, single-user server and
> does not support that mode; use Claude Desktop or Claude Code.

## 6. Using a separate Basilisk interpreter

The server itself needs only `mcp`, `numpy` and `matplotlib`. Basilisk can live in a
different interpreter, for example one you built from source:

```toml
# basilisk-mcp.toml  (create a template with: basilisk-mcp init-config)
[server]
python = "/opt/basilisk/.venv/bin/python"
```

```bash
basilisk-mcp --config basilisk-mcp.toml doctor
```

Then add `--config /absolute/path/basilisk-mcp.toml` before `serve` in the Claude
configuration. See [`configuration.md`](configuration.md) for every setting.

## 7. First prompts to try

* *"What Basilisk scenarios can you run? List the templates."*
* *"Propagate a 700 km sun-synchronous orbit for three days with and without J2 and
  compare the RAAN drift with the analytic rate."*
* *"Run the Basilisk reaction-wheel attitude example, then tune the gains so it
  settles below 1 degree faster without exceeding 4000 rpm. Plot the comparison."*
* *"Build from scratch: a 300 kg spacecraft in a 550 km, 53 deg orbit, nadir
  pointing with three HR16 wheels starting from a 0.05 rad/s tumble. How long until
  it is within 0.5 degree?"*
* *"Export the script for the last run so I can rerun it myself."*

The server also provides two prompts, **orbit_study** and **attitude_control_study**,
that appear in clients supporting MCP prompts (for example, as slash commands in
Claude Code).

More examples: [`examples/prompts.md`](../examples/prompts.md).

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| Server not listed in Claude Desktop | Validate the JSON (no trailing commas), use the absolute path, fully quit and restart Claude Desktop. Check Claude's MCP logs (`mcp-server-basilisk.log` in the Claude logs folder: `~/Library/Logs/Claude` on macOS, `%APPDATA%\Claude\logs` on Windows). |
| `spawn basilisk-mcp ENOENT` | The command is not on Claude's `PATH`; use the absolute path from `which basilisk-mcp`. |
| `basilisk import` fails in `doctor` | Install Basilisk in the worker interpreter (`pip install bsk`) or set `python = ...` to an interpreter that has it. |
| `RUN_FAILED` with a `MemoryError` | Raise `memory_limit_mb` or record less data (larger `sample_dt_s`). |
| `RUN_FAILED: timed out` | Raise `run_timeout_s`, increase `dt_s`, or shorten `duration_s`. |
| `SPEC_INVALID` | Call `validate_session`; each problem says what to connect or change. |
| HTTP mode returns 401 | Send `Authorization: Bearer <token>` with the same value as `BASILISK_MCP_TOKEN`. |

To see the server's own log, run it by hand: `basilisk-mcp serve --log-level DEBUG`
(it then waits for JSON-RPC on stdin; stop with Ctrl-C).
