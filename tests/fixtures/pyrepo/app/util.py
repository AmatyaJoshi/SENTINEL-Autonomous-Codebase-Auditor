def split_header(header: str) -> tuple[str, str]:
    parts = header.split(" ", 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def read_config(path):
    f = open(path)  # resource leak
    try:
        return f.read()
    except Exception:
        pass
