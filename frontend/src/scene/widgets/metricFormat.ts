function formatNumber(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1).replace(/\.0$/, "");
}

export function formatMetricValue(rawValue: unknown, unit: unknown, available: unknown = true): string {
  if (available === false || typeof rawValue !== "number" || !Number.isFinite(rawValue)) return "—";

  const normalizedUnit = typeof unit === "string" ? unit.trim().toLowerCase() : "unknown";
  if (normalizedUnit === "percent_0_100") return `${formatNumber(rawValue)}%`;
  if (normalizedUnit === "fraction_0_1") return `${formatNumber(rawValue * 100)}%`;
  if (normalizedUnit === "celsius") return `${formatNumber(rawValue)}°C`;
  if (normalizedUnit === "bytes") {
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    let scaled = Math.max(0, rawValue);
    let index = 0;
    while (scaled >= 1024 && index < units.length - 1) {
      scaled /= 1024;
      index += 1;
    }
    return `${formatNumber(scaled)} ${units[index]}`;
  }

  return formatNumber(rawValue);
}
