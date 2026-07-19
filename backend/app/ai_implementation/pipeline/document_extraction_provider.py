"""Provider-agnostic contracts for structured financial document extraction."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RenderedPage:
    """Minimal rendered-page contract for vision-capable document extraction."""

    page_number: int
    mime_type: str = "image/png"
    content: bytes | None = None
    image_path: str | None = None


@dataclass
class DocumentPromptContext:
    """Lightweight prompt context shared with a structured extraction provider."""

    filename: str
    route_family: str | None = None
    source_mode: str | None = None
    notes: list[str] = field(default_factory=list)
