"""engine/team_total.py: availability shrinkage, minutes profile and the
expected (minutes-scaled) team total used by Full Matchup."""

import pandas as pd
import pytest

from engine import team_total as tt


def test_availability_shrinks_toward_prior_and_caps():
    assert tt.availability(0, 0) == pytest.approx(0.7)             # no information
    assert tt.availability(10, 10) == pytest.approx((10 + 3.5) / 15)
    assert tt.availability(0, 20) == pytest.approx(3.5 / 25)       # never played
    assert tt.availability(70, 40) == tt.availability(40, 40)      # traded: capped


def test_minutes_profile_counts_regular_season_only():
    log = pd.DataFrame({
        "Game_ID": ["0022500001", "0022500002", "0042500101"],
        "MIN": [30, "34:30", 40],
    })
    mpg, regular = tt.minutes_profile(log)
    assert mpg == pytest.approx((30 + 34.5 + 40) / 3)
    assert regular == 2
    assert tt.minutes_profile(pd.DataFrame()) == (None, 0)
    assert tt.minutes_profile(pd.DataFrame({"PTS": [1]})) == (None, 0)


def test_expected_total_scales_expected_minutes_to_240():
    entries = [
        {"line": {"PTS": 30.0}, "mpg": 36.0, "availability": 1.0},
        {"line": {"PTS": 10.0}, "mpg": 24.0, "availability": 0.5},
        {"line": {"PTS": 99.0}, "mpg": 0.0, "availability": 1.0},    # no minutes: ignored
        {"line": {"PTS": 99.0}, "mpg": 30.0, "availability": 0.0},   # never plays: ignored
    ]
    totals, expected_minutes = tt.expected_team_total(entries, ["PTS"])
    assert expected_minutes == pytest.approx(36 + 12)
    assert totals["PTS"] == pytest.approx((30 + 5) * 240 / 48)


def test_expected_total_handles_nothing_usable():
    assert tt.expected_team_total([], ["PTS"]) == (None, 0.0)


def test_a_full_roster_no_longer_sums_to_320_minutes():
    """Thirteen players averaging 24.6 minutes each 'play' 320 minutes if
    every row is added up; the expected total brings that back to 240."""
    entries = [{"line": {"PTS": 10.0}, "mpg": 320 / 13, "availability": 1.0} for _ in range(13)]
    totals, _ = tt.expected_team_total(entries, ["PTS"])
    assert totals["PTS"] == pytest.approx(130 * 240 / 320)
