"""FQN: frozen value type for fully qualified names.

Centralizes construction, parsing, and validation. Callers use from_path, from_dotted, and child to build
FQNs; parent, name, and parts to decompose them.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FQN:
    _parts: tuple[str, ...]

    # -- Construction -------------------------------------------------------

    @classmethod
    def from_path(cls, rel_path: str) -> FQN:
        """Convert a relative file path to a module FQN.

        Strips .py, normalises path separators to dots, and removes
        __init__ suffixes.
        """
        module = rel_path.removesuffix(".py").replace("/", ".").replace("\\", ".")
        if module.endswith(".__init__"):
            module = module.removesuffix(".__init__")
        return cls.from_dotted(module)

    @classmethod
    def from_dotted(cls, dotted: str) -> FQN:
        """Deserialise a dotted string, validating format.

        Raises ValueError on empty strings, leading/trailing dots, or
        double dots.
        """
        if not dotted:
            raise ValueError("FQN cannot be empty")
        if dotted.startswith(".") or dotted.endswith("."):
            raise ValueError(f"FQN cannot start or end with dot: {dotted!r}")
        if ".." in dotted:
            raise ValueError(f"FQN cannot contain double dots: {dotted!r}")
        parts = tuple(dotted.split("."))
        if any(not p for p in parts):
            raise ValueError(f"FQN segment cannot be empty: {dotted!r}")
        return cls(_parts=parts)

    @classmethod
    def from_dotted_safe(cls, dotted: str) -> FQN | None:
        """Attempt from_dotted, returning None instead of raising."""
        try:
            return cls.from_dotted(dotted)
        except ValueError:
            return None

    def child(self, name: str) -> FQN:
        """Create a descendant FQN by appending *name*."""
        if not name:
            raise ValueError("Child name cannot be empty")
        return FQN(_parts=self._parts + (name,))

    # -- Interpretation -----------------------------------------------------

    @property
    def parent(self) -> FQN | None:
        """Return the parent FQN, or None for a single-segment FQN."""
        if len(self._parts) <= 1:
            return None
        return FQN(_parts=self._parts[:-1])

    @property
    def name(self) -> str:
        """Return the rightmost segment."""
        return self._parts[-1]

    @property
    def parts(self) -> tuple[str, ...]:
        """Return all segments for iteration/debugging."""
        return self._parts

    # -- Dunder -------------------------------------------------------------

    def __str__(self) -> str:
        return ".".join(self._parts)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FQN):
            return NotImplemented
        return self._parts == other._parts

    def __hash__(self) -> int:
        return hash(self._parts)