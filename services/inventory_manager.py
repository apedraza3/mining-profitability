import json
import threading
import uuid
from pathlib import Path


class InventoryManager:
    def __init__(self, inventory_path: str, locations_path: str):
        self.inventory_path = Path(inventory_path)
        self.locations_path = Path(locations_path)
        self._lock = threading.Lock()

    # --- Inventory ---

    def _load_inventory_unlocked(self) -> dict:
        """Load inventory without acquiring lock. Caller must hold self._lock."""
        if not self.inventory_path.exists():
            return {"miners": []}
        with open(self.inventory_path, "r") as f:
            return json.load(f)

    def _save_inventory_unlocked(self, data: dict) -> None:
        """Save inventory without acquiring lock. Caller must hold self._lock."""
        with open(self.inventory_path, "w") as f:
            json.dump(data, f, indent=2)

    def get_all_miners(self) -> list[dict]:
        with self._lock:
            return self._load_inventory_unlocked()["miners"]

    def get_miner(self, miner_id: str) -> dict | None:
        for m in self.get_all_miners():
            if m["id"] == miner_id:
                return m
        return None

    def add_miner(self, miner_data: dict) -> dict:
        with self._lock:
            data = self._load_inventory_unlocked()
            miner_data["id"] = str(uuid.uuid4())
            # Set defaults for optional fields
            miner_data.setdefault("status", "active")
            miner_data.setdefault("notes", "")
            miner_data.setdefault("quantity", 1)
            miner_data.setdefault("whattomine_coin_id", None)
            miner_data.setdefault("hashrateno_model_key", "")
            miner_data.setdefault("power_import_key", "")
            miner_data.setdefault("pool_fee_pct", 1.0)
            miner_data.setdefault("hashrate_unit", "TH/s")
            miner_data.setdefault("powerpool_worker_key", "")
            data["miners"].append(miner_data)
            self._save_inventory_unlocked(data)
            return miner_data

    def update_miner(self, miner_id: str, updates: dict) -> dict | None:
        with self._lock:
            data = self._load_inventory_unlocked()
            for i, m in enumerate(data["miners"]):
                if m["id"] == miner_id:
                    updates.pop("id", None)  # never overwrite the id
                    data["miners"][i].update(updates)
                    self._save_inventory_unlocked(data)
                    return data["miners"][i]
        return None

    def delete_miner(self, miner_id: str) -> bool:
        with self._lock:
            data = self._load_inventory_unlocked()
            original_len = len(data["miners"])
            data["miners"] = [m for m in data["miners"] if m["id"] != miner_id]
            if len(data["miners"]) < original_len:
                self._save_inventory_unlocked(data)
                return True
        return False

    def duplicate_miner(self, miner_id: str) -> dict | None:
        with self._lock:
            data = self._load_inventory_unlocked()
            miner = None
            for m in data["miners"]:
                if m["id"] == miner_id:
                    miner = m
                    break
            if not miner:
                return None
            new_miner = dict(miner)
            new_miner["id"] = str(uuid.uuid4())
            new_miner["name"] = miner["name"] + " (copy)"
            data["miners"].append(new_miner)
            self._save_inventory_unlocked(data)
            return new_miner

    # --- Locations ---

    def _load_locations_unlocked(self) -> dict:
        """Load locations without acquiring lock. Caller must hold self._lock."""
        if not self.locations_path.exists():
            return {"locations": []}
        with open(self.locations_path, "r") as f:
            return json.load(f)

    def _save_locations_unlocked(self, data: dict) -> None:
        """Save locations without acquiring lock. Caller must hold self._lock."""
        with open(self.locations_path, "w") as f:
            json.dump(data, f, indent=2)

    def get_all_locations(self) -> list[dict]:
        with self._lock:
            return self._load_locations_unlocked()["locations"]

    def get_location(self, location_id: str) -> dict | None:
        for loc in self.get_all_locations():
            if loc["id"] == location_id:
                return loc
        return None

    def add_location(self, location_data: dict) -> dict:
        with self._lock:
            data = self._load_locations_unlocked()
            location_data["id"] = "loc-" + str(uuid.uuid4())[:8]
            location_data.setdefault("currency", "USD")
            data["locations"].append(location_data)
            self._save_locations_unlocked(data)
            return location_data

    def update_location(self, location_id: str, updates: dict) -> dict | None:
        with self._lock:
            data = self._load_locations_unlocked()
            for i, loc in enumerate(data["locations"]):
                if loc["id"] == location_id:
                    updates.pop("id", None)
                    data["locations"][i].update(updates)
                    self._save_locations_unlocked(data)
                    return data["locations"][i]
        return None

    def delete_location(self, location_id: str) -> bool:
        with self._lock:
            data = self._load_locations_unlocked()
            original_len = len(data["locations"])
            data["locations"] = [l for l in data["locations"] if l["id"] != location_id]
            if len(data["locations"]) < original_len:
                self._save_locations_unlocked(data)
                return True
        return False
