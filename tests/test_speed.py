"""v12: קיצור זמן. קריאות במקביל, ורמת חשיבה נפרדת לשלב החיפוש (ניתנת להגדרה)."""
import threading
import time

import langchain_google_genai

from core import config
from core.llm import Reviewer
from core.nodes import parallel_map


def test_parallel_map_keeps_order(monkeypatch):
    monkeypatch.setattr(config, "MAX_PARALLEL_CALLS", 4)

    def slow(x):
        time.sleep(0.05 * (5 - x))  # הראשון מסיים אחרון
        return x * 10

    assert parallel_map(slow, [1, 2, 3, 4]) == [10, 20, 30, 40]


def test_parallel_map_really_runs_together(monkeypatch):
    monkeypatch.setattr(config, "MAX_PARALLEL_CALLS", 4)
    running, peak, lock = [0], [0], threading.Lock()

    def call(x):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.1)
        with lock:
            running[0] -= 1
        return x

    parallel_map(call, [1, 2, 3, 4])
    assert peak[0] == 4  # ארבע קריאות היו פתוחות באותו רגע


def test_parallel_map_one_means_sequential(monkeypatch):
    monkeypatch.setattr(config, "MAX_PARALLEL_CALLS", 1)
    seen = []
    parallel_map(seen.append, [1, 2, 3])
    assert seen == [1, 2, 3]


def test_low_thinking_only_for_find(monkeypatch):
    made = []

    class FakeChat:
        def __init__(self, **kwargs):
            made.append(kwargs)

        def with_structured_output(self, *a, **k):
            return self

    monkeypatch.setattr(langchain_google_genai, "ChatGoogleGenerativeAI", FakeChat)
    monkeypatch.setattr(config, "FIND_THINKING_LEVEL", "low")
    monkeypatch.setattr(config, "CHECK_MODEL_NAME", None)
    Reviewer("m")
    levels = sorted(str(k.get("thinking_level")) for k in made)
    assert levels == ["None", "low"]  # מודל אחד לחיפוש (low), ואחד לתיקון ולמאמת (ברירת מחדל)
