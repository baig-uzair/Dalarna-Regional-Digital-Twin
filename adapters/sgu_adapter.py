from __future__ import annotations

from typing import Any

from connectors import sgu
from connectors.common import DALARNA_BBOX

from .base import BaseSourceAdapter


class SGUAdapter(BaseSourceAdapter):
    source_id = "sgu"
    display_name = "SGU"

    def fetch_records(self, bbox=DALARNA_BBOX, **kwargs):
        return sgu.fetch_records(bbox=bbox, **kwargs)

    def test_connection(self) -> dict[str, Any]:
        recs = self.fetch_records(DALARNA_BBOX)
        return {
            "source": self.source_id,
            "status": "ok",
            "record_count": len(recs),
            "message": f"Connected to SGU. Fetched {len(recs)} groundwater bodies.",
        }
