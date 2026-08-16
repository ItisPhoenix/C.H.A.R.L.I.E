"""Abstract Intelligence Layer Provider Interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from charlie.geo.models import LayerDataResult, MapFeature


class IntelligenceProvider(ABC):
    """Abstract interface for Charlie spatial intelligence layers."""

    @property
    @abstractmethod
    def layer_id(self) -> str:
        """Unique layer identifier."""
        pass

    @property
    @abstractmethod
    def attribution(self) -> str:
        """Data source legal / provenance attribution."""
        pass

    @abstractmethod
    async def fetch_data(self) -> LayerDataResult:
        """Fetch and normalize latest intelligence features."""
        pass
