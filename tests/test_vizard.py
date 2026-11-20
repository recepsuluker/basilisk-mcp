"""Vizard 3-D visualization: file generation and one-click launching."""

import asyncio
import json
import os
import stat
import sys
import time

import numpy as np
import pytest
from mcp import Client

from basilisk_mcp.codegen import generate_script, vizard_period
from basilisk_mcp.errors import BskMcpError
from basilisk_mcp.manager import Manager
from basilisk_mcp.server import create_server
from basilisk_mcp.vizard import launch

from .conftest import requires_basilisk

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="fake executable is a POSIX script")


def fake_vizard(tmp_path):
    """An executable that records its arguments, standing in for Vizard."""
    log = tmp_path / "vizard_args.json"
    exe = tmp_path / "Vizard.x86_64"
    exe.write_text(f"#!{sys.executable}\nimport json, sys\nopen({str(log)!r}, 'w').write(json.dumps(sys.argv[1:]))\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe, log


def wait_for(path, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end and not path.exists():
        time.sleep(0.05)
    return path.exists()


def test_vizard_period_stretches_to_frame_limit(make_spec):
    spec = make_spec("basic_orbit")  # dt = 10 s
    assert vizard_period(spec, 3600.0, 20_000) == 10.0
    assert vizard_period(spec, 30 * 86400.0, 20_000) == 130.0  # ceil(259200 / 20000) = 13 steps


def test_vizard_code_only_when_requested(make_spec):
    spec = make_spec("attitude_feedback_rw")
    assert "vizSupport" not in generate_script(spec, 60.0)
    source = generate_script(spec, 60.0, vizard=True, vizard_dt_s=0.5)
    assert "enableUnityVisualization" in source and "rwEffectorList=[m_rw]" in source
    assert 'CreateNewTask("vizardTask", macros.sec2nano(VIZARD_DT_S))' in source


def test_launch_refuses_files_outside_data_dir(settings, tmp_path):
    outside = tmp_path / "evil.bin"
    outside.write_bytes(b"x")
    settings.vizard_path = sys.executable
    with pytest.raises(BskMcpError, match="Only Vizard files"):
        launch(settings, outside)


def test_missing_vizard_has_download_hint(settings, tmp_path):
    settings.vizard_path = str(tmp_path / "nope" / "Vizard.exe")
    target = settings.data_dir / "x.bin"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x")
    with pytest.raises(BskMcpError) as exc:
        launch(settings, target)
    assert exc.value.code == "VIZARD_NOT_FOUND" and "hanspeterschaub.info/bskFiles" in exc.value.hint


@requires_basilisk
@pytest.mark.basilisk
def test_vizard_file_does_not_change_results(settings):
    manager = Manager(settings)
    session = manager.create_session(template="attitude_feedback_rw")
    plain = asyncio.run(manager.run(session.id, 60.0))
    viz = asyncio.run(manager.run(session.id, 60.0, vizard=True))
    assert "vizard_file" not in plain and viz["vizard_file"].endswith("vizard.bin")
    assert os.path.getsize(viz["vizard_file"]) > 10_000
    a = manager.run_data(session.id, plain["run_id"])[2].get("att_error_log", "sigma_BR")
    b = manager.run_data(session.id, viz["run_id"])[2].get("att_error_log", "sigma_BR")
    np.testing.assert_array_equal(a, b)


@requires_basilisk
@pytest.mark.basilisk
@posix_only
async def test_open_in_vizard_tool_launches_with_load_file(settings, tmp_path):
    exe, log = fake_vizard(tmp_path)
    settings.vizard_path = str(exe)
    async with Client(create_server(settings)) as client:
        sid = (await client.call_tool("create_session", {"template": "basic_orbit"})).structured_content["session"]
        err = await client.call_tool("open_in_vizard", {"session": sid})
        assert err.is_error and "NOT_FOUND" in err.content[0].text
        run = await client.call_tool("run_simulation", {"session": sid, "duration_s": 600, "vizard": True})
        vizard_file = run.structured_content["run"]["vizard_file"]
        opened = await client.call_tool("open_in_vizard", {"session": sid})
        assert not opened.is_error, opened.content[0].text
    assert wait_for(log)
    assert json.loads(log.read_text()) == ["-loadFile", vizard_file]


@requires_basilisk
@pytest.mark.basilisk
@posix_only
def test_cli_opens_latest_vizard_file(settings, tmp_path, monkeypatch):
    exe, log = fake_vizard(tmp_path)
    manager = Manager(settings)
    session = manager.create_session(template="basic_orbit")
    asyncio.run(manager.run(session.id, 600.0))  # no Vizard file
    record = asyncio.run(manager.run(session.id, 600.0, vizard=True))
    monkeypatch.setenv("BASILISK_MCP_VIZARD_PATH", str(exe))
    from basilisk_mcp.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["--data-dir", str(settings.data_dir), "vizard"])
    assert exc.value.code == 0
    assert wait_for(log)
    assert json.loads(log.read_text()) == ["-loadFile", record["vizard_file"]]


@requires_basilisk
@pytest.mark.basilisk
@posix_only
async def test_auto_open_after_run(settings, tmp_path):
    exe, log = fake_vizard(tmp_path)
    settings.vizard_path = str(exe)
    settings.vizard_auto_open = True
    async with Client(create_server(settings)) as client:
        sid = (await client.call_tool("create_session", {"template": "basic_orbit"})).structured_content["session"]
        run = await client.call_tool("run_simulation", {"session": sid, "duration_s": 600, "vizard": True})
        assert run.structured_content["vizard"]["launched"] is True
    assert wait_for(log)
