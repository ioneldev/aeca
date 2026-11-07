import stat
from pathlib import Path

from aeca.adapters.copy_id_file import COPY_ID_FILE_NAME, load_or_create_copy_id


def test_the_id_is_made_once_and_read_back_on_every_later_start(tmp_path: Path) -> None:
    first = load_or_create_copy_id(tmp_path)

    assert load_or_create_copy_id(tmp_path) == first
    assert len(first) == 32 and first.isalnum()


def test_another_data_directory_is_another_copy(tmp_path: Path) -> None:
    assert load_or_create_copy_id(tmp_path / "one") != load_or_create_copy_id(tmp_path / "two")


def test_an_empty_file_is_replaced_by_a_new_id(tmp_path: Path) -> None:
    (tmp_path / COPY_ID_FILE_NAME).write_text("\n")

    assert load_or_create_copy_id(tmp_path)


def test_the_file_is_readable_by_its_owner_only(tmp_path: Path) -> None:
    load_or_create_copy_id(tmp_path)

    assert stat.S_IMODE((tmp_path / COPY_ID_FILE_NAME).stat().st_mode) == 0o600
