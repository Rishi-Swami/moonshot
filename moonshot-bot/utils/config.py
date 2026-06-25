"""
Configuration loader.
Reads config.yaml for non-sensitive settings.
Reads .env for private keys and API keys.
NEVER hardcode keys here.
"""

import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent


def load_config() -> dict:
    config_path = BASE_DIR / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # inject secrets from environment (never from yaml)
    config["keys"] = {
        "wallet_private_key": os.getenv("WALLET_PRIVATE_KEY"),
        "helius_api_key":     os.getenv("HELIUS_API_KEY"),
        "birdeye_api_key":    os.getenv("BIRDEYE_API_KEY"),
        "dashboard_output":   os.getenv("DASHBOARD_JSON_PATH",
                                         str(BASE_DIR / "data" / "dashboard.json")),
    }

    # validate critical keys
    missing = [k for k, v in config["keys"].items()
               if v is None and k != "birdeye_api_key"]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {missing}\n"
            f"Copy .env.example to .env and fill in your values."
        )

    return config
