"""Architecture rules from section 9.2 of docs/SPEC.md, checked on the source."""

import ast
from collections.abc import Iterator
from enum import StrEnum
import io
from pathlib import Path
import tokenize

import pytest

from custom_components.energyid_import import const

ROOT = Path(__file__).parent.parent
PACKAGE = ROOT / "custom_components" / "energyid_import"
TESTS = ROOT / "tests"
PACKAGE_FILES = sorted(PACKAGE.glob("*.py"))
SOURCE_FILES = PACKAGE_FILES + sorted(TESTS.glob("*.py"))

# Built from parts so this file does not match its own search.
SUPPRESSIONS = ("no" + "qa", "type" + ": ignore", "pragma" + ": no cover")

# Internal modules each module may import; anything missing here imports nothing.
ALLOWED_IMPORTS: dict[str, frozenset[str]] = {
    "const": frozenset(),
    "api": frozenset({"const"}),
    "statistics": frozenset({"const"}),
    "coordinator": frozenset({"const", "api", "statistics"}),
    "entity": frozenset({"const", "api", "statistics", "coordinator"}),
    "sensor": frozenset({"const", "api", "statistics", "coordinator", "entity"}),
    "button": frozenset({"const", "api", "statistics", "coordinator", "entity"}),
    "services": frozenset(
        {"const", "api", "statistics", "coordinator", "entity", "dashboard"}
    ),
    "dashboard": frozenset({"const", "api", "statistics", "coordinator", "entity"}),
    "diagnostics": frozenset({"const", "api", "statistics", "coordinator", "entity"}),
    "config_flow": frozenset({"const", "api"}),
    "__init__": frozenset(
        {"const", "api", "statistics", "coordinator", "services", "entity"}
    ),
}
FORBIDDEN_CALLS = frozenset(
    {"create_task", "ensure_future", "gather", "async_create_task"}
)
CLOCK_CALLS = frozenset({"now", "utcnow", "today", "time", "monotonic", "perf_counter"})
CLOCK_MODULES = frozenset({"time", "homeassistant"})
OVERRIDDEN_PROPERTIES = frozenset({"state", "name", "unit_of_measurement"})
MUTABLE_NODES = (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)
MUTABLE_FACTORIES = frozenset({"list", "dict", "set", "defaultdict"})


def parse(path: Path) -> ast.Module:
    """Parse one source file."""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def module_name(path: Path) -> str:
    """Return the module name of a package file."""
    return path.stem


def internal_imports(tree: ast.Module) -> set[str]:
    """Return the sibling modules a package module imports."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            if node.module is None:
                found.update(alias.name for alias in node.names)
            else:
                found.add(node.module.split(".")[0])
    return found


def imported_roots(tree: ast.Module) -> set[str]:
    """Return the top-level names of all absolute imports."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def is_mutable(node: ast.expr | None) -> bool:
    """Return whether an expression builds a mutable container."""
    if isinstance(node, MUTABLE_NODES):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in MUTABLE_FACTORIES
    )


def dataclass_keywords(node: ast.ClassDef) -> dict[str, bool] | None:
    """Return the keyword flags of a @dataclass decorator, or None."""
    for decorator in node.decorator_list:
        call = decorator if isinstance(decorator, ast.Call) else None
        target = call.func if call else decorator
        if isinstance(target, ast.Name) and target.id == "dataclass":
            keywords = call.keywords if call else []
            return {
                kw.arg: isinstance(kw.value, ast.Constant) and kw.value.value is True
                for kw in keywords
                if kw.arg is not None
            }
    return None


def string_constants(tree: ast.Module) -> Iterator[str]:
    """Yield every string literal that is not a docstring."""
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            yield node.value


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: p.name)
def test_no_suppressions(path: Path) -> None:
    """No lint, type or coverage suppression comments anywhere."""
    source = path.read_text(encoding="utf-8")
    comments = [
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    ]
    found = [c for c in comments if any(s in c for s in SUPPRESSIONS)]
    assert found == []


@pytest.mark.parametrize("path", PACKAGE_FILES, ids=lambda p: p.name)
def test_import_direction(path: Path) -> None:
    """Modules only import their allowed siblings; nothing imports config_flow."""
    name = module_name(path)
    assert name in ALLOWED_IMPORTS, f"{name} has no import rule"
    imports = internal_imports(parse(path))
    assert imports <= ALLOWED_IMPORTS[name]
    assert "config_flow" not in imports


@pytest.mark.parametrize("path", PACKAGE_FILES, ids=lambda p: p.name)
def test_isolated_modules(path: Path) -> None:
    """The api and statistics modules stay free of HA; only api uses aiohttp."""
    roots = imported_roots(parse(path))
    if module_name(path) in {"api", "statistics"}:
        assert "homeassistant" not in roots
    if module_name(path) != "api":
        assert "aiohttp" not in roots


