"""Read immutable cell vegetation evidence with explicit as-of selection.

The SQLite index is disk-backed; only a bounded set of sampled cells is cached.
No network access or future-product interpolation occurs during prediction.
"""

from collections import OrderedDict
from contextlib import closing
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import zlib
from threading import RLock

from wildfire_data.core.grid import cell_from_id
from wildfire_data.core.hashing import sha256_file
from wildfire_data.model.features.schema import VEGETATION_COLUMNS, VEGETATION_PRODUCT_VERSIONS, VEGETATION_QA_POLICIES

VEGETATION_VERSION = "vegetation-cell-evidence/v1"
PRODUCT_PREFIX = {"NALCMS": "land_cover", "MOD44B": "cover", "MOD13Q1": "condition"}


def utc(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None:
        raise ValueError("vegetation timestamps must include a timezone")
    return result.astimezone(timezone.utc)


def empty_features():
    return {name: 1. if name.endswith("_missing") else 0. if name.endswith("_fraction") else None
            for name in VEGETATION_COLUMNS}


def rollout_context(model, origin_frame):
    if not getattr(model, "vegetation_columns", ()):
        return {}
    # Cell-specific local-solar cutoffs can differ within a snapshot. The
    # earliest origin cutoff is a conservative shared information boundary.
    return {"origin_at": min(utc(value) for value in origin_frame.feature_cutoff_at)}


def validate_policy(policy):
    if set(policy) - {'static_context_mode'} != {"max_age_days", "min_valid_fraction"}:
        raise ValueError("vegetation policy requires max_age_days and min_valid_fraction")
    if policy.get('static_context_mode', 'as_of') not in ('as_of', 'retrospective'):
        raise ValueError('unsupported static vegetation context mode')
    if set(policy["max_age_days"]) != set(PRODUCT_PREFIX):
        raise ValueError("explicit staleness limit required for every product")
    for value in policy["max_age_days"].values():
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("staleness limits must be finite positive days")
    if not 0 < policy["min_valid_fraction"] <= 1:
        raise ValueError("minimum valid fraction must be in (0, 1]")


def validate_source(source):
    required = ("source_id", "product", "version", "observation_start", "observation_end",
                "available_at", "availability_basis", "availability_evidence", "retrieved_at",
                "revision", "assets", "qa_policy")
    if any(k not in source for k in required):
        raise ValueError("incomplete vegetation source provenance")
    if (not source["source_id"] or not source["revision"]
            or VEGETATION_PRODUCT_VERSIONS.get(source["product"]) != source["version"]
            or source["qa_policy"] not in VEGETATION_QA_POLICIES):
        raise ValueError("unsupported vegetation product, revision or QA policy")
    start, end = utc(source["observation_start"]), utc(source["observation_end"])
    retrieved = utc(source["retrieved_at"])
    if start >= end:
        raise ValueError("invalid vegetation observation period")
    if source["availability_basis"] not in ("provider-publication", "captured-response", "unknown"):
        raise ValueError("unsupported availability basis")
    if source["availability_basis"] == "unknown":
        if source["available_at"] is not None:
            raise ValueError("unknown publication cannot declare an availability date")
    else:
        available = utc(source["available_at"])
        if not source["availability_evidence"] or available < end or available > retrieved:
            raise ValueError("publication evidence/date inconsistent with source period and retrieval")
        if source["availability_basis"] == "captured-response" and available != retrieved:
            raise ValueError("capture proves availability only at retrieval")
    if not source["assets"]:
        raise ValueError("vegetation source needs checksummed assets")
    for asset in source["assets"]:
        digest = asset.get("sha256", "")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("vegetation asset needs a SHA-256")


def select_features(records, *, cutoff_at, simulation_at, policy):
    """Pure shared selection for dataset rows and rollout candidates."""
    validate_policy(policy)
    cutoff, simulation = utc(cutoff_at), utc(simulation_at)
    if simulation < cutoff:
        raise ValueError("simulation time cannot precede the information cutoff")
    values, audit = empty_features(), {"selected": {}, "rejected": []}
    threshold = policy["min_valid_fraction"]
    for product, prefix in PRODUCT_PREFIX.items():
        eligible, insufficient = [], []
        for record in records:
            source = record["source"]
            if source["product"] != product:
                continue
            end = utc(source["observation_end"])
            retrospective = policy.get('static_context_mode') == 'retrospective' and product in ('NALCMS', 'MOD44B')
            reason = None
            if end > cutoff:
                reason = "observations-after-cutoff"
            elif not retrospective and (source["available_at"] is None or source["availability_basis"] == "unknown"):
                reason = "unproven-publication"
            elif not retrospective and utc(source["available_at"]) > cutoff:
                reason = "published-after-cutoff"
            elif (cutoff - end).total_seconds() / 86400 > policy["max_age_days"][product]:
                reason = "stale-at-origin"
            elif record["valid_fraction"] < threshold:
                reason = "insufficient-valid-area"
                insufficient.append(record)
            if reason:
                audit["rejected"].append({"source_id": source["source_id"], "reason": reason,
                                          "valid_fraction": record["valid_fraction"]})
            else:
                eligible.append(record)
        eligible.sort(key=lambda r: (utc(r["source"]["observation_end"]),
                      utc(r["source"]["available_at"]) if r['source']['available_at'] else datetime.min.replace(tzinfo=timezone.utc),
                      r["source"]["revision"], r["source"]["source_id"]), reverse=True)
        # Later revisions of the same observation period are alternatives,
        # not a previous composite for change features.
        unique, periods = [], set()
        for record in eligible:
            period = (utc(record["source"]["observation_start"]), utc(record["source"]["observation_end"]))
            if period not in periods:
                unique.append(record)
                periods.add(period)
        if not unique:
            if insufficient:
                diagnostic = max(insufficient, key=lambda r: (utc(r["source"]["observation_end"]), r["source"]["source_id"]))
                values.update({k: v for k, v in diagnostic["values"].items() if k.endswith("_valid_fraction")})
            continue
        latest = unique[0]
        selected_ids = [latest["source"]["source_id"]]
        values.update(latest["values"])
        if product == "MOD13Q1":
            for index in ("ndvi", "evi"):
                if values[f"vegetation_{index}_valid_fraction"] < threshold:
                    values[f"vegetation_{index}"] = None
                    values[f"vegetation_{index}_missing"] = 1.
        age = (simulation - utc(latest["source"]["observation_end"])).total_seconds() / 86400
        values["vegetation_" + prefix + "_age_days"] = age
        # Selection is frozen at the origin; crossing the staleness limit
        # during playback cannot switch to a different composite.
        if product == "MOD13Q1" and len(unique) > 1:
            previous = unique[1]
            selected_ids.append(previous["source"]["source_id"])
            values["vegetation_composite_gap_days"] = (
                utc(latest["source"]["observation_end"]) - utc(previous["source"]["observation_end"])
            ).total_seconds() / 86400
            old = {p["pixel_id"]: p for p in previous.get("pixels", [])}
            for index in ("ndvi", "evi"):
                area, weighted = 0., 0.
                for pixel in latest.get("pixels", []):
                    before = old.get(pixel["pixel_id"])
                    if before is None or pixel.get(index) is None or before.get(index) is None:
                        continue
                    if not math.isclose(pixel["area"], before["area"], rel_tol=1e-7, abs_tol=1e-4):
                        raise ValueError("composite pixel support changed for the same pixel identity")
                    area += pixel["area"]
                    weighted += pixel["area"] * (pixel[index] - before[index])
                values[f"vegetation_{index}_common_valid_fraction"] = area / 1_000_000
                if area / 1_000_000 >= threshold:
                    values[f"vegetation_{index}_change"] = weighted / area
                    values[f"vegetation_{index}_change_missing"] = 0.
        audit["selected"][product] = selected_ids
        if policy.get('static_context_mode') == 'retrospective' and product in ('NALCMS', 'MOD44B'):
            audit.setdefault('retrospective_static_sources', []).extend(selected_ids)
        for record in eligible:
            if record["source"]["source_id"] not in selected_ids:
                audit["rejected"].append({"source_id": record["source"]["source_id"], "reason": "older-eligible-alternative"})
    return {**values, "vegetation_lineage": audit,
            "vegetation_information_cutoff_at": cutoff.isoformat(), "vegetation_simulation_at": simulation.isoformat()}


class VegetationFeatureSampler:
    def __init__(self, manifest_path, *, expected_sha256=None, max_cached_cells=8192, raster_cache=None):
        self.manifest_path = Path(manifest_path).resolve()
        self.manifest_sha256 = sha256_file(self.manifest_path)
        if expected_sha256 is not None and self.manifest_sha256 != expected_sha256:
            raise ValueError("vegetation manifest checksum mismatch")
        self.manifest = json.loads(self.manifest_path.read_text())
        if (self.manifest.get("kind") != VEGETATION_VERSION or self.manifest.get("status") != "complete"
                or self.manifest.get("grid") != "ESRI:102008/1000m"):
            raise ValueError("requires a completed supported vegetation manifest")
        self.policy = self.manifest["policy"]
        self.payload_encoding = self.manifest.get('payload_encoding', 'json')
        if self.payload_encoding not in ('json', 'zlib-json/v1'):
            raise ValueError('Unsupported vegetation payload encoding')
        validate_policy(self.policy)
        artifact = self.manifest["artifact"]
        path = (self.manifest_path.parent / artifact["path"]).resolve()
        if path.parent != self.manifest_path.parent or sha256_file(path) != artifact["sha256"]:
            raise ValueError("vegetation cell artifact checksum/path mismatch")
        self.sources = self.manifest["sources"]
        self.raster_cache = raster_cache
        for identity, source in self.sources.items():
            validate_source(source)
            for asset in source['assets']:
                asset['path'] = str((self.manifest_path.parent / asset['path']).resolve())
            if identity != source["source_id"]:
                raise ValueError("vegetation source identity mismatch")
        # A connection per read avoids sharing sqlite connections across the
        # application's worker threads. Immutable source records alone cache.
        self.database_uri = path.as_uri() + "?mode=ro&immutable=1"
        if not isinstance(max_cached_cells, int) or max_cached_cells < 0:
            raise ValueError("cache size must be nonnegative")
        self.max_cached_cells = max_cached_cells
        self.cache = OrderedDict()
        self.cache_lock = RLock()
        self.raster_fallback = self.manifest.get('raster_fallback', False)
        self.rasters = {}
        self.raster_lock = RLock()

    def sample_cell(self, cell_id, *, cutoff_at, simulation_at=None):
        cell_from_id(cell_id)
        with self.cache_lock:
            records = self.cache.pop(cell_id, None)
        if records is None:
            with closing(sqlite3.connect(self.database_uri, uri=True)) as connection:
                records = [json.loads(zlib.decompress(row[0]) if self.payload_encoding == 'zlib-json/v1' else row[0]) for row in connection.execute(
                    "SELECT payload FROM cells WHERE cell_id=? ORDER BY source_id", (cell_id,))]
            if not records and self.raster_fallback:
                # Arbitrary rollout/map cells need the same source features as
                # training cells. The immutable table is an acceleration cache,
                # not the geographic boundary of an enriched model.
                from wildfire_data.providers.vegetation.aggregation import SourceRaster
                with self.raster_lock:
                    for identity, source in self.sources.items():
                        if identity not in self.rasters:
                            checked = set()
                            for asset in source['assets']:
                                path = Path(asset['path'])
                                if path not in checked and sha256_file(path) != asset['sha256']:
                                    raise ValueError('vegetation fallback raster checksum mismatch')
                                checked.add(path)
                            self.rasters[identity] = SourceRaster(source, cache_manifest=self.raster_cache)
                        records.append(self.rasters[identity].sample(cell_id))
            records = [{**r, "source": self.sources[r["source_id"]]} for r in records]
        with self.cache_lock:
            if self.max_cached_cells:
                self.cache[cell_id] = records
                while len(self.cache) > self.max_cached_cells:
                    self.cache.popitem(last=False)
        return select_features(records, cutoff_at=cutoff_at, simulation_at=simulation_at or cutoff_at, policy=self.policy)

    def bundle_reference(self):
        return {"manifest_path": str(self.manifest_path), "manifest_sha256": self.manifest_sha256}

    def close(self):
        with self.raster_lock:
            for raster in self.rasters.values():
                raster.close()
            self.rasters.clear()
