import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1]


def test_no_core_module_imports_a_pack() -> None:
    # ADR-0010: logistics and country specifics can be lifted out without touching the core.
    offenders = []
    for path in (APP / "modules").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
            if any(name == "app.packs" or name.startswith("app.packs.") for name in names):
                offenders.append(str(path.relative_to(APP)))
    assert offenders == []
