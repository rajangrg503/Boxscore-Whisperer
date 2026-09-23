"""Tests for tools/nightly_slip.py -- the file handling, not the shapes.

Whether the slip and the record agree about a shape is pinned in
tests/test_pipeline_end_to_end.py, against fixtures the real writers
produced. Do not re-test that here with a hand-built projections dict:
that is the mistake this repository has shipped three times.

What IS tested here is the part that only exists on disk -- that
tonight's card, once committed, cannot be rewritten, and that a morning
with nothing to settle says so instead of printing an empty card.
"""

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(REPO_ROOT, "tools", "nightly_slip.py")


def run(*args, **kwargs):
    return subprocess.run([sys.executable, CLI, *args], capture_output=True,
                          text=True, cwd=REPO_ROOT, **kwargs)


@pytest.fixture
def capture(tmp_path):
    """A projections file. Deliberately minimal: these tests are about
    files on disk, and the real shape is covered elsewhere."""
    body = {
        "captured_at": "2026-10-21T18:00:00",
        "season": "2026-27",
        "range_nominal": 0.8,
        "players": {
            "2544": {"n_prior_games": 40, "stats": {
                "PTS": {"projected": 25.0, "low": 20.0, "high": 30.0,
                        "calibrated": True},
                "REB": {"projected": 8.0, "low": 5.0, "high": 11.0,
                        "calibrated": True}}},
            "201939": {"n_prior_games": 40, "stats": {
                "PTS": {"projected": 28.0, "low": 23.0, "high": 33.0,
                        "calibrated": True}}},
        },
    }
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(body))
    return path


def test_it_writes_the_card_and_prints_the_post(capture, tmp_path):
    slips = tmp_path / "slips"
    result = run("--date", "2026-10-21", "--projections", str(capture),
                 "--slips", str(slips))
    assert result.returncode == 0, result.stderr

    written = slips / "2026-10-21.json"
    assert written.exists(), "no card was committed to disk"
    card = json.loads(written.read_text())
    assert card["claims"], "the committed card named nothing"
    assert "2026-10-21" in result.stdout


def test_a_committed_card_is_never_rewritten(capture, tmp_path):
    """The guard the whole design rests on. Rewriting tonight's card
    after the games is how a public record quietly becomes a private
    one."""
    slips = tmp_path / "slips"
    assert run("--date", "2026-10-21", "--projections", str(capture),
               "--slips", str(slips)).returncode == 0
    before = (slips / "2026-10-21.json").read_text()

    again = run("--date", "2026-10-21", "--projections", str(capture),
                "--slips", str(slips), "--size", "1")
    assert again.returncode == 1
    assert "already committed" in again.stderr
    assert (slips / "2026-10-21.json").read_text() == before, (
        "the committed card was overwritten")


def test_the_refusal_is_not_simply_a_refusal_to_ever_write(capture, tmp_path):
    """The control. A guard that refuses everything passes the test
    above while making the tool useless."""
    slips = tmp_path / "slips"
    assert run("--date", "2026-10-21", "--projections", str(capture),
               "--slips", str(slips)).returncode == 0
    assert run("--date", "2026-10-22", "--projections", str(capture),
               "--slips", str(slips)).returncode == 0
    assert (slips / "2026-10-22.json").exists()


def test_a_night_with_no_result_yet_says_so(capture, tmp_path):
    slips, results = tmp_path / "slips", tmp_path / "results"
    results.mkdir()
    assert run("--date", "2026-10-21", "--projections", str(capture),
               "--slips", str(slips)).returncode == 0

    result = run("--settle", "--date", "2026-10-21", "--slips", str(slips),
                 "--results", str(results))
    assert result.returncode == 1
    assert "not been scored" in result.stderr
    assert result.stdout == "", "printed a post for a night with no result"


