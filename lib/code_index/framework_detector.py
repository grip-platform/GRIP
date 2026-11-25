"""Framework component detection via AST base class analysis.

Classifies Python classes as framework components (Django, Flask, FastAPI, etc.)
based on:
1. Base class names (primary signal -- AST-verified)
2. Decorator names (secondary signal)
3. File path conventions (tertiary fallback)

Pluggable via DetectorRegistry: register custom detectors for any framework.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from lib.code_index.models import FrameworkType


@dataclass(frozen=True)
class FrameworkDetector:
    """Configuration for detecting a specific framework's components."""
    name: str
    base_class_rules: list[tuple[list[str], FrameworkType]]
    decorator_rules: dict[str, FrameworkType] = field(default_factory=dict)
    path_hints: dict[str, FrameworkType] = field(default_factory=dict)
    priority: int = 0  # higher = checked first


class DetectorRegistry:
    """Registry for framework component detectors.

    Detectors are checked in priority order (highest first).
    First match wins.
    """

    def __init__(self):
        self._detectors: list[FrameworkDetector] = []

    def register(self, detector: FrameworkDetector) -> None:
        """Register a detector. Higher priority = checked first."""
        self._detectors.append(detector)
        self._detectors.sort(key=lambda d: d.priority, reverse=True)

    def classify(self, class_name: str, bases: list[str],
                 decorators: list[str], file_path: str) -> FrameworkType:
        """Classify using all registered detectors in priority order."""
        for detector in self._detectors:
            result = _classify_with_detector(
                detector, class_name, bases, decorators, file_path,
            )
            if result != FrameworkType.NONE:
                return result
        return FrameworkType.NONE


def _classify_with_detector(
    detector: FrameworkDetector,
    class_name: str,
    bases: list[str],
    decorators: list[str],
    file_path: str,
) -> FrameworkType:
    """Run classification against a single detector's rules."""
    # Tier 1: Base class analysis
    for base in bases:
        simple_base = base.rsplit(".", 1)[-1] if "." in base else base
        for patterns, fw_type in detector.base_class_rules:
            if simple_base in patterns:
                return fw_type

    # Tier 2: Decorator analysis
    for decorator in decorators:
        for pattern, fw_type in detector.decorator_rules.items():
            if decorator == pattern or decorator.endswith(f".{pattern}"):
                return fw_type

    # Tier 3: File path fallback
    normalised = file_path.replace("\\", "/")
    for path_pattern, fw_type in detector.path_hints.items():
        if path_pattern in normalised:
            return fw_type

    return FrameworkType.NONE


# === Built-in Framework Detectors ===

