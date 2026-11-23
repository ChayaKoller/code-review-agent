from config_utils import get_nested


def test_existing_path():
    assert get_nested({"a": {"b": 5}}, "a.b") == 5


def test_missing_path_returns_default():
    assert get_nested({"a": {"b": 5}}, "a.c", default=0) == 0


def test_missing_path_returns_none_by_default():
    assert get_nested({}, "x.y") is None
