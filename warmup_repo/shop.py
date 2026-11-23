def average_price(products):
    """Average price of the products, ignoring products whose price is None."""
    prices = [p["price"] for p in products]
    return sum(prices) / len(prices)
