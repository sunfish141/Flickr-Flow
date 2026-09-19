"""Shared candidate radius and no-weather model input columns."""

DEFAULT_CANDIDATE_RADIUS_CELLS = 2

# Explicit model inputs.  Every other field in a row is lineage, a label,
# selection metadata, geometry, timing, or a missingness declaration.
DEFAULT_MODEL_FEATURE_COLUMNS = (
    "firms_center_has_detection",
    "firms_center_detection_count",
    "firms_center_bright_ti4_max",
    "firms_center_bright_ti4_mean",
    "firms_center_platform_count",
    "firms_center_hours_since_last_detection",
    "firms_local_3x3_has_detection",
    "firms_local_3x3_detection_count",
    "firms_local_3x3_bright_ti4_max",
    "firms_local_3x3_bright_ti4_mean",
    "firms_local_3x3_platform_count",
    "firms_local_3x3_hours_since_last_detection",
    "firms_local_3x3_active_cell_count",
    "terrain_valid",
    "terrain_elevation_m",
    "terrain_slope_degrees",
    "terrain_aspect_defined",
    "terrain_aspect_sin",
    "terrain_aspect_cos",
)

# Explicit contracts keep old fitted models independent of new experiments.
FRONTIER_BASELINE_COLUMNS = tuple(
    name for name in DEFAULT_MODEL_FEATURE_COLUMNS if not name.startswith("firms_center_")
)
LAND_COVER_GROUPS = (
    "needleleaf", "broadleaf", "mixed_forest", "shrubland", "grassland",
    "wetland", "cropland", "other_vegetation", "barren", "urban", "water", "snow_ice",
)
LAND_COVER_COLUMNS = tuple("vegetation_land_cover_" + group for group in LAND_COVER_GROUPS)
LAND_COVER_QUALITY_COLUMNS = (
    "vegetation_land_cover_valid_fraction", "vegetation_land_cover_missing", "vegetation_land_cover_age_days",
)
COVER_COLUMNS = ("vegetation_tree_cover", "vegetation_non_tree_cover", "vegetation_nonvegetated_cover")
COVER_QUALITY_COLUMNS = ("vegetation_cover_valid_fraction", "vegetation_cover_missing", "vegetation_cover_age_days")
SEASONAL_COLUMNS = tuple("vegetation_" + name for name in (
    "ndvi", "evi", "ndvi_valid_fraction", "evi_valid_fraction", "ndvi_missing", "evi_missing",
    "condition_age_days", "ndvi_change", "evi_change", "ndvi_change_missing", "evi_change_missing",
    "ndvi_common_valid_fraction", "evi_common_valid_fraction", "composite_gap_days",
))
STATIC_VEGETATION_COLUMNS = LAND_COVER_COLUMNS + LAND_COVER_QUALITY_COLUMNS + COVER_COLUMNS + COVER_QUALITY_COLUMNS
COVER_DIAGNOSTIC_COLUMNS = ('vegetation_cover_caution_fraction',)
VEGETATION_COLUMNS = STATIC_VEGETATION_COLUMNS + SEASONAL_COLUMNS + COVER_DIAGNOSTIC_COLUMNS
VEGETATION_PRODUCT_VERSIONS = {"NALCMS": "2020 v2", "MOD44B": "061", "MOD13Q1": "061"}
VEGETATION_QA_POLICY = "conservative-vegetation-QA/v1"
VEGETATION_COVER_QA_POLICY = "annual-vegetation-five-clear-periods/v2"
VEGETATION_QA_POLICIES = (VEGETATION_QA_POLICY, VEGETATION_COVER_QA_POLICY)
FEATURE_SETS = {
    "frontier-baseline/v1": FRONTIER_BASELINE_COLUMNS,
    "frontier-vegetation-land-cover/v1": FRONTIER_BASELINE_COLUMNS + LAND_COVER_COLUMNS + LAND_COVER_QUALITY_COLUMNS,
    "frontier-vegetation-static/v1": FRONTIER_BASELINE_COLUMNS + STATIC_VEGETATION_COLUMNS,
    "frontier-vegetation-seasonal/v1": FRONTIER_BASELINE_COLUMNS + STATIC_VEGETATION_COLUMNS + SEASONAL_COLUMNS,
    "frontier-vegetation-static/v2": FRONTIER_BASELINE_COLUMNS + STATIC_VEGETATION_COLUMNS + COVER_DIAGNOSTIC_COLUMNS,
}


def feature_set_for_columns(columns):
    for name, expected in FEATURE_SETS.items():
        if tuple(columns) == expected:
            return name
    raise ValueError("unsupported ordered model feature contract")


def feature_contract(name):
    columns = FEATURE_SETS[name]
    return {"feature_set": name, "feature_columns": list(columns),
            "vegetation_source_requirements": [] if name == "frontier-baseline/v1" else
                ["NALCMS"] if name == "frontier-vegetation-land-cover/v1" else
                ["NALCMS", "MOD44B"] if name.startswith("frontier-vegetation-static/") else ["NALCMS", "MOD44B", "MOD13Q1"],
            "vegetation_units": {c: "days" if c.endswith("_days") else "dimensionless" for c in columns if c in VEGETATION_COLUMNS},
            "vegetation_transform": "area-weighted-source-QA/v1",
            "vegetation_missing_policy": "NaN with explicit flags; no zero imputation",
            "vegetation_availability_policy": ('manifest-pinned-static-context-mode/v2' if name.endswith('/v2') else
                "observation-end-and-proven-publication-at-or-before-cutoff/v1")}


def validate_feature_contract(contract):
    columns = tuple(contract.get("feature_columns", ()))
    name = feature_set_for_columns(columns)
    if name == "frontier-baseline/v1" and "feature_set" not in contract:
        return columns  # historical bundles predate the named registry
    expected = feature_contract(name)
    if any(contract.get(key) != value for key, value in expected.items()):
        raise ValueError("persisted model feature contract mismatch")
    return columns
