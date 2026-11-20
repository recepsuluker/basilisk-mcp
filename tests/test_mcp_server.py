"""End-to-end tests through a real MCP client connected in-process."""

import json

import pytest
from mcp import Client

from basilisk_mcp.server import create_server

from .conftest import requires_basilisk


def payload(result):
    assert not result.is_error, result.content[0].text
    return result.structured_content


def error(result):
    assert result.is_error
    text = result.content[0].text
    return json.loads(text[text.index("{"):])


async def test_discovery_tools(settings):
    async with Client(create_server(settings)) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert {"create_session", "run_simulation", "analyze_run", "plot_results", "export_script"} <= names
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert tools["list_module_types"].annotations.read_only_hint is True
        assert tools["close_session"].annotations.destructive_hint is True
        described = payload(await client.call_tool("describe_module_type", {"module_type": "mrp_feedback"}))
        assert described["params"]["K"]["unit"] == "N*m"
        prompts = {p.name for p in (await client.list_prompts()).prompts}
        assert {"orbit_study", "attitude_control_study"} <= prompts


async def test_errors_are_structured(settings):
    async with Client(create_server(settings)) as client:
        sid = payload(await client.call_tool("create_session", {"template": "basic_orbit"}))["session"]
        err = error(await client.call_tool("add_module", {"session": sid, "name": "x", "module_type": "laser"}))
        assert err["code"] == "INVALID_ARGUMENT" and "Available types" in err["hint"]
        err = error(await client.call_tool("run_simulation", {"session": sid, "duration_s": 1e15}))
        assert err["code"] == "SPEC_INVALID"
        err = error(await client.call_tool("set_params", {"session": sid, "name": "sc", "params": {"mass_kg": 1},
                                                          "expected_version": 99}))
        assert err["code"] == "CONFLICT"


async def test_build_from_scratch_validation_flow(settings):
    async with Client(create_server(settings)) as client:
        sid = payload(await client.call_tool("create_session", {"name": "scratch"}))["session"]
        await client.call_tool("add_module", {"session": sid, "name": "sc", "module_type": "spacecraft",
                                              "params": {"orbit": {"altitude_km": 400, "inclination_deg": 51.6}}})
        report = payload(await client.call_tool("validate_session", {"session": sid}))
        assert not report["valid"] and any("gravity" in p for p in report["problems"])
        await client.call_tool("add_module", {"session": sid, "name": "earth", "module_type": "gravity",
                                              "params": {"attach_to": ["sc"]}})
        await client.call_tool("set_simulation", {"session": sid, "dt_s": 5.0})
        await client.call_tool("add_recorder", {"session": sid, "name": "states", "source": "sc.state",
                                                "sample_dt_s": 30.0})
        report = payload(await client.call_tool("validate_session", {"session": sid, "duration_s": 5400}))
        assert report["valid"], report
        script = payload(await client.call_tool("export_script", {"session": sid, "duration_s": 5400}))
        assert "orbitalMotion.elem2rv" in script["script"]


@requires_basilisk
@pytest.mark.basilisk
async def test_full_attitude_workflow(settings):
    async with Client(create_server(settings)) as client:
        sid = payload(await client.call_tool("create_session", {"template": "attitude_feedback_rw"}))["session"]
        run = payload(await client.call_tool("run_simulation", {"session": sid, "duration_s": 600}))
        assert run["run"]["status"] == "completed"
        att = payload(await client.call_tool("analyze_run", {"session": sid, "analysis": "attitude"}))["result"]
        assert att["settled"] and att["final_error_deg"] < 1.0
        await client.call_tool("set_params", {"session": sid, "name": "control", "params": {"K": 7.0}})
        payload(await client.call_tool("run_simulation", {"session": sid, "duration_s": 600}))
        cmp = payload(await client.call_tool("compare_runs", {"session": sid, "run_ids": ["r001", "r002"],
                                                              "series": "att_error_log.attitude_error_deg"}))
        assert "difference_vs_baseline" in cmp["runs"]["r002"]
        plot = await client.call_tool("plot_results", {"session": sid, "run_ids": ["r001", "r002"],
                                                       "series": ["att_error_log.attitude_error_deg"]})
        assert not plot.is_error and plot.content[0].type == "image"
        q = payload(await client.call_tool("query_results", {"session": sid, "recorder": "rw_speed_log",
                                                             "variables": ["wheel_speeds_rpm"], "max_points": 10}))
        assert q["points_returned"] == 10 and len(q["variables"]["wheel_speeds_rpm"]["values"][0]) == 3
        exported = payload(await client.call_tool("export_results", {"session": sid, "recorder": "rw_speed_log"}))
        assert exported["rows"] == 601


async def test_no_tool_uses_reserved_session_id_parameter(settings):
    """Claude Desktop's local MCP bridge strips arguments named `session_id`; tools must use `session`."""
    async with Client(create_server(settings)) as client:
        for tool in (await client.list_tools()).tools:
            assert "session_id" not in tool.input_schema.get("properties", {}), tool.name
