/**
 * Camera math, bounds fitting, and safe zone calculations for Charlie Map.
 *
 * Accounts for Charlie's docked core in the bottom-right corner so map elements,
 * routes, and context cards are never obscured.
 */

export interface PaddingOptions {
  top?: number;
  bottom?: number;
  left?: number;
  right?: number;
}

/**
 * Standard Charlie HUD Safe Zone Padding.
 * Gives 280px margin on the right and 120px at the bottom to avoid the docked Charlie Core.
 */
export const CHARLIE_SAFE_PADDING: Required<PaddingOptions> = {
  top: 60,
  bottom: 120,
  left: 60,
  right: 280,
};

/**
 * Calculate bounding box [minLon, minLat, maxLon, maxLat] from an array of coordinates.
 */
export function calculateBounds(
  coords: [number, number][]
): [[number, number], [number, number]] | null {
  if (!coords || coords.length === 0) return null;

  let minLon = coords[0][0];
  let maxLon = coords[0][0];
  let minLat = coords[0][1];
  let maxLat = coords[0][1];

  for (let i = 1; i < coords.length; i++) {
    const [lon, lat] = coords[i];
    if (lon < minLon) minLon = lon;
    if (lon > maxLon) maxLon = lon;
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
  }

  // Prevent degenerate single-point bounds
  if (minLon === maxLon) {
    minLon -= 0.05;
    maxLon += 0.05;
  }
  if (minLat === maxLat) {
    minLat -= 0.05;
    maxLat += 0.05;
  }

  return [
    [minLon, minLat],
    [maxLon, maxLat],
  ];
}

/**
 * Heuristic flyTo animation duration based on geodesic distance between coordinates.
 */
export function calculateFlyDuration(
  startLon: number,
  startLat: number,
  endLon: number,
  endLat: number
): number {
  const dLon = Math.abs(endLon - startLon);
  const dLat = Math.abs(endLat - startLat);
  const dist = Math.sqrt(dLon * dLon + dLat * dLat);

  if (dist < 1.0) {
    return 450; // Local / city move
  }
  if (dist < 15.0) {
    return 750; // Regional / country move
  }
  if (dist < 60.0) {
    return 1000; // Sub-continental
  }
  return 1200; // Global transition
}
