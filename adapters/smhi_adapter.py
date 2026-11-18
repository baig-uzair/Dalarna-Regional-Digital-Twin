from __future__ import annotations

from typing import Any

from connectors import smhi
from connectors.common import NormalizedRecord

from .base import BaseSourceAdapter


class SMHIAdapter(BaseSourceAdapter):
    source_id = "smhi"
    display_name = "SMHI"

    def fetch_records(self, **kwargs) -> list[NormalizedRecord]:
        observation = smhi.fetch_observation()
        return [
            NormalizedRecord(
                source_id="SMHI",
                dataset_id="meteorological_observations",
                feature_id=observation.entity_id,
                observed_at=observation.observed_at,
                lon=observation.lon,
                lat=observation.lat,
                geometry_wkt=f"POINT({observation.lon} {observation.lat})",
                attributes={
                    "air_temperature_c": observation.temperature,
                    "station": "Borlänge flygplats",
                    "data_origin": "live",
                },
                source_url=smhi.URL,
                license="https://opendata.smhi.se",
            )
        ]

    def test_connection(self) -> dict[str, Any]:
        records = self.fetch_records()
        record = records[0]
        return {
            "source": self.source_id,
            "status": "ok",
            "record_count": len(records),
            "message": f"Connected to SMHI. Observation at Borlänge flygplats: {record.attributes['air_temperature_c']} C",
        }
