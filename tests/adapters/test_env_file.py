import stat
from pathlib import Path

from aeca.adapters.env_file import set_env_value


def test_set_env_value_replaces_the_key_and_keeps_other_lines(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("A=1\nGOOGLE_REFRESH_TOKEN=old\nB=2\n")

    set_env_value(env, "GOOGLE_REFRESH_TOKEN", "new")

    assert env.read_text() == "A=1\nGOOGLE_REFRESH_TOKEN=new\nB=2\n"


def test_set_env_value_appends_a_missing_key_after_a_final_newline(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("A=1")

    set_env_value(env, "GOOGLE_REFRESH_TOKEN", "new")

    assert env.read_text() == "A=1\nGOOGLE_REFRESH_TOKEN=new\n"


def test_set_env_value_keeps_the_file_mode(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("A=1\n")
    env.chmod(0o600)

    set_env_value(env, "B", "2")

    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_set_env_value_writes_in_place_so_a_bind_mounted_file_keeps_its_inode(
    tmp_path: Path,
) -> None:
    env = tmp_path / ".env"
    env.write_text("A=1\n")
    inode_before = env.stat().st_ino

    set_env_value(env, "A", "2")

    assert env.stat().st_ino == inode_before


def test_set_env_value_leaves_a_commented_out_key_alone(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# GOOGLE_REFRESH_TOKEN=old\n")

    set_env_value(env, "GOOGLE_REFRESH_TOKEN", "new")

    assert env.read_text() == "# GOOGLE_REFRESH_TOKEN=old\nGOOGLE_REFRESH_TOKEN=new\n"


def test_set_env_value_does_not_match_a_key_that_only_ends_the_same(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("OTHER_GOOGLE_REFRESH_TOKEN=keep\n")

    set_env_value(env, "GOOGLE_REFRESH_TOKEN", "new")

    assert env.read_text() == "OTHER_GOOGLE_REFRESH_TOKEN=keep\nGOOGLE_REFRESH_TOKEN=new\n"


def test_set_env_value_replaces_only_the_first_of_a_duplicated_key_and_drops_the_rest(
    tmp_path: Path,
) -> None:
    env = tmp_path / ".env"
    env.write_text("K=1\nA=x\nK=2\n")

    set_env_value(env, "K", "new")

    assert env.read_text() == "K=new\nA=x\n"
