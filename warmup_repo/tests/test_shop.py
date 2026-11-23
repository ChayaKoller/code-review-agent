from shop import average_price


def test_average_ignores_missing_price():
    products = [{"price": 10}, {"price": None}, {"price": 20}]
    assert average_price(products) == 15


def test_average_simple():
    assert average_price([{"price": 4}, {"price": 6}]) == 5
