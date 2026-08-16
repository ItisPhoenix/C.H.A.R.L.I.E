"""C.H.A.R.L.I.E. V1 — Geospatial Models and Data Contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class GeocodingResult:
    name: str
    display_name: str
    coordinates: List[float]  # [lon, lat]
    bounding_box: Optional[List[List[float]]] = None  # [[minLon, minLat], [maxLon, maxLat]]
    category: Optional[str] = None
    place_type: Optional[str] = None
    provider: str = "nominatim"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RouteStep:
    instruction: str
    distance: str
    duration: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RouteResult:
    start: List[float]  # [lon, lat]
    start_label: str
    destination: List[float]  # [lon, lat]
    destination_label: str
    geometry: List[List[float]]  # [[lon, lat], ...]
    distance_km: float
    duration_min: Optional[float] = None
    steps: List[RouteStep] = field(default_factory=list)
    mode: str = "driving"
    provider: str = "osrm"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start": self.start,
            "startLabel": self.start_label,
            "destination": self.destination,
            "destinationLabel": self.destination_label,
            "geometry": self.geometry,
            "distanceKm": self.distance_km,
            "durationMin": self.duration_min,
            "steps": [s.to_dict() for s in self.steps],
            "mode": self.mode,
            "provider": self.provider,
        }


@dataclass
class MapFeature:
    id: str
    label: str
    category: str
    description: str
    coordinates: List[float]  # [lon, lat]
    severity: str = "normal"  # low, normal, medium, high, critical
    source: str = "open_data"
    timestamp: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)
    color: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LayerDataResult:
    layer_id: str
    status: str  # ready, loading, unconfigured, error, empty
    features: List[MapFeature] = field(default_factory=list)
    attribution: str = ""
    timestamp: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layer_id": self.layer_id,
            "status": self.status,
            "features": [f.to_dict() for f in self.features],
            "attribution": self.attribution,
            "timestamp": self.timestamp,
            "error": self.error,
        }
