from pyproj import Transformer
from shapely.geometry import box
from wildfire_data.providers.landscape.collect import normalize_roads


def features(**properties):
    inverse = Transformer.from_crs('ESRI:102008', 'EPSG:4326', always_xy=True)
    return [{'type': 'Feature', 'geometry': {'type': 'LineString', 'coordinates': [inverse.transform(10, 50), inverse.transform(90, 50)]},
             'properties': {'id': 'test-road', **properties}}]


def test_offline_preparation_preserves_unknown_grade_width_surface():
    roads = normalize_roads(features(), box(0, 0, 100, 100), preserve_unknown_grade=True)['features']
    assert len(roads) == 1
    assert roads[0]['properties']['at_grade'] is None
    assert roads[0]['properties']['width_m'] is None
    assert roads[0]['properties']['surface'] is None


def test_known_bridge_does_not_become_unknown_when_level_missing():
    roads = normalize_roads(features(road_flags=[{'values': ['is_bridge']}]), box(0, 0, 100, 100), preserve_unknown_grade=True)['features']
    assert roads[0]['properties']['at_grade'] is False


def test_missing_scoped_level_does_not_default_to_ground():
    roads = normalize_roads(features(level_rules=[{'between': [0, .5], 'value': 0}]), box(0, 0, 100, 100), preserve_unknown_grade=True)['features']
    assert [r['properties']['at_grade'] for r in roads] == [True, None]


def test_legacy_normalization_default_is_unchanged():
    roads = normalize_roads(features(), box(0, 0, 100, 100))['features']
    assert roads[0]['properties']['at_grade'] is True
