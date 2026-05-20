from __future__ import annotations

import ast
import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field

try:
    from RestrictedPython import compile_restricted as _compile_restricted
except ImportError as _exc:
    raise RuntimeError("RestrictedPython not installed — run `uv sync`") from _exc

ALLOWED_IMPORTS_DEFAULT = ["numpy", "pandas", "ta", "math", "decimal", "collections", "itertools"]
PROHIBITED_PATTERNS = [
    "network access",
    "file I/O",
    "subprocess",
    "__import__",
    "eval",
    "exec",
    "os module",
    "sys module",
    "socket",
]

_PROHIBITED_IMPORTS = {
    "builtins",
    "io",
    "os",
    "pathlib",
    "shutil",
    "socket",
    "subprocess",
    "sys",
}
_PROHIBITED_CALLS = {"__import__", "eval", "exec", "open", "compile", "input"}
_PROHIBITED_ATTRS = {
    "__class__",
    "__mro__",
    "__subclasses__",
    "__bases__",
    "__globals__",
    "__dict__",
    "__code__",
    "__closure__",
    "__wrapped__",
    "__init_subclass__",
    "__func__",
    "__self__",
}
_ALLOWED_APP_IMPORTS = {"app.bot.base"}


@dataclass(frozen=True)
class SandboxValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


def compute_source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


class SandboxValidator:
    def __init__(self, allowed_imports: Iterable[str] | None = None) -> None:
        self.allowed_imports = set(allowed_imports or ALLOWED_IMPORTS_DEFAULT)

    def validate_code(self, source: str) -> SandboxValidationResult:
        errors: list[str] = []
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            return SandboxValidationResult(ok=False, errors=[f"syntax error: {exc.msg}"])

        try:
            _compile_restricted(source, "<strategy>", "exec")
        except Exception as exc:
            return SandboxValidationResult(ok=False, errors=[f"restricted compile error: {exc}"])

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self._validate_import(node.names, errors)
            elif isinstance(node, ast.ImportFrom):
                self._validate_module_name(node.module or "", errors)
            elif isinstance(node, ast.Call):
                self._validate_call(node, errors)
            elif isinstance(node, ast.Attribute):
                if node.attr in _PROHIBITED_ATTRS:
                    errors.append(f"prohibited attribute access: {node.attr}")
            elif isinstance(node, ast.Name):
                if node.id in _PROHIBITED_CALLS:
                    errors.append(f"prohibited name: {node.id}")

        return SandboxValidationResult(ok=not errors, errors=errors)

    def validate(self, source: str) -> SandboxValidationResult:
        return self.validate_code(source)

    def _validate_import(self, aliases: list[ast.alias], errors: list[str]) -> None:
        for alias in aliases:
            self._validate_module_name(alias.name, errors)

    def _validate_module_name(self, module: str, errors: list[str]) -> None:
        root_module = module.split(".", maxsplit=1)[0]
        if module in _ALLOWED_APP_IMPORTS:
            return
        if root_module in _PROHIBITED_IMPORTS:
            errors.append(f"prohibited import: {root_module}")
        elif root_module not in self.allowed_imports:
            errors.append(f"import not allowed: {root_module}")

    def _validate_call(self, node: ast.Call, errors: list[str]) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in _PROHIBITED_CALLS:
            errors.append(f"prohibited call: {func.id}")
        elif isinstance(func, ast.Attribute) and func.attr in _PROHIBITED_ATTRS:
            errors.append(f"prohibited call: {func.attr}")
