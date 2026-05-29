import json
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("charlie.self_mod.config")

class ConfigEditor:
    def __init__(self, config_path: str = "charlie_config.json"):
        self.config_path = Path(config_path)

    def read_config(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            return {}
        try:
            with open(self.config_path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"failed_read_config | {e}")
            return {}

    def update_key(self, key_path: str, value: Any) -> bool:
        """Updates a nested key in the config. Key path is dot-separated, e.g., 'ui.accent'."""
        config = self.read_config()
        keys = key_path.split(".")

        curr = config
        for key in keys[:-1]:
            if key not in curr or not isinstance(curr[key], dict):
                curr[key] = {}
            curr = curr[key]

        curr[keys[-1]] = value

        try:
            with open(self.config_path, "w") as f:
                json.dump(config, f, indent=4)
                f.write("\n")
            return True
        except Exception as e:
            logger.error(f"failed_update_config | {e}")
            return False

    def list_config(self) -> str:
        config = self.read_config()
        return json.dumps(config, indent=2)
