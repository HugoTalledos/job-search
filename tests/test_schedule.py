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


def test_wake_times_are_one_minute_before_each_run_wrapping_midnight():
    times = install_schedule.parse_times(["08:00", "14:30", "00:00"])

    assert install_schedule.wake_times(times) == [
        {"Hour": 7, "Minute": 59}, {"Hour": 14, "Minute": 29}, {"Hour": 23, "Minute": 59},
    ]


def test_wake_daemon_runs_at_each_wake_time_and_on_load():
    plist = install_schedule.build_wake_plist("com.test.job-search-collector", install_schedule.parse_times(["08:00"]))

    assert plist["Label"] == "com.test.job-search-collector.wake"
    assert plist["StartCalendarInterval"] == [{"Hour": 7, "Minute": 59}]
    assert plist["RunAtLoad"] is True
    assert plist["ProgramArguments"][:2] == ["/bin/sh", "-c"]


def test_wake_script_schedules_future_wakes_for_today_and_tomorrow(tmp_path):
    calls = tmp_path / "calls.log"
    for tool in ("pmset", "caffeinate"):
        stub = tmp_path / tool
        stub.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{calls}"\n')
        stub.chmod(0o755)
    plist = install_schedule.build_wake_plist("com.test.collector", install_schedule.parse_times(["00:00"]))
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin"}

    subprocess.run(plist["ProgramArguments"], env=env, check=True, timeout=5)

    lines = calls.read_text().splitlines()
    wakes = [line for line in lines if line.startswith("pmset schedule wake")]
    assert wakes == [
        f"pmset schedule wake {time.strftime('%m/%d/%y', time.localtime(time.time() + d * 86400))} 23:59:00 "
        "com.test.collector.wake"
        for d in (0, 1)
        if time.strftime("%H:%M") < "23:59" or d == 1
    ]
    assert lines[-1] == "caffeinate -i -t 300"
    assert "pmset sleepnow" not in lines


def test_collector_runner_keeps_mac_awake_while_running():
    assert 'caffeinate -i "$PYTHON" -m local_collector' in (
        install_schedule.REPO / "scripts/macos/run_collector.sh"
    ).read_text()


def _run_last_wake(tmp_path, idle_seconds):
    calls = tmp_path / "calls.log"
    stubs = {
        "pmset": f'echo "pmset $*" >> "{calls}"',
        "caffeinate": f'echo "caffeinate $*" >> "{calls}"',
        "pgrep": "exit 1",
        "ioreg": f'echo \'    | |   "HIDIdleTime" = {idle_seconds * 1_000_000_000}\'',
    }
    for tool, body in stubs.items():
        stub = tmp_path / tool
        stub.write_text(f"#!/bin/sh\n{body}\n")
        stub.chmod(0o755)
    run_at = time.strftime("%H:%M", time.localtime(time.time() + 60))  # wakes now = last wake of the day
    plist = install_schedule.build_wake_plist("com.test.collector", install_schedule.parse_times([run_at]))
    subprocess.run(plist["ProgramArguments"], env={"PATH": f"{tmp_path}:/usr/bin:/bin"}, check=True, timeout=5)
    return calls.read_text().splitlines()


def test_last_wake_of_day_keeps_awake_seven_minutes_then_sleeps(tmp_path):
    lines = _run_last_wake(tmp_path, idle_seconds=600)

    assert lines[-2:] == ["caffeinate -i -t 420", "pmset sleepnow"]


def test_last_wake_does_not_sleep_while_someone_uses_the_mac(tmp_path):
    lines = _run_last_wake(tmp_path, idle_seconds=5)

    assert lines[-1] == "caffeinate -i -t 420"
