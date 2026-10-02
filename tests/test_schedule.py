import plistlib
import sys

from scripts.macos import install_schedule


def test_collector_plist_runs_dedicated_script_at_three_default_times():
    times = install_schedule.parse_times(["08:00", "14:00", "22:00"])

    plist = install_schedule.build_plist("com.test.job-search-collector", times, component="collector")

    assert plist["ProgramArguments"] == ["/bin/bash", str(install_schedule.REPO / "scripts/macos/run_collector.sh")]
    assert plist["StartCalendarInterval"] == [
        {"Hour": 8, "Minute": 0}, {"Hour": 14, "Minute": 0}, {"Hour": 22, "Minute": 0},
    ]


def test_collector_and_legacy_schedules_have_distinct_default_labels():
    assert install_schedule.default_label(component="collector") != install_schedule.default_label()


def test_collector_dry_run_prints_installable_plist(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["install_schedule.py", "--component", "collector", "--dry-run"])

    assert install_schedule.main() == 0

    plist = plistlib.loads(capsys.readouterr().out.encode())
    assert plist["Label"].endswith("job-search-collector")
    assert plist["ProgramArguments"][1].endswith("run_collector.sh")
