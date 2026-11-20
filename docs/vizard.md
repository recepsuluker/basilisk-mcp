# 3-D visualization with Vizard

[Vizard](https://avslab.github.io/basilisk/Vizard/Vizard.html) is Basilisk's Unity-based
3-D visualizer. It shows the spacecraft, planets, orbit, attitude, body axes and
reaction wheels, and lets you scrub through time. basilisk-mcp can record a Vizard
file for any run and open it in Vizard with one call, one command, or one click.

```
"Run the reaction-wheel example and show it in 3-D"
        │
        ▼
run_simulation(session, 600, vizard=true)   → runs/r001/vizard.bin
open_in_vizard(session)                     → Vizard -loadFile .../vizard.bin
```

## 1. Install Vizard (once)

Vizard is a separate desktop application from the Basilisk team.

| Platform | Download |
|---|---|
| Windows | https://hanspeterschaub.info/bskFiles/Vizard_Windows64.zip |
| macOS | https://hanspeterschaub.info/bskFiles/Vizard_macOS.zip |
| Linux | https://hanspeterschaub.info/bskFiles/Vizard_Linux.zip |

Unzip it. basilisk-mcp looks for Vizard automatically in these places:

* **Windows:** `%USERPROFILE%\Vizard\`, `%USERPROFILE%\Vizard_Windows64\`, the same names under
  `Downloads\` and `Desktop\` (also one sub-folder deep), `%LOCALAPPDATA%\Programs\Vizard\`,
  `C:\Program Files\Vizard\`
* **macOS:** `/Applications/Vizard.app`, `~/Applications/Vizard.app`, `~/Downloads/Vizard.app`
* **Linux:** `~/Vizard/`, `~/Vizard_Linux/`, `~/Downloads/Vizard_Linux/`, `/opt/Vizard/`

Check what was found:

```bash
basilisk-mcp vizard --find
```

If Vizard is somewhere else, set its path in the config file (or the
`BASILISK_MCP_VIZARD_PATH` environment variable):

```toml
[server]
vizard_path = "C:/Users/you/Tools/Vizard/Vizard.exe"
```

The Basilisk wheel from PyPI already includes the `vizInterface` module that writes
Vizard files; nothing has to be compiled.

## 2. Ask Claude

* *"Run the reaction-wheel attitude example with Vizard and open it."*
* *"Propagate a 700 km sun-synchronous orbit for one day and show it in Vizard."*
* *"Open the last run in Vizard."*

Claude calls `run_simulation(..., vizard=true)` and then `open_in_vizard`. To have
Vizard open automatically after every Vizard run, set:

```toml
[server]
vizard_auto_open = true
```

## 3. One command or one click

`basilisk-mcp vizard` opens the newest Vizard file of any session:

```bash
basilisk-mcp vizard                                 # newest Vizard run
basilisk-mcp vizard --session s1a2b3c4d             # newest Vizard run of a session
basilisk-mcp vizard --session s1a2b3c4d --run r002  # a specific run
```

### Windows desktop shortcut

Run this once in PowerShell (adjust the path to where `basilisk-mcp.exe` is installed);
it puts an **Open in Vizard** icon on the desktop:

```powershell
$exe = "$HOME\basilisk\.venv\Scripts\basilisk-mcp.exe"
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut(
    [Environment]::GetFolderPath("Desktop") + "\Open in Vizard.lnk")
$lnk.TargetPath = $exe
$lnk.Arguments = "vizard"
$lnk.WindowStyle = 7          # start minimized
$lnk.Save()
```

Double-click it after Claude has run a simulation with Vizard enabled.

### macOS / Linux

Create an alias or a desktop launcher that runs `basilisk-mcp vizard`.

## One-click scenarios

`basilisk-mcp run <template or session>` runs a scenario and opens it in Vizard in one
step; see [one-click.md](one-click.md) for desktop shortcuts.

## What is recorded

* All spacecraft in the session, their attitude and body axes, labels.
* The gravity body (Earth, Mars, …) discovered from the spacecraft's gravity field.
* Reaction wheels (with the wheel panel and HUD turned on) when the spacecraft has a
  `reaction_wheels` module.
* Frames at the dynamics step, stretched to an integer multiple so a file holds at
  most `max_vizard_frames` (default 20 000) frames. A 600 s attitude run at 0.1 s is
  about 3 MB; a one-day orbit at 10 s is about 2 MB.

Recording a Vizard file does not change the simulation results (this is tested).

The exported script (`export_script`) contains the Vizard lines when the run used
them, so the standalone script reproduces the file too.

## Opening a file manually

Start Vizard, press **Select** on the start panel, choose the run's `vizard.bin`
(the path is in the run record, under the server's data directory, for example
`~/.basilisk-mcp/sessions/<session>/runs/<run>/vizard.bin`), then **Start Visualization**.

## Security

`open_in_vizard` starts only the configured or auto-detected Vizard executable, never
a program named in a tool call, and passes only a `vizard.bin` path that the server
wrote inside its data directory.
