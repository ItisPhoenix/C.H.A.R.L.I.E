export function formatGaugeValue(value: number, unit?: string): string {
  const numeric = Number.isFinite(value) ? value : 0;
  switch ((unit || "percent_0_100").toLowerCase()) {
    case "percent_0_100":
      return `${numeric}%`;
    case "fraction_0_1":
      return `${numeric * 100}%`;
    case "celsius":
      return `${numeric}°C`;
    case "bytes": {
      const units = ["B", "KiB", "MiB", "GiB", "TiB"];
      let scaled = numeric;
      let index = 0;
      while (scaled >= 1024 && index < units.length - 1) {
        scaled /= 1024;
        index += 1;
      }
      return `${scaled} ${units[index]}`;
    }
    default:
      return String(numeric);
  }
}
