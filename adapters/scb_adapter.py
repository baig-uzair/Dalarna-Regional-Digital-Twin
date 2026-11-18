from __future__ import annotations

from typing import Any

from connectors import scb

from .base import BaseSourceAdapter


class SCBAdapter(BaseSourceAdapter):
    source_id = "scb"
    display_name = "SCB"

    def fetch_records(self, **kwargs):
        return scb.fetch_records(**kwargs)

    def test_connection(self) -> dict[str, Any]:
        recs = self.fetch_records()
        return {
            "source": self.source_id,
            "status": "ok",
            "record_count": len(recs),
            "message": f"Connected to SCB. Fetched {len(recs)} municipality records.",
        }
