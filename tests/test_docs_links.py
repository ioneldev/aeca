"""Every relative link in the docs resolves to a file, and to a heading when it names one."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOP_LEVEL_PAGES = ("README.md", "DESIGN.md", "AGENTS.md")
EXCLUDED_FOLDERS = ("docs/superpowers", "docs/research")
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
INLINE_CODE = re.compile(r"`[^`\n]*`")
EXTERNAL = ("http://", "https://", "mailto:")


def documentation_pages() -> list[Path]:
    wiki = sorted((REPO_ROOT / "docs").glob("**/*.md"))
    pages = [REPO_ROOT / name for name in TOP_LEVEL_PAGES] + wiki
    return [page for page in pages if not is_excluded(page)]


def is_excluded(page: Path) -> bool:
    relative = page.relative_to(REPO_ROOT).as_posix()
    return any(relative.startswith(f"{folder}/") for folder in EXCLUDED_FOLDERS)


def relative_links_of(page: Path) -> list[str]:
    prose = INLINE_CODE.sub("", FENCE.sub("", page.read_text()))
    return [link for link in LINK.findall(prose) if not link.startswith(EXTERNAL)]


def anchors_of(page: Path) -> set[str]:
    headings = re.findall(r"^#+\s+(.*?)\s*$", page.read_text(), re.MULTILINE)
    return {github_slug(heading) for heading in headings}


def github_slug(heading: str) -> str:
    plain = re.sub(r"[`*_]", "", heading).lower()
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", plain))


def problem_with(page: Path, link: str) -> str | None:
    path_part, _, anchor = link.partition("#")
    target = page if not path_part else (page.parent / path_part).resolve()
    if not target.exists():
        return f"{page.relative_to(REPO_ROOT)}: {link} points to nothing"
    if anchor and target.suffix == ".md" and anchor not in anchors_of(target):
        return f"{page.relative_to(REPO_ROOT)}: {link} names a heading that is not there"
    return None


def test_the_three_top_level_pages_exist() -> None:
    missing = [name for name in TOP_LEVEL_PAGES if not (REPO_ROOT / name).is_file()]
    assert missing == []


def test_every_relative_link_in_the_docs_resolves() -> None:
    problems = [
        problem
        for page in documentation_pages()
        if page.is_file()
        for link in relative_links_of(page)
        if (problem := problem_with(page, link)) is not None
    ]
    assert problems == []


def test_the_superpowers_and_research_folders_are_left_out() -> None:
    relative = {page.relative_to(REPO_ROOT).as_posix() for page in documentation_pages()}
    assert not any(name.startswith(EXCLUDED_FOLDERS) for name in relative)
    assert "docs/decisions/README.md" in relative
