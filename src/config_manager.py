import os
import yaml
import argparse
from typing import Dict, Any, List

class ConfigManager:
    def __init__(self, config_path: str = "config/config.yaml", cli_args: List[str] = None):
        self.config_path = config_path
        self.config: Dict[str, Any] = {}
        self.load_config()
        self.parse_and_merge_cli(cli_args)
        self.ensure_directories()

    def load_config(self):
        """Loads configuration from YAML files."""
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        
        with open(self.config_path, 'r') as f:
            try:
                self.config = yaml.safe_load(f) or {}
            except yaml.YAMLError as e:
                raise ValueError(f"Error parsing YAML config: {e}")

        # Load credentials if present
        creds_path = "config/credentials.yaml"
        if os.path.exists(creds_path):
            with open(creds_path, 'r') as f:
                try:
                    creds = yaml.safe_load(f) or {}
                    self.config.update(creds)
                except yaml.YAMLError as e:
                    print(f"[WARNING] Error parsing credentials YAML: {e}")

    def parse_and_merge_cli(self, args: List[str] = None):
        """Parses CLI arguments and merges overrides into config."""
        parser = argparse.ArgumentParser(description="CryoDataDownloader - Ingestion Pipeline")
        parser.add_argument("--config", type=str, default=self.config_path, help="Path to config yaml")
        parser.add_argument("--start-date", type=str, help="Override start date (YYYY-MM-DD)")
        parser.add_argument("--end-date", type=str, help="Override end date (YYYY-MM-DD)")
        parser.add_argument("--mode", type=str, choices=["resilient", "strict"], help="Override execution mode")
        parser.add_argument("--cpu-workers", type=str, help="Override CPU worker count (auto or integer)")
        parser.add_argument("--glaciers", type=str, nargs="+", help="Explicit glacier filter names")
        parser.add_argument("--sources", type=str, nargs="+", help="Sources to download e.g., sentinel1 sentinel2")
        parser.add_argument("--headless", action="store_true", help="Run in headless CLI mode instead of GUI dashboard")

        parsed_args = parser.parse_args(args)

        # Merge overrides
        if parsed_args.start_date:
            self.config.setdefault("date_range", {})["start_date"] = parsed_args.start_date
        if parsed_args.end_date:
            self.config.setdefault("date_range", {})["end_date"] = parsed_args.end_date
        if parsed_args.mode:
            self.config.setdefault("download", {})["mode"] = parsed_args.mode
        if parsed_args.cpu_workers:
            val = parsed_args.cpu_workers
            if val.isdigit():
                self.config.setdefault("parallelism", {})["cpu_workers"] = int(val)
            else:
                self.config.setdefault("parallelism", {})["cpu_workers"] = val
        if parsed_args.sources:
            # Set selected sources to true, others to false in config
            if "sources" not in self.config:
                self.config["sources"] = {}
            for src in self.config["sources"]:
                self.config["sources"][src]["enabled"] = src in parsed_args.sources

        # Headless option is a runtime flag
        self.config["headless"] = bool(parsed_args.headless)
        self.config["glaciers_filter"] = parsed_args.glaciers if parsed_args.glaciers else []

    def ensure_directories(self):
        """Creates the directory structure based on config values."""
        project_cfg = self.config.get("project", {})
        output_dir = project_cfg.get("output_dir", "./data")
        geojson_dir = project_cfg.get("input_geojson_folder", project_cfg.get("geojson_dir", "./inputGeoJson"))

        dirs = [
            geojson_dir,
            os.path.join(output_dir, "raw", "sentinel1"),
            os.path.join(output_dir, "raw", "sentinel2"),
            os.path.join(output_dir, "raw", "era5"),
            os.path.join(output_dir, "raw", "modis"),
            os.path.join(output_dir, "raw", "dem"),
            os.path.join(output_dir, "raw", "landsat_thermal"),
            os.path.join(output_dir, "clipped"),
            os.path.join(output_dir, "processed", "geotiff"),
            "./metadata/sqlite",
            "./metadata/logs",
            "./metadata/reports",
        ]

        for d in dirs:
            os.makedirs(d, exist_ok=True)

    def get(self, key_path: str, default: Any = None) -> Any:
        """Helper to get nested dictionary values using dot notation."""
        parts = key_path.split(".")
        current = self.config
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current
