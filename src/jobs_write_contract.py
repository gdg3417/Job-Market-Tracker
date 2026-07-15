from __future__ import annotations

import argparse
import ast
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

DIRECT_METHODS = {
    "add_cols",
    "add_rows",
    "add_worksheet",
    "append_cell",
    "append_cells",
    "append_dimension",
    "append_row",
    "append_rows",
    "batch_update",
    "clear",
    "delete_columns",
    "delete_dimension",
    "delete_rows",
    "format",
    "freeze",
    "insert_column",
    "insert_columns",
    "insert_row",
    "insert_rows",
    "resize",
    "set_basic_filter",
    "update",
    "update_cell",
    "update_cells",
    "values_append",
}
DIRECT_REQUEST_KEYS = {"updateCells", "appendCells", "appendDimension"}
SHEET_SOURCE_METHODS = {"get_worksheet", "ensure_worksheet", "worksheet", "add_worksheet", "open_by_key"}
SHEET_NAME_HINTS = {"worksheet", "workbook", "spreadsheet", "sheet", "tab", "guide", "ws"}


@dataclass(frozen=True, slots=True)
class DirectWriteOccurrence:
    file: str
    function: str
    operation: str
    line: int
    expression: str

    @property
    def key(self) -> tuple[str, str, str]:
        return self.file, self.function, self.operation

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _DirectWriteVisitor(ast.NodeVisitor):
    def __init__(self, *, relative_path: str) -> None:
        self.relative_path = relative_path
        self.class_stack: list[str] = []
        self.function_stack: list[str] = []
        self.sheet_variables: list[set[str]] = [set()]
        self.occurrences: list[DirectWriteOccurrence] = []

    @property
    def function_name(self) -> str:
        parts = [*self.class_stack, *self.function_stack]
        return ".".join(parts) if parts else "<module>"

    @property
    def current_sheet_variables(self) -> set[str]:
        return self.sheet_variables[-1]

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self.function_stack.append(node.name)
        self.sheet_variables.append(set())
        self.generic_visit(node)
        self.sheet_variables.pop()
        self.function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        self.visit_FunctionDef(node)

    def visit_Assign(self, node: ast.Assign) -> Any:
        if self._is_sheet_source(node.value):
            for target in node.targets:
                self.current_sheet_variables.update(self._target_names(target))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> Any:
        if node.value is not None and self._is_sheet_source(node.value):
            self.current_sheet_variables.update(self._target_names(node.target))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        if isinstance(node.func, ast.Attribute):
            method = node.func.attr
            expression = self._safe_unparse(node.func)
            if method in DIRECT_METHODS and self._looks_like_sheet_object(node.func.value, expression):
                self._record(method, node, expression)
            elif method == "append" and self._is_values_append_chain(node.func.value, expression):
                self._record("values_append", node, expression)
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> Any:
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value in DIRECT_REQUEST_KEYS:
                self._record(key.value, node, key.value)
        self.generic_visit(node)

    def _record(self, operation: str, node: ast.AST, expression: str) -> None:
        occurrence = DirectWriteOccurrence(
            file=self.relative_path,
            function=self.function_name,
            operation=operation,
            line=int(getattr(node, "lineno", 0) or 0),
            expression=expression,
        )
        if occurrence not in self.occurrences:
            self.occurrences.append(occurrence)

    def _is_sheet_source(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in SHEET_SOURCE_METHODS:
                return True
        expression = self._safe_unparse(node).lower()
        return ".workbook" in expression or ".spreadsheet" in expression

    def _target_names(self, target: ast.AST) -> set[str]:
        if isinstance(target, ast.Name):
            return {target.id}
        if isinstance(target, (ast.Tuple, ast.List)):
            names: set[str] = set()
            for item in target.elts:
                names.update(self._target_names(item))
            return names
        return set()

    def _looks_like_sheet_object(self, base: ast.AST, expression: str) -> bool:
        root = self._root_name(base)
        if root and root in self.current_sheet_variables:
            return True
        lowered = expression.lower()
        segments = re.findall(r"[a-z_][a-z0-9_]*", lowered)
        return any(
            segment in SHEET_NAME_HINTS
            or segment.endswith("_worksheet")
            or segment.endswith("_workbook")
            or segment.endswith("_spreadsheet")
            or segment.endswith("_sheet")
            for segment in segments
        )

    def _is_values_append_chain(self, base: ast.AST, expression: str) -> bool:
        lowered = expression.lower()
        return "values" in lowered and ("spreadsheets" in lowered or "sheet" in lowered)

    @staticmethod
    def _root_name(node: ast.AST) -> str:
        current = node
        while isinstance(current, (ast.Attribute, ast.Call, ast.Subscript)):
            if isinstance(current, ast.Attribute):
                current = current.value
            elif isinstance(current, ast.Call):
                current = current.func
            else:
                current = current.value
        return current.id if isinstance(current, ast.Name) else ""

    @staticmethod
    def _safe_unparse(node: ast.AST) -> str:
        try:
            return ast.unparse(node)
        except Exception:
            return node.__class__.__name__


def scan_direct_sheet_writes(source_root: str | Path = "src") -> list[DirectWriteOccurrence]:
    root = Path(source_root)
    project_root = root.parent
    occurrences: list[DirectWriteOccurrence] = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "jobs_write_contract.py":
            continue
        relative_path = path.relative_to(project_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
        visitor = _DirectWriteVisitor(relative_path=relative_path)
        visitor.visit(tree)
        occurrences.extend(visitor.occurrences)
    return sorted(occurrences, key=lambda value: (value.file, value.function, value.operation, value.line))


def load_allowlist(path: str | Path = "config/jobs_write_allowlist.yml") -> list[dict[str, Any]]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    entries = payload.get("entries") or []
    if not isinstance(entries, list):
        raise ValueError("Jobs write allowlist entries must be a list")
    required = {"file", "function", "operation", "worksheet", "reason", "can_write_canonical_data", "guard"}
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise ValueError(f"Jobs write allowlist entry {index} must be a mapping")
        missing = sorted(required - set(raw))
        if missing:
            raise ValueError(f"Jobs write allowlist entry {index} is missing: {', '.join(missing)}")
        if not str(raw.get("reason") or "").strip() or not str(raw.get("guard") or "").strip():
            raise ValueError(f"Jobs write allowlist entry {index} must document a reason and guard")
        normalized.append(dict(raw))
    return normalized


def unallowlisted_occurrences(
    occurrences: Iterable[DirectWriteOccurrence],
    allowlist: Iterable[dict[str, Any]],
) -> list[DirectWriteOccurrence]:
    allowed = {
        (str(entry["file"]), str(entry["function"]), str(entry["operation"]))
        for entry in allowlist
    }
    return [occurrence for occurrence in occurrences if occurrence.key not in allowed]


def audit_write_contract(
    *,
    source_root: str | Path = "src",
    allowlist_path: str | Path = "config/jobs_write_allowlist.yml",
) -> dict[str, Any]:
    occurrences = scan_direct_sheet_writes(source_root)
    allowlist = load_allowlist(allowlist_path)
    unallowlisted = unallowlisted_occurrences(occurrences, allowlist)
    return {
        "status": "healthy" if not unallowlisted else "unsafe",
        "direct_write_occurrences": len(occurrences),
        "allowlist_entries": len(allowlist),
        "unallowlisted_count": len(unallowlisted),
        "unallowlisted": [item.to_dict() for item in unallowlisted],
        "occurrences": [item.to_dict() for item in occurrences],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit direct Google Sheets write paths")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--enforce", action="store_true")
    parser.add_argument("--source-root", default="src")
    parser.add_argument("--allowlist", default="config/jobs_write_allowlist.yml")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = audit_write_contract(source_root=args.source_root, allowlist_path=args.allowlist)
    print(json.dumps(result, indent=2))
    if args.enforce and result["status"] != "healthy":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
