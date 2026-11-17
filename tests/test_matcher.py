"""Unit tests for URL parsing, title normalization, and closest-match fallback."""

from src.matcher import find_best_ytmusic_match, normalize_text, score_candidate
from src.spotify_client import TrackInfo, extract_playlist_id


class FakeYTMusic:
    """Mock ytmusicapi client for offline testing."""

    def __init__(self, responses):
        self.responses = responses

    def search(self, query, filter=None, limit=5):
        return self.responses.get((filter, query), [])


def test_extract_playlist_id_variants():
    assert (
        extract_playlist_id("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc123")
        == "37i9dQZF1DXcBWIGoYBM5M"
    )
    assert extract_playlist_id("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M") == "37i9dQZF1DXcBWIGoYBM5M"
    assert extract_playlist_id("37i9dQZF1DXcBWIGoYBM5M") == "37i9dQZF1DXcBWIGoYBM5M"


def test_normalize_text_strips_noise():
    assert normalize_text("Blinding Lights (feat. Someone) - Remastered 2020") == "blinding lights"
    assert normalize_text("Bohemian Rhapsody [Official Video]") == "bohemian rhapsody"


def test_exact_song_match_preferred():
    track = TrackInfo(
        title="Blinding Lights",
        artists=["The Weeknd"],
        album="After Hours",
        duration_ms=200000,
        spotify_url="https://open.spotify.com/track/1",
    )
    fake_yt = FakeYTMusic(
        {
            ("songs", "Blinding Lights The Weeknd"): [
                {
                    "videoId": "vid_exact",
                    "title": "Blinding Lights",
                    "artists": [{"name": "The Weeknd"}],
                    "duration": "3:20",
                }
            ]
        }
    )
    res = find_best_ytmusic_match(fake_yt, track)
    assert res.video_id == "vid_exact"
    assert res.match_type == "exact"
    assert res.score >= 82.0


def test_closest_video_fallback_when_song_missing():
    track = TrackInfo(
        title="Rare Unreleased Track (feat. Guest)",
        artists=["Indie Artist"],
        album="Single",
        duration_ms=180000,
        spotify_url="https://open.spotify.com/track/2",
    )
    fake_yt = FakeYTMusic(
        {
            ("songs", "Rare Unreleased Track (feat. Guest) Indie Artist"): [],
            ("videos", "Rare Unreleased Track (feat. Guest) Indie Artist"): [
                {
                    "videoId": "vid_closest",
                    "title": "Rare Unreleased Track (Lyrics)",
                    "artists": [{"name": "Indie Artist"}],
                    "duration": "3:02",
                }
            ],
        }
    )
    res = find_best_ytmusic_match(fake_yt, track)
    assert res.video_id == "vid_closest"
    assert res.match_type == "closest_video"
    assert res.score >= 65.0
