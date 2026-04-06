"""Provider-agnostic contracts for structured financial document extraction."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

from ...models.financial_document_extraction import ExtractedFinancialStatement


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
    notes: list[str] = field(default_factory=list)


class VisionStatementExtractor(Protocol):
    """Structured provider contract for extracting a financial statement from pages."""

    async def extract_statement(
        self,
        pages: list[RenderedPage],
        *,
        schema: type[BaseModel],
        prompt_context: DocumentPromptContext,
    ) -> ExtractedFinancialStatement:
        ...
