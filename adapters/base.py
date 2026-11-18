from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from connectors.common import NormalizedRecord


class BaseSourceAdapter(ABC):
    """Contract for each source adapter.

    Each source owns its own adapter, while the rest of the app depends only on this
    small contract instead of on individual connector modules.
    """

    source_id: str
    display_name: str

    @abstractmethod
    def fetch_records(self, **kwargs) -> list[NormalizedRecord]:
        """Return records using the shared internal source-data contract."""

    @abstractmethod
    def test_connection(self) -> dict[str, Any]:
        """Perform a lightweight live connectivity check without persisting data."""
