from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_cli(*arguments: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "flight_recorder", *arguments],
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_smoke_all_subcommands(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    record = run_cli("record", "stale-update", "--runs-dir", str(runs_dir))
    assert record.returncode == 0, record.stderr
    run_id = record.stdout.split()[1]

    replay = run_cli("replay", run_id, "--runs-dir", str(runs_dir))
    assert replay.returncode == 0, replay.stderr
    assert "REPLAY MATCH" in replay.stdout

    branch = run_cli("branch", run_id, "--at", "5", "--runs-dir", str(runs_dir))
    assert branch.returncode == 0, branch.stderr
    branch_id = branch.stdout.split()[1]

    counterfactual = run_cli(
        "counterfactual", run_id, "--at", "5", "--seed", "29", "--prompt", "Try another path",
        "--runs-dir", str(runs_dir),
    )
    assert counterfactual.returncode == 0, counterfactual.stderr
    counterfactual_id = counterfactual.stdout.split()[1]

    difference = run_cli("diff", run_id, counterfactual_id, "--runs-dir", str(runs_dir))
    assert difference.returncode == 0, difference.stderr
    assert "Divergent positions" in difference.stdout

    fixtures = run_cli("fixtures")
    assert fixtures.returncode == 0, fixtures.stderr
    assert "scripted fixture" in fixtures.stdout

    install = run_cli("fixtures", "--install", "--runs-dir", str(runs_dir))
    assert install.returncode == 0, install.stderr
    assert "INSTALLED" in install.stdout

    output = tmp_path / "timeline.html"
    viewer = run_cli(
        "viewer", branch_id, "--compare", run_id, "--output", str(output), "--runs-dir", str(runs_dir)
    )
    assert viewer.returncode == 0, viewer.stderr
    html = output.read_text(encoding="utf-8")
    assert "scripted fixtures" in html
    assert "State diff" in html


def test_cli_unknown_run_reports_error() -> None:
    result = run_cli("replay", "missing-run")
    assert result.returncode == 2
    assert "ERROR run not found" in result.stderr


def test_bundled_fixture_command_lists_exactly_three() -> None:
    result = run_cli("fixtures")
    assert result.returncode == 0
    rows = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(rows) == 3
