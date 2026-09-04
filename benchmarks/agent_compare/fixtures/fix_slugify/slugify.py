def slugify(value: str) -> str:
    """Convert a display name into a lowercase dash-separated slug."""

    return value.strip().replace(" ", "-")
