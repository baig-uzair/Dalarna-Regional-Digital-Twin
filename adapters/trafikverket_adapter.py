from __future__ import annotations

from typing import Any

from connectors import trafikverket
from connectors.common import DALARNA_BBOX

from .base import BaseSourceAdapter


class TrafikverketAdapter(BaseSourceAdapter):
    source_id = "trafikverket"
    display_name = "Trafikverket"

    def fetch_records(self, bbox=DALARNA_BBOX, **kwargs):
        return trafikverket.fetch_records(bbox=bbox, **kwargs)

    def test_connection(self) -> dict[str, Any]:
        recs = self.fetch_records(DALARNA_BBOX)
        return {
            "source": self.source_id,
            "status": "ok",
            "record_count": len(recs),
            "message": f"Connected to Trafikverket. Fetched {len(recs)} weather stations.",
        }
