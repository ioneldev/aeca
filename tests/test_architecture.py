"""The layer rules, enforced by reading every module's imports."""

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
PACKAGE = "aeca"

ALWAYS_FORBIDDEN = ("aeca.adapters", "aeca.tools", "googleapiclient", "google", "anthropic")
THIRD_PARTY_ALLOWED = frozenset({"pydantic"})
SDK_MODULES = frozenset({"anthropic", "httpx2", "httpx"})


@dataclass(frozen=True, slots=True)
class Layer:
    folder: str
    may_import_from_aeca: tuple[str, ...]


DOMAIN = Layer("domain", ("aeca.domain",))
APPLICATION = Layer("application", ("aeca.domain", "aeca.application"))


def imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def is_inside(module: str, package: str) -> bool:
    return module == package or module.startswith(f"{package}.")


def breaks_layer(module: str, layer: Layer) -> bool:
    if any(is_inside(module, forbidden) for forbidden in ALWAYS_FORBIDDEN):
        return True
    top_level = module.split(".")[0]
    if top_level == PACKAGE:
        return not any(is_inside(module, allowed) for allowed in layer.may_import_from_aeca)
    return top_level not in sys.stdlib_module_names and top_level not in THIRD_PARTY_ALLOWED


def layer_violations(root: Path, layer: Layer) -> list[str]:
    violations = []
    for path in sorted((root / PACKAGE / layer.folder).rglob("*.py")):
        for module in sorted(imported_modules(path.read_text(encoding="utf-8"))):
            if breaks_layer(module, layer):
                violations.append(f"{path.relative_to(root)} imports {module}")
    return violations


def sdk_leaks(root: Path) -> list[str]:
    leaks = []
    for path in sorted((root / PACKAGE).rglob("*.py")):
        if is_inside(".".join(path.relative_to(root).with_suffix("").parts), "aeca.adapters"):
            continue
        for module in sorted(imported_modules(path.read_text(encoding="utf-8"))):
            if module.split(".")[0] in SDK_MODULES:
                leaks.append(f"{path.relative_to(root)} imports {module}")
    return leaks


def test_the_scanned_layers_exist() -> None:
    assert (SOURCE_ROOT / PACKAGE / "domain").is_dir()
    assert any((SOURCE_ROOT / PACKAGE / "application").rglob("*.py"))


def test_domain_imports_only_the_standard_library_pydantic_and_itself() -> None:
    assert layer_violations(SOURCE_ROOT, DOMAIN) == []


def test_application_imports_only_domain_the_standard_library_and_pydantic() -> None:
    assert layer_violations(SOURCE_ROOT, APPLICATION) == []


def test_the_model_sdk_and_its_http_library_are_imported_only_under_adapters() -> None:
    assert sdk_leaks(SOURCE_ROOT) == []


def write_module(root: Path, relative: str, source: str) -> None:
    path = root / PACKAGE / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)


def test_the_scanner_catches_an_adapter_import_in_application(tmp_path: Path) -> None:
    write_module(tmp_path, "application/bad.py", "from aeca.adapters import env_file\n")
    assert layer_violations(tmp_path, APPLICATION) == [
        "aeca/application/bad.py imports aeca.adapters"
    ]


def test_the_scanner_catches_a_third_party_import_in_domain(tmp_path: Path) -> None:
    write_module(tmp_path, "domain/bad.py", "import yaml\nimport anthropic.types\n")
    assert len(layer_violations(tmp_path, DOMAIN)) == 2


def test_the_scanner_catches_domain_reaching_up_into_application(tmp_path: Path) -> None:
    write_module(tmp_path, "domain/bad.py", "from aeca.application.ports import Clock\n")
    assert layer_violations(tmp_path, DOMAIN) != []


def test_the_scanner_accepts_the_allowed_imports(tmp_path: Path) -> None:
    write_module(
        tmp_path,
        "application/good.py",
        "import json\nfrom pydantic import BaseModel\nfrom aeca.domain.labels import Label\n"
        "from aeca.application import ports\n",
    )
    assert layer_violations(tmp_path, APPLICATION) == []


def test_the_scanner_catches_an_sdk_import_outside_adapters(tmp_path: Path) -> None:
    write_module(tmp_path, "settings.py", "import httpx2\n")
    write_module(tmp_path, "adapters/ok.py", "import anthropic\n")
    assert sdk_leaks(tmp_path) == ["aeca/settings.py imports httpx2"]