@pytest.mark.parametrize("path", PACKAGE_FILES, ids=lambda p: p.name)
def test_no_hass_data_and_no_loose_tasks(path: Path) -> None:
    """Runtime state lives in entry.runtime_data and no task is spawned."""
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.Attribute):
            assert node.attr != "data" or not (
                isinstance(node.value, ast.Name) and node.value.id == "hass"
            ), f"hass.data in {path.name}:{node.lineno}"
            assert node.attr not in FORBIDDEN_CALLS, f"{node.attr} in {path.name}"


@pytest.mark.parametrize("path", PACKAGE_FILES, ids=lambda p: p.name)
def test_dataclasses_are_frozen(path: Path) -> None:
    """Dataclasses are frozen and keyword-only; all but descriptions use slots."""
    for node in ast.walk(parse(path)):
        if not isinstance(node, ast.ClassDef):
            continue
        flags = dataclass_keywords(node)
        if flags is None:
            continue
        assert flags.get("frozen"), node.name
        assert flags.get("kw_only"), node.name
        if not node.name.endswith("EntityDescription"):
            assert flags.get("slots"), node.name


@pytest.mark.parametrize("path", PACKAGE_FILES, ids=lambda p: p.name)
def test_no_mutable_shared_state(path: Path) -> None:
    """No mutable module-level values or class attributes."""
    tree = parse(path)
    scopes: list[list[ast.stmt]] = [tree.body]
    scopes += [n.body for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    for body in scopes:
        for stmt in body:
            value = stmt.value if isinstance(stmt, ast.Assign | ast.AnnAssign) else None
            assert not is_mutable(value), f"{path.name}:{stmt.lineno}"


def test_constants_are_final() -> None:
    """Every module-level constant in const.py is Final or an enum."""
    for stmt in parse(PACKAGE / "const.py").body:
        assert not isinstance(stmt, ast.Assign), f"const.py:{stmt.lineno} lacks Final"
        if isinstance(stmt, ast.AnnAssign):
            assert "Final" in ast.unparse(stmt.annotation)


def test_enum_values_only_in_const() -> None:
    """Metric, unit and interval strings appear only in const.py."""
    enums: list[type[StrEnum]] = [const.Metric, const.Unit, const.Interval]
    values = {member.value for enum in enums for member in enum}
    for path in PACKAGE_FILES:
        if path.name == "const.py":
            continue
        literals = set(string_constants(parse(path)))
        assert literals.isdisjoint(values), path.name


def test_statistics_does_not_read_the_clock() -> None:
    """statistics.py receives `now` as an argument and never calls the clock."""
    tree = parse(PACKAGE / "statistics.py")
    assert imported_roots(tree).isdisjoint(CLOCK_MODULES)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else None
        name = func.id if isinstance(func, ast.Name) else name
        assert name not in CLOCK_CALLS, f"statistics.py:{node.lineno}"


def test_api_returns_only_dataclasses() -> None:
    """Public functions of api.py return dataclasses, lists of them or None."""
    tree = parse(PACKAGE / "api.py")
    dataclasses = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and dataclass_keywords(n) is not None
    }
    allowed = dataclasses | {"list", "tuple", "date", "None", "bool"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name.startswith("_") or node.returns is None:
            continue
        names = {
            n.id if isinstance(n, ast.Name) else str(n.value)
            for n in ast.walk(node.returns)
            if isinstance(n, ast.Name | ast.Constant)
        }
        assert names <= allowed, f"{node.name} returns {ast.unparse(node.returns)}"


@pytest.mark.parametrize("path", PACKAGE_FILES, ids=lambda p: p.name)
def test_entities_only_set_attr(path: Path) -> None:
    """Entities never override state, name or unit_of_measurement."""
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.ClassDef):
            defined = {
                f.name
                for f in node.body
                if isinstance(f, ast.FunctionDef | ast.AsyncFunctionDef)
            }
            assert defined.isdisjoint(OVERRIDDEN_PROPERTIES), node.name


@pytest.mark.parametrize("path", sorted(TESTS.glob("*.py")), ids=lambda p: p.name)
def test_tests_only_mock_http(path: Path) -> None:
    """Tests never patch the integration or the recorder."""
    for node in ast.walk(parse(path)):
        if not isinstance(node, ast.Call):
            continue
        func = ast.unparse(node.func)
        if not func.endswith(("patch", "patch.object", "patch.dict")):
            continue
        target = ast.unparse(node.args[0]) if node.args else ""
        assert "custom_components" not in target, f"{path.name}:{node.lineno}"
        assert "recorder" not in target, f"{path.name}:{node.lineno}"
