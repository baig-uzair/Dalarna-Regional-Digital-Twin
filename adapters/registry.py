from __future__ import annotations

from .base import BaseSourceAdapter
from .lantmateriet_adapter import LantmaterietAdapter
from .scb_adapter import SCBAdapter
from .sgu_adapter import SGUAdapter
from .skogsstyrelsen_adapter import SkogsstyrelsenAdapter
from .smhi_adapter import SMHIAdapter
from .trafikverket_adapter import TrafikverketAdapter

_ADAPTERS = {
    "smhi": SMHIAdapter(),
    "trafikverket": TrafikverketAdapter(),
    "scb": SCBAdapter(),
    "sgu": SGUAdapter(),
    "skogsstyrelsen": SkogsstyrelsenAdapter(),
    "lantmateriet": LantmaterietAdapter(),
}


def get_adapter(source_id: str) -> BaseSourceAdapter:
    key = (source_id or "").lower()
    if key not in _ADAPTERS:
        raise KeyError(f"No adapter registered for source '{source_id}'")
    return _ADAPTERS[key]


def list_adapters() -> list[BaseSourceAdapter]:
    return list(_ADAPTERS.values())
