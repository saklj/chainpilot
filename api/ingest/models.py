"""Pure data objects used by the validation and import pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ValidatedRow:
    po_id: str
    material_pn: str
    supplier_id: str
    qty: int
    eta_date: date


@dataclass(frozen=True)
class ValidationError:
    row: int
    field: str
    code: str
    reason: str


@dataclass(frozen=True)
class ValidationReport:
    filename: str
    total_rows: int
    valid_rows: tuple[ValidatedRow, ...]
    errors: tuple[ValidationError, ...]

    @property
    def valid_count(self) -> int:
        return len(self.valid_rows)

    @property
    def error_count(self) -> int:
        return len(self.errors)
