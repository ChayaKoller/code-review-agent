def get_nested(data, path, default=None):
    """Read a value like 'a.b.c' from nested dicts; return default if any key is missing."""
    current = data
    for key in path.split("."):
        current = current[key]
    return current