def test_settling_a_night_that_was_never_carded_is_refused(tmp_path):
    slips, results = tmp_path / "slips", tmp_path / "results"
    slips.mkdir()
    results.mkdir()
    result = run("--settle", "--date", "2026-10-21", "--slips", str(slips),
                 "--results", str(results))
    assert result.returncode == 1
    assert "no card was committed" in result.stderr


def test_the_morning_post_reports_every_committed_claim(capture, tmp_path):
    slips, results = tmp_path / "slips", tmp_path / "results"
    results.mkdir()
    assert run("--date", "2026-10-21", "--projections", str(capture),
               "--slips", str(slips)).returncode == 0
    card = json.loads((slips / "2026-10-21.json").read_text())

    # A record that settles one claim and knows nothing of the others.
    settled_claim = card["claims"][0]
    (results / "2026-10-21.json").write_text(json.dumps({
        "game_date": "2026-10-21",
        "players": {settled_claim["player_id"]: {"status": "scored", "stats": {
            settled_claim["stat"]: {"actual": 31.0, "covered": False}}}},
        "totals": {"scored": 1},
    }))

    result = run("--settle", "--date", "2026-10-21", "--slips", str(slips),
                 "--results", str(results))
    assert result.returncode == 0, result.stderr
    for claim in card["claims"]:
        assert claim["name"] in result.stdout, (
            f"{claim['name']} was committed but left out of the morning post")
    assert "did not play" in result.stdout
    assert "1 of 1 inside the range" not in result.stdout


def test_the_card_is_filed_under_the_us_game_date_not_the_local_one(tmp_path):
    """The bug this file did not catch when the CLI was written.

    Captures run from Australia. A capture at 11:30 on 22 October in
    Sydney is the evening of 21 October in New York, and that is the
    date tools/score_forward_test.py files its result under. A card
    named for the local date would never find a result to settle
    against, and nothing would say so -- no error, just a morning post
    that never appears.
    """
    from engine.season import game_date_for

    body = {
        "captured_at": "2026-10-22T11:30:00+11:00",   # Sydney morning
        "season": "2026-27",
        "range_nominal": 0.8,
        "players": {"2544": {"n_prior_games": 40, "stats": {
            "PTS": {"projected": 25.0, "low": 20.0, "high": 30.0,
                    "calibrated": True}}}},
    }
    capture = tmp_path / "capture.json"
    capture.write_text(json.dumps(body))
    slips = tmp_path / "slips"

    result = run("--projections", str(capture), "--slips", str(slips))
    assert result.returncode == 0, result.stderr

    written = [f.name for f in slips.iterdir()]
    assert written == ["2026-10-21.json"], (
        f"card filed as {written}, but the scorer will write its result "
        f"under {game_date_for(body)}.json")
    # Same source of truth, so the two cannot drift apart.
    assert written[0] == game_date_for(body) + ".json"
    assert "2026-10-22" not in written[0], "filed under the Sydney date"


def test_a_capture_with_no_timestamp_refuses_rather_than_guessing(tmp_path):
    """The control. Falling back to today's date is exactly the bug --
    it would look like it worked."""
    capture = tmp_path / "capture.json"
    capture.write_text(json.dumps({"season": "2026-27", "players": {
        "2544": {"n_prior_games": 40, "stats": {"PTS": {
            "projected": 25.0, "low": 20.0, "high": 30.0}}}}}))
    slips = tmp_path / "slips"

    result = run("--projections", str(capture), "--slips", str(slips))
    assert result.returncode == 1
    assert "no game date" in result.stderr
    assert not slips.exists() or not list(slips.iterdir())


