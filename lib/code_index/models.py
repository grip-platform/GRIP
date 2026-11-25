"""Data models for the code index.

Contains the Symbol dataclass, FrameworkType enum (with DjangoType alias),
used across all modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class FrameworkType(Enum):
    """Framework component classification."""
    # Django
    MODEL = "model"
    VIEW = "view"
    SERIALIZER = "serializer"
    ADMIN = "admin"
    COMMAND = "command"
    TASK = "task"
    FORM = "form"
    MANAGER = "manager"
    MIDDLEWARE = "middleware"
    # Flask
    BLUEPRINT = "blueprint"
    EXTENSION = "extension"
    # FastAPI
    ROUTER = "router"
    DEPENDENCY = "dependency"
    # Generic
    CONTROLLER = "controller"
    SERVICE = "service"
    REPOSITORY = "repository"
    # None
    NONE = "none"


# Backward-compatible alias
DjangoType = FrameworkType


@dataclass
class Symbol:
    """A Python symbol extracted via AST analysis."""
    name: str
    qualified_name: str
    symbol_type: str  # "class", "function", "method", "module"
    file_path: str
    line_start: int
    line_end: int
    signature: str = ""
    docstring: str = ""
    decorators: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)
    parent: str = ""  # Parent class for methods
    framework_type: FrameworkType = FrameworkType.NONE
    project: str = ""

    @property
    def django_type(self) -> FrameworkType:
        """Backward-compatible alias for framework_type."""
        return self.framework_type

    @property
    def location(self) -> str:
        """Format as file:line for navigation."""
        return f"{self.file_path}:{self.line_start}"

    def to_dict(self) -> dict:
        """Serialise for JSON output."""
        return {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "type": self.symbol_type,
            "file": self.file_path,
            "lines": f"{self.line_start}-{self.line_end}",
            "signature": self.signature,
            "docstring": self.docstring[:200] if self.docstring else "",
            "decorators": self.decorators,
            "bases": self.bases,
            "parent": self.parent,
            "framework_type": self.framework_type.value,
            "django_type": self.framework_type.value,  # backward compat
        }
