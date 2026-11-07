from pathlib import Path


def set_env_value(path: Path, key: str, value: str) -> None:
    """Sets KEY=value in a dotenv file, keeping every other line and the file's mode.

    The file is rewritten in place and never replaced: in the auth container it is a single-file
    bind mount, and renaming a temporary file onto a mount point fails with EBUSY. Writing in place
    also keeps the mode and the owner. The value is never logged.
    """
    if "\n" in value or "\r" in value:
        raise ValueError("an .env value cannot hold a line break")
    with path.open("r+", encoding="utf-8") as env_file:
        updated = _with_key_set(env_file.read().splitlines(), key, value)
        env_file.seek(0)
        env_file.truncate()
        env_file.write("".join(f"{line}\n" for line in updated))


def _with_key_set(lines: list[str], key: str, value: str) -> list[str]:
    setting = f"{key}={value}"
    prefix = f"{key}="
    first_match = next((i for i, line in enumerate(lines) if line.startswith(prefix)), None)
    if first_match is None:
        return [*lines, setting]
    # A later duplicate would override the new value when dotenv reads the file, so it goes.
    return [
        setting if i == first_match else line
        for i, line in enumerate(lines)
        if i == first_match or not line.startswith(prefix)
    ]
