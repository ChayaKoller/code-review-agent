def paginate(items, page, page_size):
    """Return the items of a 1-based page number."""
    start = (page - 1) * page_size
    end = start + page_size
    return items[start:end]
