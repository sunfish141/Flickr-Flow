import tempfile
import unittest
from pathlib import Path
from shapely.geometry import box, LineString, shape
from shapely.ops import unary_union

from model.fuel_fixture import bundle, policy
from wildfire_data.model.local_spread import LocalSpreadModel
from wildfire_data.providers.landscape.tiles import LandscapeMosaic


class LocalSpreadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def model(self, **kwargs):
        return LocalSpreadModel(bundle(self.temp.name, **kwargs), policy())

    def test_road_splits_inside_a_mesh_element_and_never_teleports_across(self):
        m = self.model(roads=[(LineString([(45, 0), (45, 120)]), {})])
        seed = next(i for i, c in enumerate(m.centers) if c.x < 30 and 30 < c.y < 60)
        arrivals = m.arrivals((seed,))
        self.assertTrue(all(m.centers[i].x < 45 for i in arrivals))
        self.assertTrue(any(30 < m.centers[i].x < 45 for i in arrivals))
        self.assertEqual(m.frame((seed,), 60), m.frame((seed,), 60))

    def test_front_routes_around_road_end_and_takes_longer(self):
        m = self.model(roads=[(LineString([(45, 30), (45, 90)]), {})])
        seed = next(i for i, c in enumerate(m.centers) if c.x < 30 and 30 < c.y < 60)
        arrivals = m.arrivals((seed,))
        target = next(i for i, c in enumerate(m.centers) if 60 < c.x < 90 and 30 < c.y < 60)
        self.assertIn(target, arrivals)
        self.assertGreater(arrivals[target], m.centers[seed].distance(m.centers[target]))

    def test_water_urban_and_unknown_do_not_become_vegetation(self):
        m = self.model(cover=[(box(0, 0, 30, 120), 'grassland'), (box(30, 0, 60, 120), 'water'),
            (box(60, 0, 90, 120), 'urban')])
        self.assertTrue(all(p.geometry.bounds[2] <= 30 for p in m.patches))
        lon, lat = m.sampler.to_geo.transform(75, 45)
        with self.assertRaisesRegex(ValueError, 'supported vegetation'):
            m.seed_ids([(lon, lat)])

    def test_diagonal_corner_contact_is_not_a_route(self):
        m = self.model(cover=[(box(0, 0, 30, 30), 'grassland'), (box(30, 30, 60, 60), 'grassland')])
        self.assertEqual(m.arrivals((0,)), {0: 0.})

    def test_burned_footprint_never_reactivates_and_preserves_holes(self):
        geometry = box(0, 0, 120, 120).difference(box(30, 30, 90, 90))
        m = self.model(cover=[(geometry, 'grassland')])
        frame = m.frame((0,), 500)
        self.assertEqual(frame['active_patch_count'], 0)
        self.assertEqual(frame['burned_patch_count'], len(m.patches))
        burned = unary_union([shape(f['geometry']) for f in frame['perimeters']['features']])
        self.assertGreater(sum(len(p.interiors) for p in ([burned] if burned.geom_type == 'Polygon' else burned.geoms)), 0)
        self.assertEqual(m.frame((0,), 600)['burned_patch_count'], frame['burned_patch_count'])

    def test_each_patch_burns_out_after_its_own_ignition_time(self):
        m = self.model(bounds=(0,0,60,30))
        seed = min(range(len(m.centers)), key=lambda i: m.centers[i].x)
        self.assertEqual(sorted(m.arrivals((seed,)).values()), [0., 30.])
        self.assertEqual(m.frame((seed,), 119)['burned_patch_count'], 0)
        frame = m.frame((seed,), 120)
        self.assertEqual((frame['active_patch_count'], frame['burned_patch_count']), (1,1))
        self.assertEqual(list(frame['cells'].values()), [{'active_area_m2': 900., 'burned_area_m2': 900.}])
        self.assertEqual(m.frame((seed,), 149)['active_patch_count'], 1)
        self.assertEqual(m.frame((seed,), 150)['burned_patch_count'], 2)
        self.assertEqual(m.frame((seed,), 1440)['active_patch_count'], 0)

    def test_unknown_width_only_changes_geometry_with_explicit_assumption(self):
        sampler = bundle(self.temp.name, roads=[(LineString([(45, 0), (45, 120)]), {'width_m': None})])
        a = LocalSpreadModel(sampler, policy())
        b = LocalSpreadModel(sampler, policy(unknown_road_width_m=6))
        self.assertTrue(a.road_surface.is_empty)
        self.assertGreater(b.road_surface.area, 0)
        self.assertNotEqual(a.identity, b.identity)

    def test_tunnels_do_not_cut_surface_fuel(self):
        m = self.model(roads=[(LineString([(45, 0), (45, 120)]), {'at_grade': False})])
        self.assertTrue(m.road_surface.is_empty)
        self.assertEqual(len(m.arrivals((0,))), len(m.patches))

    def test_wind_reversal_changes_directional_travel_and_spotting_is_opt_in(self):
        s = bundle(self.temp.name, roads=[(LineString([(45, 0), (45, 120)]), {})])
        a = LocalSpreadModel(s, policy(wind_east_m_s=5))
        b = LocalSpreadModel(s, policy(wind_east_m_s=5, spotting_distance_per_wind_m_s=20, max_spotting_distance_m=100))
        seed = next(i for i, c in enumerate(a.centers) if 30 < c.x < 45 and 30 < c.y < 60)
        self.assertTrue(all(a.centers[i].x < 45 for i in a.arrivals((seed,))))
        self.assertTrue(any(b.centers[i].x > 45 for i in b.arrivals((seed,))))
        c = LocalSpreadModel(s, policy(wind_east_m_s=-5, spotting_distance_per_wind_m_s=20, max_spotting_distance_m=100))
        self.assertTrue(all(c.centers[i].x < 45 for i in c.arrivals((seed,))))

    def test_residence_time_and_domain_limits_are_enforced(self):
        s = bundle(self.temp.name)
        m = LocalSpreadModel(s, policy(residence_minutes=1))
        self.assertEqual(m.arrivals((0,)), {0: 0.})
        with self.assertRaises(ValueError):
            m.frame((0,), float('nan'))
        with self.assertRaises(ValueError):
            m.frame((999999,), 10)
        with self.assertRaises(ValueError):
            LocalSpreadModel(s, policy(), max_patches=2)

    def test_source_only_needs_to_survive_until_front_reaches_shared_gate(self):
        s = bundle(self.temp.name, bounds=(0,0,300,100))
        m = LocalSpreadModel(s, policy(residence_minutes=60), mesh_m=100)
        arrivals = m.arrivals((0,))
        self.assertEqual(arrivals, {0: 0., 1: 100., 2: 200.})

    def test_fuel_specific_patch_duration_applies_to_burnout_and_spread(self):
        from wildfire_data.model.local_spread import VEGETATED
        durations = {k: 240 if k == 'needleleaf' else 60 for k in VEGETATED}
        sampler = bundle(self.temp.name, bounds=(0,0,90,30),
                         cover=[(box(0,0,30,30),'grassland'), (box(30,0,90,30),'needleleaf')])
        m = LocalSpreadModel(sampler, policy(residence_minutes_by_fuel=durations))
        seed = min(range(len(m.centers)), key=lambda i: m.centers[i].x)
        at_120 = m.frame((seed,), 120)
        self.assertEqual((at_120['burned_patch_count'], at_120['active_patch_count']), (1,2))
        self.assertEqual(m.frame((seed,), 300)['active_patch_count'], 0)
        short = LocalSpreadModel(sampler, policy(residence_minutes_by_fuel={**durations, 'grassland': 1}))
        self.assertEqual(short.arrivals((seed,)), {seed: 0.})
        self.assertNotEqual(m.identity, short.identity)
        with self.assertRaises(ValueError):
            policy(residence_minutes_by_fuel={'grassland': 120})

    def test_joined_tiles_match_direct_mesh_with_roads_holes_and_wind(self):
        root = Path(self.temp.name)
        bounds = [(0,0,120,120),(120,0,240,120)]
        land = box(0,0,240,120).difference(box(80,30,160,60))
        roads = [(LineString([(125,0),(125,90)]), {})]
        samplers = [bundle(root/str(i), bounds=b, cover=[(land.intersection(box(*b)), 'grassland')], roads=roads)
                    for i,b in enumerate(bounds)]
        p = policy(wind_east_m_s=3, wind_north_m_s=-1)
        components = [LocalSpreadModel(s, p) for s in samplers]
        sampler = LandscapeMosaic(samplers)
        joined = LocalSpreadModel.join(sampler, p, components)
        direct = LocalSpreadModel(sampler, p)
        coordinates = [sampler.to_geo.transform(20,100)]
        for minute in [0,60,120,240,720]:
            a = joined.frame(joined.seed_ids(coordinates), minute)
            b = direct.frame(direct.seed_ids(coordinates), minute)
            self.assertEqual(a['cells'], b['cells'])
            for status in ['active','burned']:
                ga = unary_union([shape(f['geometry']) for f in a['perimeters']['features'] if f['properties']['status']==status])
                gb = unary_union([shape(f['geometry']) for f in b['perimeters']['features'] if f['properties']['status']==status])
                self.assertLess(ga.symmetric_difference(gb).area, 1e-8)
                self.assertLess(ga.intersection(joined.road_surface).area, 1e-8)

    def test_empty_neighbor_tile_and_wrong_policy_cannot_create_fuel_or_edges(self):
        a = bundle(Path(self.temp.name)/'a')
        b = bundle(Path(self.temp.name)/'b', bounds=(120,0,240,120), cover=[(box(120,0,240,120),'water')])
        p = policy()
        first = LocalSpreadModel(a,p)
        empty = LocalSpreadModel(b,p,allow_empty=True)
        joined = LocalSpreadModel.join(LandscapeMosaic([a,b]),p,[first,empty])
        self.assertEqual(len(joined.patches),len(first.patches))
        self.assertEqual(joined.adjacency,first.adjacency)
        with self.assertRaisesRegex(ValueError,'policy or mesh'):
            LocalSpreadModel.join(LandscapeMosaic([a,b]),policy(unknown_road_width_m=4),[first,empty])
