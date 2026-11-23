def moving_sums(values, window):
    """Sum of every consecutive group of `window` items."""
    sums = []
    for i in range(len(values) - window):
        sums.append(sum(values[i:i + window]))
    return sums
