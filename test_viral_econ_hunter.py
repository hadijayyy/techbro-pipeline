import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "hunter", Path(__file__).with_name("viral-econ-hunter.py")
)
assert spec and spec.loader
hunter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hunter)


def test_ranking_never_falls_back_below_s_tier_threshold():
    stories = [
        {"viral_score": 74, "econ_score": 99, "source_count": 3},
        {"viral_score": 99, "econ_score": 74, "source_count": 3},
    ]
    assert hunter._s_tier_stories(stories) == []


def test_ranking_keeps_only_stories_meeting_both_thresholds():
    stories = [
        {"viral_score": 75, "econ_score": 75, "source_count": 2},
        {"viral_score": 99, "econ_score": 74, "source_count": 4},
    ]
    assert hunter._s_tier_stories(stories) == [stories[0]]
