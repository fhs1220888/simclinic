"""Adapters preserve uncertainty; raw text never becomes fabricated tool success."""
from typing import Protocol
from pathlib import Path
from simclinic.core.record import Record


class Adapter(Protocol):
    def import_record(self, source: Path, metadata: Path) -> Record:
        """Return validated v1 events ordered by observation, with source evidence."""
        ...
