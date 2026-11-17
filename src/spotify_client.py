"""Spotify playlist extraction via CSV/JSON exports, public web embed, or Web API."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import requests
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth


PLAYLIST_URL_RE = re.compile(
    r"(?:https?://open\.spotify\.com/playlist/|spotify:playlist:)([A-Za-z0-9]+)"
)


@dataclass
class TrackInfo:
    """Normalized representation of a Spotify track."""

    title: str
    artists: List[str]
    album: str
    duration_ms: int
    spotify_url: str
    is_local: bool = False

    @property
    def primary_artist(self) -> str:
        return self.artists[0] if self.artists else ""

    @property
    def artists_str(self) -> str:
        return ", ".join(self.artists)


@dataclass
class PlaylistData:
    """Extracted Spotify playlist metadata and complete track list."""

    playlist_id: str
    name: str
    description: str
    source_url: str
    tracks: List[TrackInfo]


def extract_playlist_id(playlist_input: str) -> str:
    """Extract Spotify playlist ID from a URL, URI, or raw ID."""
    cleaned = playlist_input.strip()
    match = PLAYLIST_URL_RE.search(cleaned)
    if match:
        return match.group(1)
    return cleaned.split("?")[0].split("/")[-1]


def parse_exportify_csv(
    csv_text: str,
    playlist_name: str = "Imported Spotify Playlist",
) -> PlaylistData:
    """
    Parse an Exportify CSV export (100% free, no Spotify Premium or API key needed,
    supports unlimited tracks).
    """
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
    tracks: List[TrackInfo] = []

    for row in reader:
        title = (row.get("Track Name") or row.get("Name") or "").strip()
        if not title:
            continue
        raw_artists = (
            row.get("Artist Name(s)")
            or row.get("Artist Name")
            or row.get("Artists")
            or ""
        )
        artists = [a.strip() for a in re.split(r"[,;]", raw_artists) if a.strip()]
        album = (row.get("Album Name") or row.get("Album") or "").strip()
        duration_raw = (row.get("Duration (ms)") or row.get("Duration") or "0").strip()
        try:
            duration_ms = int(float(duration_raw))
        except ValueError:
            duration_ms = 0

        track_uri = (row.get("Track URI") or "").strip()
        spotify_url = ""
        if track_uri.startswith("spotify:track:"):
            spotify_url = f"https://open.spotify.com/track/{track_uri.split(':')[-1]}"

        tracks.append(
            TrackInfo(
                title=title,
                artists=artists,
                album=album,
                duration_ms=duration_ms,
                spotify_url=spotify_url,
            )
        )

    return PlaylistData(
        playlist_id="csv_import",
        name=playlist_name,
        description="Imported from Spotify CSV export",
        source_url="https://open.spotify.com",
        tracks=tracks,
    )


def parse_spotify_privacy_json(json_text: str) -> List[PlaylistData]:
    """
    Parse Spotify's official 'Playlist1.json' from Account Privacy Data Download
    (free for all accounts, includes every playlist and all tracks with no cap).
    """
    data = json.loads(json_text)
    raw_playlists = data.get("playlists") if isinstance(data, dict) else data
    if not isinstance(raw_playlists, list):
        raise ValueError("Unrecognized Spotify Playlist JSON format.")

    playlists: List[PlaylistData] = []
    for idx, pl in enumerate(raw_playlists, start=1):
        name = (pl.get("name") or f"Spotify Playlist {idx}").strip()
        items = pl.get("items") or []
        tracks: List[TrackInfo] = []
        for item in items:
            track = item.get("track") or {}
            title = (track.get("trackName") or "").strip()
            if not title:
                continue
            artist = (track.get("artistName") or "").strip()
            album = (track.get("albumName") or "").strip()
            track_uri = (track.get("trackUri") or "").strip()
            spotify_url = (
                f"https://open.spotify.com/track/{track_uri.split(':')[-1]}"
                if track_uri.startswith("spotify:track:")
                else ""
            )
            tracks.append(
                TrackInfo(
                    title=title,
                    artists=[artist] if artist else [],
                    album=album,
                    duration_ms=0,
                    spotify_url=spotify_url,
                )
            )
        if tracks:
            playlists.append(
                PlaylistData(
                    playlist_id=f"privacy_json_{idx}",
                    name=name,
                    description="Imported from Spotify Data Export",
                    source_url="https://open.spotify.com",
                    tracks=tracks,
                )
            )
    return playlists


def fetch_public_playlist_no_api(playlist_link_or_id: str) -> PlaylistData:
    """
    Fetch a public Spotify playlist from its embed page without any API key or Premium.
    Note: Spotify's embed page caps results at 100 tracks; for >100 tracks without Premium,
    use Exportify CSV or Spotify's Playlist1.json export.
    """
    playlist_id = extract_playlist_id(playlist_link_or_id)
    embed_url = f"https://open.spotify.com/embed/playlist/{playlist_id}"
    resp = requests.get(
        embed_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            )
        },
        timeout=20,
    )
    resp.raise_for_status()

    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        resp.text,
        re.DOTALL,
    )
    if not match:
        raise ValueError(
            "Could not extract playlist data from Spotify embed page. "
            "Make sure the playlist is public, or use Exportify CSV / Playlist1.json."
        )

    next_data = json.loads(match.group(1))
    entity = (
        next_data.get("props", {})
        .get("pageProps", {})
        .get("state", {})
        .get("data", {})
        .get("entity", {})
    )
    name = entity.get("name") or f"Imported Playlist {playlist_id}"
    track_list = entity.get("trackList") or []

    tracks: List[TrackInfo] = []
    for item in track_list:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        subtitle = (item.get("subtitle") or "").strip()
        artists = [a.strip() for a in subtitle.split(",") if a.strip()]
        duration_ms = int(item.get("duration") or 0)
        uri = (item.get("uri") or "").strip()
        spotify_url = (
            f"https://open.spotify.com/track/{uri.split(':')[-1]}"
            if uri.startswith("spotify:track:")
            else ""
        )
        tracks.append(
            TrackInfo(
                title=title,
                artists=artists,
                album="",
                duration_ms=duration_ms,
                spotify_url=spotify_url,
            )
        )

    return PlaylistData(
        playlist_id=playlist_id,
        name=name,
        description="Imported from Spotify public playlist",
        source_url=f"https://open.spotify.com/playlist/{playlist_id}",
        tracks=tracks,
    )


class SpotifyExtractor:
    """Fetches public or private Spotify playlists via Web API (requires Premium dev app)."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: Optional[str] = None,
        use_oauth: bool = False,
    ) -> None:
        if use_oauth:
            auth_manager = SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri or "http://127.0.0.1:8888/callback",
                scope="playlist-read-private playlist-read-collaborative user-library-read",
            )
        else:
            auth_manager = SpotifyClientCredentials(
                client_id=client_id,
                client_secret=client_secret,
            )
        self.sp = spotipy.Spotify(
            auth_manager=auth_manager,
            requests_timeout=15,
            retries=3,
        )

    def get_playlist(self, playlist_link_or_id: str) -> PlaylistData:
        """Fetch playlist metadata and ALL tracks across all pages."""
        playlist_id = extract_playlist_id(playlist_link_or_id)
        meta = self.sp.playlist(
            playlist_id,
            fields="id,name,description,external_urls",
        )

        tracks: List[TrackInfo] = []
        results = self.sp.playlist_items(
            playlist_id,
            additional_types=("track",),
            limit=100,
        )

        while results:
            for item in results.get("items", []):
                track = item.get("track")
                if not track or track.get("type") != "track":
                    continue
                artists = [
                    a.get("name", "")
                    for a in track.get("artists", [])
                    if a.get("name")
                ]
                tracks.append(
                    TrackInfo(
                        title=track.get("name", ""),
                        artists=artists,
                        album=(track.get("album") or {}).get("name", ""),
                        duration_ms=int(track.get("duration_ms") or 0),
                        spotify_url=(track.get("external_urls") or {}).get(
                            "spotify", ""
                        ),
                        is_local=bool(item.get("is_local", False)),
                    )
                )

            if results.get("next"):
                results = self.sp.next(results)
            else:
                break

        return PlaylistData(
            playlist_id=playlist_id,
            name=meta.get("name") or f"Imported Playlist {playlist_id}",
            description=meta.get("description") or "",
            source_url=(meta.get("external_urls") or {}).get(
                "spotify", f"https://open.spotify.com/playlist/{playlist_id}"
            ),
            tracks=tracks,
        )

    def get_all_user_playlists(self) -> List[str]:
        """Return all playlist IDs owned or followed by the authenticated user."""
        playlist_ids: List[str] = []
        results = self.sp.current_user_playlists(limit=50)
        while results:
            for pl in results.get("items", []):
                if pl.get("id"):
                    playlist_ids.append(pl["id"])
            if results.get("next"):
                results = self.sp.next(results)
            else:
                break
        return playlist_ids
