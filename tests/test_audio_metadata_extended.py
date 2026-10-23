"""Tests for the audio status parser (no physical AudioServer needed)."""

from custom_components.loxone.audio_metadata import (
    AudioMetadata,
    parse_audio_status,
    parse_player_id,
)

BASE = "http://192.0.2.5:7091"


def test_player_ids():
    assert parse_player_id(0) == 0
    assert parse_player_id(" 12 ") == 12
    assert parse_player_id(-1) is None
    assert parse_player_id(True) is None
    assert parse_player_id("abc") is None


def test_spotify_status():
    status = {
        "status_result": [
            {
                "title": "Example track",
                "artist": "Example artist",
                "album": "Example album",
                "coverurl": "/covers/10.jpg",
                "audiotype": 5,
                "source": "Spotify",
                "duration": 210,
                "time": 19,
            }
        ]
    }
    assert parse_audio_status(status, BASE) == AudioMetadata(
        title="Example track",
        artist="Example artist",
        album="Example album",
        image_url=BASE + "/covers/10.jpg",
        source="Spotify",
        duration=210.0,
        position=19.0,
    )


def test_nested_track():
    status = {
        "audio/3/status_result": {
            "track": {"title": "A", "artist": "B", "coverUrl": "https://example.org/c.jpg"},
            "source": "airplay",
        }
    }
    meta = parse_audio_status(status, BASE)
    assert meta.title == "A"
    assert meta.artist == "B"
    assert meta.source == "airplay"
    assert meta.image_url == "https://example.org/c.jpg"


def test_unsupported_or_empty():
    assert parse_audio_status({"error": "unauthorized"}, BASE) is None
    assert parse_audio_status({"status_result": []}, BASE) is None
    assert parse_audio_status({"unexpected": 123}, BASE) is None
    assert parse_audio_status({"status_result": {"title": ""}}, BASE) == AudioMetadata()


def test_does_not_emit_unsafe_cover_urls():
    assert parse_audio_status({"title": "A", "coverurl": "javascript:alert(1)"}, BASE).image_url is None
