"""Validated session snapshots; the root JSON is an atomic compatibility export only."""
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.recipes.discovery import DiscoveryStore
from app.recipes.ingredients import POLICY_VERSION, assess_ingredients

ROOT = Path(__file__).resolve().parents[2]
COORDINATE_VERSION = "cost-time.absolute.v2"


class MapPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: int = Field(gt=0, strict=True)
    topic: str = Field(min_length=1)
    x: float = Field(ge=-100, le=100)
    y: float = Field(ge=-100, le=100)
    desc: str = Field(min_length=1)


def coordinates(price, minutes):
    """Absolute, bounded scales; independent of other recommended recipes.

    The neutral anchors (£2.50/serving, 45 elapsed minutes) are product scale
    definitions, not claims about current market averages or cooking skill.
    A value one third of its anchor scores -50; three times it scores +50.
    Smooth tails distinguish long/expensive dishes without hard saturation.
    """
    if not all(math.isfinite(v) for v in (price, minutes)) or price <= 0 or minutes <= 0:
        raise ValueError("Invalid coordinate inputs")
    return round(100 * (price - 2.5) / (price + 2.5), 2), round(100 * (minutes - 45) / (minutes + 45), 2)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".map-", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            json.dump(value, file, ensure_ascii=False, allow_nan=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class MapStore:
    def __init__(self, database, export_root=ROOT):
        self.db = DiscoveryStore(Path(database))
        self.root = Path(export_root)
        self.lock = threading.RLock()
        with self.db.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS gastrolabe_ids (id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT UNIQUE NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS gastrolabe_sessions (token TEXT PRIMARY KEY, snapshot TEXT NOT NULL, profile TEXT)")

    def create(self, batch):
        token = uuid.uuid4().hex
        points, recipes = [], {}
        with self.lock, self.db.connect() as db:
            seen = set()
            for recipe in batch.recipes:
                if recipe.id in seen:
                    raise ValueError("Duplicate recipe ID")
                seen.add(recipe.id)
                if recipe.classification_version != POLICY_VERSION or not assess_ingredients(
                        recipe.dish_name, [i.model_dump() for i in recipe.ingredients], batch.inventory)["eligible"]:
                    raise ValueError("Ineligible recipe in map")
                db.execute("INSERT OR IGNORE INTO gastrolabe_ids(source_id) VALUES (?)", (recipe.id,))
                identity = db.execute("SELECT id FROM gastrolabe_ids WHERE source_id=?", (recipe.id,)).fetchone()[0]
                x, y = coordinates(recipe.price, recipe.time)
                names = ", ".join(i.name for i in recipe.ingredients[:4])
                point = MapPoint(id=identity, topic=recipe.dish_name, x=x, y=y,
                                 desc=recipe.description or f"{recipe.dish_name}, featuring {names}. Source method available after Cook.")
                points.append(point.model_dump())
                recipes[str(identity)] = recipe.model_dump()
            snapshot = {"points": points, "recipes": recipes, "inventory": batch.inventory,
                        "coordinate_version": COORDINATE_VERSION}
            # Each session gets its own static JSON. Never serve the shared latest
            # export as a user's data source.
            atomic_json(self.path(token), points)
            db.execute("INSERT INTO gastrolabe_sessions VALUES (?,?,NULL)", (token, json.dumps(snapshot)))
            atomic_json(self.root / "fridge_recipes.json", points)
        return token

    def path(self, token):
        if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
            raise KeyError("Unknown map session")
        return self.root / "data" / "maps" / token / "fridge_recipes.json"

    def get(self, token):
        path = self.path(token)
        with self.db.connect() as db:
            row = db.execute("SELECT snapshot,profile FROM gastrolabe_sessions WHERE token=?", (token,)).fetchone()
        if row is None:
            raise KeyError("Unknown map session")
        snapshot = json.loads(row[0])
        # Retain friend's file-reading interface; recover a missing export from DB.
        with self.lock:
            if not path.exists():
                atomic_json(path, snapshot["points"])
            points = [MapPoint.model_validate(p).model_dump() for p in json.loads(path.read_text(encoding="utf-8"))]
            if points != snapshot["points"] or len({p["id"] for p in points}) != len(points):
                raise ValueError("Map snapshot mismatch")
            if snapshot.get("coordinate_version") != COORDINATE_VERSION:
                # Recalculate from persisted factual estimates, never from old
                # x/y or batch ranks. Keep IDs, ownership, profile and descriptions.
                previous = points
                points = []
                for point in previous:
                    recipe = snapshot["recipes"][str(point["id"])]
                    x, y = coordinates(recipe["price"], recipe["time"])
                    points.append(MapPoint(**{**point, "x": x, "y": y}).model_dump())
                snapshot.update(points=points, coordinate_version=COORDINATE_VERSION)
                atomic_json(path, points)
                with self.db.connect() as db:
                    db.execute("UPDATE gastrolabe_sessions SET snapshot=? WHERE token=?", (json.dumps(snapshot), token))
                latest = self.root / "fridge_recipes.json"
                if latest.exists() and json.loads(latest.read_text(encoding="utf-8")) == previous:
                    atomic_json(latest, points)
        # Maps saved under an older policy may hold recipes with unconfirmed ingredients:
        # serve only those that pass the current policy against the map's inventory.
        inventory = snapshot.get("inventory", [])
        keep = {key for key, recipe in snapshot["recipes"].items()
                if assess_ingredients(recipe["dish_name"], [{"name": i["name"], "quantity": i.get("quantity", "")}
                                                            for i in recipe["ingredients"]], inventory)["eligible"]}
        return {**snapshot, "points": [p for p in snapshot["points"] if str(p["id"]) in keep],
                "recipes": {k: v for k, v in snapshot["recipes"].items() if k in keep},
                "profile": json.loads(row[1]) if row[1] else None}

    def profile(self, token, profile):
        self.get(token)
        with self.db.connect() as db:
            db.execute("UPDATE gastrolabe_sessions SET profile=? WHERE token=?", (json.dumps(profile), token))


_store = None


def get_map_store():
    global _store
    if _store is None:
        from app.config import get_settings
        _store = MapStore(get_settings().database_path)
    return _store