def test_settle_finds_the_newest_card_that_has_a_result(tmp_path):
    """So the morning job never has to work out what "yesterday" means
    from Australia -- which is the same trap in another shape."""
    slips, results = tmp_path / "slips", tmp_path / "results"
    slips.mkdir(); results.mkdir()
    for day in ("2026-10-21", "2026-10-22", "2026-10-23"):
        (slips / f"{day}.json").write_text(json.dumps({
            "game_date": day, "range_nominal": 0.8, "selection": "test",
            "claims": [{"player_id": "2544", "name": "A Player",
                        "stat": "PTS", "projected": 25.0,
                        "low": 20.0, "high": 30.0, "calibrated": True}]}))
    # Only the middle night has been scored.
    (results / "2026-10-22.json").write_text(json.dumps({
        "game_date": "2026-10-22",
        "players": {"2544": {"status": "scored", "stats": {
            "PTS": {"actual": 27.0, "covered": True}}}},
        "totals": {"scored": 1}}))

    result = run("--settle", "--slips", str(slips), "--results", str(results))
    assert result.returncode == 0, result.stderr
    assert "2026-10-22" in result.stdout
    assert "got 27" in result.stdout


def test_settle_with_nothing_scored_says_so(tmp_path):
    slips, results = tmp_path / "slips", tmp_path / "results"
    slips.mkdir(); results.mkdir()
    (slips / "2026-10-21.json").write_text(json.dumps({
        "game_date": "2026-10-21", "claims": []}))
    result = run("--settle", "--slips", str(slips), "--results", str(results))
    assert result.returncode == 1
    assert "no committed card has a result" in result.stderr


def capture_on(day, tmp_path):
    """A capture whose US game date is `day`. 17:00 UTC is the same
    calendar day in New York, so the fixture says what it means."""
    body = {"captured_at": f"{day}T17:00:00+00:00", "season": "2026-27",
            "range_nominal": 0.8,
            "players": {"2544": {"n_prior_games": 40, "stats": {
                "PTS": {"projected": 25.0, "low": 20.0, "high": 30.0,
                        "calibrated": True}}}}}
    path = tmp_path / f"capture-{day}.json"
    path.write_text(json.dumps(body))
    return path


def test_no_card_is_written_before_the_season_opens(tmp_path):
    """Karma, 23 Sep: "in pre season starting 5s will play about 20 mins
    only". True, and it would put the numbers a third high. But the
    binding reason is settlement: engine/game_log.py fetches Regular
    Season and Playoffs only, so no preseason box score ever reaches the
    cache and every preseason claim scores as void.

    A card that can never be graded is a public claim with no result
    coming, which is the opposite of what the nightly post promises."""
    from engine.season import SEASON_OPENS
    slips = tmp_path / "slips"
    result = run("--projections", str(capture_on("2026-10-05", tmp_path)),
                 "--slips", str(slips))

    # Exit 0 on purpose: a fortnight of exhibitions is a normal state,
    # and a job that reports failure nightly for a fortnight gets
    # ignored -- which is how a real failure then goes unnoticed.
    assert result.returncode == 0, result.stderr
    assert "can never be settled" in result.stderr
    assert SEASON_OPENS in result.stderr
    assert not slips.exists() or not list(slips.iterdir())


def test_a_card_is_written_once_the_season_opens(tmp_path):
    """The control. A guard that refused every date would pass the test
    above while making the whole feature dead."""
    from engine.season import SEASON_OPENS
    slips = tmp_path / "slips"
    result = run("--projections", str(capture_on(SEASON_OPENS, tmp_path)),
                 "--slips", str(slips))
    assert result.returncode == 0, result.stderr
    assert [f.name for f in slips.iterdir()] == [f"{SEASON_OPENS}.json"]


def test_the_capture_script_reads_the_opener_rather_than_copying_it():
    """Two copies of a season boundary is how the capture throttles on
    one date and the card refuses on another."""
    from engine.season import SEASON_OPENS
    script = open(os.path.join(REPO_ROOT, "tools", "nightly_capture.sh")).read()
    assert "from engine.season import SEASON_OPENS" in script
    assert SEASON_OPENS not in script, (
        "the opener is hardcoded in the shell as well as engine/season.py")
