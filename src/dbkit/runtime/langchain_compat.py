from __future__ import annotations

from typing import Any


def configure_langchain_deserialization_allowlist(
    allowed_objects: str = "messages",
) -> None:
    """Set LangChain Reviver's default allowlist before LangGraph imports it.

    Current LangGraph versions instantiate ``Reviver()`` at import time inside
    checkpoint serializers. LangChain warns when that default is omitted, so
    DBKit patches the constructor boundary to provide the explicit default the
    warning asks callers to use.
    """

    import importlib

    load_module = importlib.import_module("langchain_core.load.load")
    current = load_module.Reviver
    if getattr(current, "_dbkit_allowed_objects", None) == allowed_objects:
        return

    class DBKitReviver(current):  # type: ignore[misc, valid-type]
        _dbkit_allowed_objects = allowed_objects

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            if not args and kwargs.get("allowed_objects") is None:
                kwargs["allowed_objects"] = allowed_objects
            super().__init__(*args, **kwargs)

    load_module.Reviver = DBKitReviver
