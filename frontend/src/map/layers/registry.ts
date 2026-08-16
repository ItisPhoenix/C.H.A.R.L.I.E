import type { IntelligenceLayerDefinition } from "../types";
import { fetchBackendIntelligenceLayer } from "../providers/intelligenceProvider";

/**
 * Authoritative Spatial Intelligence Layer Registry.
 * INVARIANT: Every single layer has defaultEnabled: false.
 */
export const INTELLIGENCE_LAYERS: IntelligenceLayerDefinition[] = [
  // 1. Environment
  {
    id: "earthquakes",
    label: "Earthquakes (M2.5+)",
    category: "Environment",
    defaultEnabled: false,
    attribution: "USGS Hazards",
    fetcher: (signal) => fetchBackendIntelligenceLayer("earthquakes", signal),
  },
  {
    id: "wildfires",
    label: "Active Wildfires",
    category: "Environment",
    defaultEnabled: false,
    attribution: "NASA EONET",
    fetcher: (signal) => fetchBackendIntelligenceLayer("wildfires", signal),
  },

  // 2. Weather
  {
    id: "weather",
    label: "Global Meteorology",
    category: "Weather",
    defaultEnabled: false,
    attribution: "Open-Meteo",
    fetcher: (signal) => fetchBackendIntelligenceLayer("weather", signal),
  },

  // 3. Cyber Threat Intelligence
  {
    id: "cyber_threats",
    label: "Cyber Threat Indicators",
    category: "Cyber",
    defaultEnabled: false,
    attribution: "abuse.ch Threat Intelligence",
    fetcher: (signal) => fetchBackendIntelligenceLayer("cyber_threats", signal),
  },

  // 4. Infrastructure (Graceful unconfigured placeholders)
  {
    id: "subsea_cables",
    label: "Subsea Telecom Cables",
    category: "Infrastructure",
    defaultEnabled: false,
    attribution: "TeleGeography",
    requiresCredential: true,
  },
  {
    id: "flight_radar",
    label: "Live Aircraft Transponders",
    category: "Aviation",
    defaultEnabled: false,
    attribution: "OpenSky Network",
    requiresCredential: true,
  },
  {
    id: "ais_shipping",
    label: "Maritime AIS Vessels",
    category: "Maritime",
    defaultEnabled: false,
    attribution: "Global Fishing Watch",
    requiresCredential: true,
  },
];

export const LAYER_BY_ID = new Map<string, IntelligenceLayerDefinition>(
  INTELLIGENCE_LAYERS.map((l) => [l.id, l])
);
