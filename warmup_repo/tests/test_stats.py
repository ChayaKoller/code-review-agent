from stats import moving_sums


def test_moving_sums_includes_last_window():
    assert moving_sums([1, 2, 3, 4], 2) == [3, 5, 7]


def test_moving_sums_window_equals_length():
    assert moving_sums([1, 2, 3], 3) == [6]
