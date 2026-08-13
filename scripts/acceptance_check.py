"""Developer-mode HTTP acceptance probe for a running Charlie web server."""

import json
import sys
from urllib.error import URLError
from urllib.request import urlopen


def main() -> int:
    base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    endpoints = (
        "/api/health",
        "/api/config",
        "/api/capabilities",
        "/api/tasks",
        "/api/backup/status",
        "/api/media",
    )
    report = {}
    for endpoint in endpoints:
        try:
            with urlopen(base + endpoint, timeout=1) as response:
                report[endpoint] = {"ok": response.status == 200, "status": response.status}
        except (OSError, URLError) as exc:
            report[endpoint] = {"ok": False, "error": type(exc).__name__}
    print(json.dumps(report, indent=2))
    return 0 if all(item["ok"] for item in report.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
