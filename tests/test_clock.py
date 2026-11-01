import time

from xianyu_core.clock import beijing_time


def test_beijing_time_ignores_local_timezone(monkeypatch):
    # 1761800000 是北京时间 2025-10-30 12:53:20，电脑在洛杉矶也一样
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    try:
        assert beijing_time(1761800000) == "2025-10-30 12:53"
        assert beijing_time(1761800000, "%m-%d %H:%M:%S") == "10-30 12:53:20"
    finally:
        monkeypatch.undo()
        time.tzset()
