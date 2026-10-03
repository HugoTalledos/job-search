import plistlib
import sys

import os
import shutil
import subprocess
import time

import pytest

from scripts.macos import install_schedule


def test_collector_plist_runs_dedicated_script_at_three_default_times():
    times = install_schedule.parse_times(["08:00", "14:00", "22:00"])

    plist = install_schedule.build_plist("com.test.job-search-collector", times, component="collector")

    assert plist["ProgramArguments"] == ["/bin/bash", str(install_schedule.REPO / "scripts/macos/run_collector.sh")]
    assert plist["StartCalendarInterval"] == [
        {"Hour": 8, "Minute": 0}, {"Hour": 14, "Minute": 0}, {"Hour": 22, "Minute": 0},
    ]


def test_collector_dry_run_prints_installable_plist(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["install_schedule.py", "--component", "collector", "--dry-run"])

    assert install_schedule.main() == 0

    plist = plistlib.loads(capsys.readouterr().out.encode())
    assert plist["Label"].endswith("job-search-collector")
    assert plist["ProgramArguments"][1].endswith("run_collector.sh")


def test_scheduled_runner_waits_for_shared_lock_then_runs(tmp_path):
    runner, command = "run_collector.sh", "local_collector"
    scripts = tmp_path / "scripts" / "macos"
    scripts.mkdir(parents=True)
    shutil.copy2(install_schedule.REPO / "scripts" / "macos" / runner, scripts / runner)
    python = tmp_path / ".venv-collector" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\necho "$*" >> "$TEST_RUN_LOG"\n')
    python.chmod(0o755)
    lock = tmp_path / ".run.lock"
    lock.mkdir()
    run_log = tmp_path / "calls.log"
    process = subprocess.Popen(["/bin/bash", str(scripts / runner)], env={**os.environ, "TEST_RUN_LOG": str(run_log)})
    try:
        time.sleep(0.3)
        assert process.poll() is None
        lock.rmdir()
        assert process.wait(timeout=5) == 0
        assert f"-m {command}" in run_log.read_text()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_schedule_installs_only_the_collector():
    plist = install_schedule.build_plist(install_schedule.default_label(), [{"Hour": 8, "Minute": 0}])
    assert plist["ProgramArguments"][-1].endswith("run_collector.sh")
