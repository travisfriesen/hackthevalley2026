"""Room geometry: ray casting, person estimate, presence from baselines."""
import math

import pytest
from room_math import (PresenceEstimator, PERSON_RADIUS_M, ray_hit,
                       ray_intersection, ray_point, simplify, snap_to_polygon)

ROOM = [[0, 0], [4, 0], [4, 3], [0, 3]]
S1 = {"x": 0.15, "y": 1.5, "heading_deg": 0}     # aims east
S2 = {"x": 3.85, "y": 1.5, "heading_deg": 180}    # aims west


def test_simplify_drops_collinear_points():
    pts = [(0, 0), (0.1, 0.0), (0.2, 0.0), (1.0, 0.05), (2.0, 1.0), (2.1, 1.0)]
    out = simplify(pts, tolerance=0.08)
    assert len(out) <= 4
    assert out[0] == pts[0] and out[-1] == pts[-1]


def test_ray_cast_hits_wall_and_person():
    walls = [ROOM]
    # empty: east ray from x=0.15 hits the far wall at x=4
    assert ray_hit(0.15, 1.5, 0.0, walls, [], 4.0) == pytest.approx(3.85, abs=1e-6)
    # person standing mid-room blocks it earlier (sensor sits at x=0.15)
    person = [(2.0, 1.5, PERSON_RADIUS_M)]
    assert ray_hit(0.15, 1.5, 0.0, walls, person, 4.0) == pytest.approx(
        2.0 - PERSON_RADIUS_M - 0.15, abs=1e-6)
    # ray pointed away from every wall finds nothing
    assert ray_hit(0.15, 1.5, 0.0, [], [], 4.0) == pytest.approx(4.0)
    # north-heading ray from mid-wall hits y=0 boundary
    assert ray_hit(2.0, 1.5, -math.pi / 2, walls, [], 4.0) == pytest.approx(
        1.5, abs=1e-6)


def test_ray_point_and_intersection():
    hit = ray_point(S1, 3.85)
    assert hit[0] == pytest.approx(4.0)
    assert hit[1] == pytest.approx(1.5)
    # both sensors see the person at x=2 -> rays cross at the person
    d1 = 2.0 - 0.15 - PERSON_RADIUS_M
    d2 = 3.85 - 2.0 - PERSON_RADIUS_M
    cross = ray_intersection(S1, d1, S2, d2)
    assert cross is not None
    assert cross[0] == pytest.approx(2.0, abs=0.05)
    assert cross[1] == pytest.approx(1.5, abs=0.05)
    # parallel rays on different lines never meet; same-line but same
    # direction don't either - only facing rays meet between the hits
    assert ray_intersection(S1, 1.0, dict(S1, y=2.5), 1.0) is None
    assert ray_intersection(S1, 1.0, dict(S1, x=2.0), 1.0) is None


def test_snap_door_to_nearest_wall():
    door = snap_to_polygon(ROOM, (2.0, -0.3))
    assert door[0] == pytest.approx(2.0)
    assert door[1] == pytest.approx(0.0)


def test_presence_from_baseline_deviation():
    est = PresenceEstimator(deviation_cm=30, enter_s=1.5, exit_s=2.0,
                            max_distance_m=4.0, baseline_s=60)
    t = 0.0
    # warm the baseline: empty room, steady 3.85 m readings
    for i in range(30):
        t = i * 0.5
        assert est.update(t, [3.85, 3.85]) is False
    # someone walks into the beam
    for i in range(6):
        t += 0.5
        est.update(t, [1.60, 3.85])
    assert est.present is True
    assert est.deviating == {0}
    # beam clears again
    for i in range(8):
        t += 0.5
        est.update(t, [3.85, 3.85])
    assert est.present is False


def test_presence_needs_a_brief_sustained_break():
    est = PresenceEstimator(deviation_cm=30, enter_s=1.5, exit_s=2.0,
                            max_distance_m=4.0, baseline_s=60)
    for i in range(30):
        assert est.update(i * 0.5, [3.85, 3.85]) is False
    # a 0.5 s blip (someone walking past) must not read as presence
    assert est.update(15.2, [1.0, 3.85]) is False
    assert est.update(15.7, [3.85, 3.85]) is False
    assert est.present is False
    # no-echo readings are ignored, not treated as deviation
    assert est.update(16.0, [4.0, 3.85]) is False
    assert est.present is False
