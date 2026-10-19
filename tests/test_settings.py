from app.db.models.settings import DEFAULT_SETTINGS


def test_video_limits_defaults_match_prd():
    assert DEFAULT_SETTINGS["video_max_mb"] == "100"
    assert DEFAULT_SETTINGS["video_max_seconds"] == "180"
