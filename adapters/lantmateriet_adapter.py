from __future__ import annotations

from typing import Any

from connectors import lantmateriet
from connectors.common import DALARNA_BBOX

from .base import BaseSourceAdapter


class LantmaterietAdapter(BaseSourceAdapter):
    source_id = "lantmateriet"
    display_name = "Lantmäteriet"

    def fetch_records(self, bbox=DALARNA_BBOX, **kwargs):
        return lantmateriet.fetch_records(bbox=bbox, **kwargs)

    def test_connection(self) -> dict[str, Any]:
        recs = self.fetch_records(DALARNA_BBOX)
        return {
            "source": self.source_id,
            "status": "ok",
            "record_count": len(recs),
            "message": f"Connected to Lantmateriet. Fetched {len(recs)} items.",
        }
