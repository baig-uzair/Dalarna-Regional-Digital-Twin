from __future__ import annotations

from typing import Any

from connectors import skogsstyrelsen

from .base import BaseSourceAdapter


class SkogsstyrelsenAdapter(BaseSourceAdapter):
    source_id = "skogsstyrelsen"
    display_name = "Skogsstyrelsen"

    def fetch_records(self, **kwargs):
        return skogsstyrelsen.fetch_records(**kwargs)

    def test_connection(self) -> dict[str, Any]:
        recs = self.fetch_records()
        return {
            "source": self.source_id,
            "status": "ok",
            "record_count": len(recs),
            "message": f"Connected to Skogsstyrelsen. Fetched {len(recs)} forest stands.",
        }