DJANGO_DETECTOR = FrameworkDetector(
    name="django",
    priority=10,
    base_class_rules=[
        (["Model", "AbstractBaseUser", "AbstractUser", "TimeStampedModel",
          "MPTTModel", "PolymorphicModel"], FrameworkType.MODEL),
        (["Serializer", "ModelSerializer", "HyperlinkedModelSerializer",
          "ListSerializer", "BaseSerializer"], FrameworkType.SERIALIZER),
        (["View", "APIView", "ViewSet", "ModelViewSet", "GenericAPIView",
          "CreateAPIView", "ListAPIView", "RetrieveAPIView", "UpdateAPIView",
          "DestroyAPIView", "ListCreateAPIView", "RetrieveUpdateAPIView",
          "RetrieveDestroyAPIView", "RetrieveUpdateDestroyAPIView",
          "GenericViewSet", "ReadOnlyModelViewSet", "TemplateView",
          "ListView", "DetailView", "CreateView", "UpdateView", "DeleteView",
          "FormView", "RedirectView"], FrameworkType.VIEW),
        (["ModelAdmin", "TabularInline", "StackedInline", "AdminSite"],
         FrameworkType.ADMIN),
        (["BaseCommand", "AppCommand", "LabelCommand"], FrameworkType.COMMAND),
        (["Form", "ModelForm", "BaseForm", "BaseModelForm"], FrameworkType.FORM),
        (["Manager", "QuerySet", "BaseManager"], FrameworkType.MANAGER),
        (["MiddlewareMixin", "BaseMiddleware"], FrameworkType.MIDDLEWARE),
    ],
    decorator_rules={
        "admin.register": FrameworkType.ADMIN,
        "register": FrameworkType.ADMIN,
        "shared_task": FrameworkType.TASK,
        "task": FrameworkType.TASK,
        "app.task": FrameworkType.TASK,
        "periodic_task": FrameworkType.TASK,
    },
    path_hints={
        "/models/": FrameworkType.MODEL,
        "/models.py": FrameworkType.MODEL,
        "/views/": FrameworkType.VIEW,
        "/views.py": FrameworkType.VIEW,
        "/serializers/": FrameworkType.SERIALIZER,
        "/serializers.py": FrameworkType.SERIALIZER,
        "/admin/": FrameworkType.ADMIN,
        "/admin.py": FrameworkType.ADMIN,
        "/management/commands/": FrameworkType.COMMAND,
        "/forms/": FrameworkType.FORM,
        "/forms.py": FrameworkType.FORM,
        "/managers/": FrameworkType.MANAGER,
        "/managers.py": FrameworkType.MANAGER,
        "/middleware/": FrameworkType.MIDDLEWARE,
        "/middleware.py": FrameworkType.MIDDLEWARE,
        "/tasks/": FrameworkType.TASK,
        "/tasks.py": FrameworkType.TASK,
    },
)


FLASK_DETECTOR = FrameworkDetector(
    name="flask",
    priority=5,
    base_class_rules=[
        (["Blueprint"], FrameworkType.BLUEPRINT),
        (["MethodView", "FlaskView"], FrameworkType.VIEW),
        (["FlaskForm", "Form"], FrameworkType.FORM),
    ],
    decorator_rules={
        "app.route": FrameworkType.VIEW,
        "blueprint.route": FrameworkType.VIEW,
        "login_required": FrameworkType.MIDDLEWARE,
    },
    path_hints={
        "/blueprints/": FrameworkType.BLUEPRINT,
        "/extensions/": FrameworkType.EXTENSION,
    },
)


FASTAPI_DETECTOR = FrameworkDetector(
    name="fastapi",
    priority=5,
    base_class_rules=[
        (["APIRouter"], FrameworkType.ROUTER),
        (["BaseModel"], FrameworkType.SERIALIZER),  # Pydantic models as serializers
    ],
    decorator_rules={
        "app.get": FrameworkType.VIEW,
        "app.post": FrameworkType.VIEW,
        "app.put": FrameworkType.VIEW,
        "app.delete": FrameworkType.VIEW,
        "app.patch": FrameworkType.VIEW,
        "router.get": FrameworkType.VIEW,
        "router.post": FrameworkType.VIEW,
        "router.put": FrameworkType.VIEW,
        "router.delete": FrameworkType.VIEW,
        "router.patch": FrameworkType.VIEW,
        "Depends": FrameworkType.DEPENDENCY,
    },
    path_hints={
        "/routers/": FrameworkType.ROUTER,
        "/dependencies/": FrameworkType.DEPENDENCY,
    },
)


# Default registry with all built-in detectors
_default_registry = DetectorRegistry()
_default_registry.register(DJANGO_DETECTOR)
_default_registry.register(FLASK_DETECTOR)
_default_registry.register(FASTAPI_DETECTOR)


def classify_framework_type(
    class_name: str,
    bases: list[str],
    decorators: list[str],
    file_path: str,
    registry: Optional[DetectorRegistry] = None,
) -> FrameworkType:
    """Classify a class as a framework component type.

    Uses the default registry unless a custom one is provided.
    """
    reg = registry or _default_registry
    return reg.classify(class_name, bases, decorators, file_path)


# Backward-compatible alias
classify_django_type = classify_framework_type
