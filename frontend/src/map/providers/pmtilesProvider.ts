import * as maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";

let protocolRegistered = false;
let pmtilesProtocolInstance: Protocol | null = null;

/**
 * Initialize and register PMTiles custom protocol handler with MapLibre GL.
 */
export function initPMTilesProtocol(): Protocol {
  if (!protocolRegistered) {
    pmtilesProtocolInstance = new Protocol();
    maplibregl.addProtocol("pmtiles", pmtilesProtocolInstance.tile);
    protocolRegistered = true;
  }
  return pmtilesProtocolInstance!;
}
