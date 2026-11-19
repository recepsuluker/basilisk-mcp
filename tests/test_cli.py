import json

from basilisk_mcp.cli import main


def run(args, capsys):
    try:
        main(args)
    except SystemExit as exc:
        assert exc.code == 0
    return capsys.readouterr().out


def test_print_config(capsys, tmp_path):
    out = run(["print-config", "claude-desktop"], capsys)
    assert json.loads(out)["mcpServers"]["basilisk"]["args"][-1] == "serve"
    assert run(["print-config", "claude-code"], capsys).startswith("claude mcp add basilisk -- ")


def test_token_and_init_config(capsys, tmp_path):
    assert len(run(["token"], capsys).strip()) >= 40
    path = tmp_path / "cfg.toml"
    run(["init-config", str(path)], capsys)
    from basilisk_mcp.config import load_settings

    assert load_settings(path, data_dir=tmp_path).max_sessions == 16
