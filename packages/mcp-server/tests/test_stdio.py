"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Test MCP CLI diagnostics, env-file loading and real stdio protocol framing.
"""  # noqa: D205, D212

import json
import os
import select
import shutil
import subprocess
from pathlib import Path

import pytest
from athlete_ledger_mcp.cli import run
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import CLIENT_CAPABILITIES_META_KEY, CLIENT_INFO_META_KEY, PROTOCOL_VERSION_META_KEY


def synthetic_env_file(tmp_path: Path, ledger_file: Path) -> Path:
    """Write a temporary settings file containing only a synthetic SQLite URL.

    Args:
        tmp_path: Isolated configuration directory.
        ledger_file: Synthetic ledger used by the subprocess.

    Returns:
        Absolute settings-file path suitable for a client with an unrelated cwd.
    """
    path = tmp_path / "synthetic-settings.env"
    path.write_text(f'ATHLETE_LEDGER_DB_URL="sqlite:///{ledger_file}"\n')
    return path


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_sdk_stdio_client_protocols(ledger_file, tmp_path, mode) -> None:
    """Start the installed CLI using an explicit env-file and call it through the SDK."""
    settings = synthetic_env_file(tmp_path, ledger_file)
    arbitrary_cwd = tmp_path / "desktop-cwd"
    arbitrary_cwd.mkdir()
    params = StdioServerParameters(
        command=shutil.which("athlete-ledger-mcp"),
        args=["--env-file", str(settings)],
        cwd=arbitrary_cwd,
    )
    async with Client(params, mode=mode, read_timeout_seconds=10) as client:
        assert client.protocol_version == ("2025-11-25" if mode == "legacy" else mode)
        assert len((await client.list_tools()).tools) == 4
        result = await client.call_tool("ledger_status")
        assert not result.is_error and len(result.structured_content["datasets"]) == 4


def test_stdio_stdout_is_only_jsonrpc_messages(ledger_file, tmp_path) -> None:
    """Inspect raw subprocess stdout to reject banners, prints or malformed protocol lines."""
    settings = synthetic_env_file(tmp_path, ledger_file)
    process = subprocess.Popen(
        ["athlete-ledger-mcp", "--env-file", str(settings)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=dict(os.environ),
    )
    meta = {
        PROTOCOL_VERSION_META_KEY: "2026-07-28",
        CLIENT_INFO_META_KEY: {"name": "synthetic-test", "version": "1"},
        CLIENT_CAPABILITIES_META_KEY: {},
    }
    try:
        for identity, method, arguments in [
            (1, "tools/list", {}),
            (
                2,
                "tools/call",
                {
                    "name": "list_activities",
                    "arguments": {"start": "2020-12-31", "end": "2021-01-04"},
                },
            ),
        ]:
            process.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": identity,
                        "method": method,
                        "params": {"_meta": meta, **arguments},
                    }
                )
                + "\n"
            )
            process.stdin.flush()
            ready, _, _ = select.select([process.stdout], [], [], 15)
            assert ready, "MCP stdio response timed out"
            line = process.stdout.readline()
            reply = json.loads(line)
            assert reply["jsonrpc"] == "2.0" and reply["id"] == identity
            assert "result" in reply and "error" not in reply
            assert "ATHLETE LEDGER" not in line
        trailing, stderr = process.communicate(timeout=10)
        assert trailing == ""
        assert process.returncode == 0
        assert "SYNTHETIC-PRIVATE" not in stderr
        assert "SELECT " not in stderr and "Traceback" not in stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_cli_missing_database_fails_on_stderr_without_creating_file(tmp_path) -> None:
    """Exit nonzero without protocol output or accidental empty-database creation."""
    path = tmp_path / "missing.db"
    config = tmp_path / "synthetic-missing.env"
    config.write_text(f"ATHLETE_LEDGER_DB_URL=sqlite:///{path}\n")
    result = subprocess.run(
        ["athlete-ledger-mcp", "--env-file", str(config)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1 and result.stdout == ""
    assert "Invalid database settings" in result.stderr
    assert str(path) not in result.stderr
    assert not path.exists()


def test_cli_reader_validation_names_only(tmp_path, capsys) -> None:
    """Report missing reader names while ignoring owner/provider secrets in a synthetic file."""
    path = tmp_path / "synthetic-oracle.env"
    path.write_text(
        "ATHLETE_LEDGER_DB_DSN=synthetic_low\n"
        "ATHLETE_LEDGER_DB_USER=SYNTHETIC-PRIVATE-OWNER\n"
        "ATHLETE_LEDGER_DB_PASSWORD=SYNTHETIC-PRIVATE-PASSWORD\n"
        "GARMIN_PERSONAL_API_KEY=SYNTHETIC-PRIVATE-KEY\n"
        "ATHLETE_LEDGER_DB_WALLET_DIR=/synthetic/wallet\n"
        "ATHLETE_LEDGER_DB_WALLET_PASSWORD=synthetic-wallet-password\n"
    )
    assert run(["--env-file", str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Invalid database settings: ATHLETE_LEDGER_RO_DB_PASSWORD, ATHLETE_LEDGER_RO_DB_USER.\n"
    )


def test_cli_default_env_and_names_setting(ledger_file, tmp_path, monkeypatch) -> None:
    """Load the default cwd file and the name opt-in without starting a real transport."""
    (tmp_path / ".env").write_text(
        f'ATHLETE_LEDGER_DB_URL="sqlite:///{ledger_file}"\n'
        "ATHLETE_LEDGER_MCP_INCLUDE_ACTIVITY_NAMES=true\n"
    )
    from unittest.mock import Mock

    factory = Mock()
    monkeypatch.setattr("athlete_ledger_mcp.cli.create_server", factory)
    assert run([]) == 0
    assert factory.call_args.kwargs["include_activity_names"] is True
    factory.return_value.run.assert_called_once_with(transport="stdio")


@pytest.mark.parametrize("code", ["ORA-01017", "DPY-6005", None])
def test_startup_connectivity_failure_is_sanitized(
    ledger_file, tmp_path, monkeypatch, capsys, code
) -> None:
    """Fail startup on stderr without exposing driver SQL, bound values or traceback."""
    from unittest.mock import Mock

    from sqlalchemy.exc import OperationalError

    settings = synthetic_env_file(tmp_path, ledger_file)
    engine = Mock()
    engine.connect.side_effect = OperationalError(
        "SYNTHETIC-PRIVATE-SQL",
        {"secret": "SYNTHETIC-PRIVATE-PASSWORD"},
        Exception(f"{code or 'driver failure'} SYNTHETIC-PRIVATE-DRIVER"),
    )
    monkeypatch.setattr("athlete_ledger_mcp.cli.create_reader_engine", Mock(return_value=engine))
    assert run(["--env-file", str(settings)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"Database operation failed{': ' + code if code else ''}.\n"
    engine.dispose.assert_called_once()
