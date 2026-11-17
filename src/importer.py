"""End-to-end playlist migration orchestrator."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

from .matcher import MatchResult, find_best_ytmusic_match
from .spotify_client import (
    PlaylistData,
    SpotifyExtractor,
    fetch_public_playlist_no_api,
)
from .ytmusic_client import YouTubeMusicClient


@dataclass
class PlaylistMigrationSummary:
    spotify_name: str
    spotify_url: str
    ytmusic_playlist_id: Optional[str]
    ytmusic_url: Optional[str]
    total_tracks: int
    exact_matches: int
    closest_fallbacks: int
    not_found: int
    matches: List[MatchResult]


def migrate_playlist_data(
    yt_client: YouTubeMusicClient,
    playlists: List[PlaylistData],
    privacy_status: str = "PRIVATE",
    dry_run: bool = False,
    progress_callback: Optional[Callable[[str, int, int, MatchResult], None]] = None,
    report_dir: str = "reports",
) -> List[PlaylistMigrationSummary]:
    """Import pre-extracted PlaylistData objects into YouTube Music."""
    summaries: List[PlaylistMigrationSummary] = []

    for pl_data in playlists:
        matches: List[MatchResult] = []
        total = len(pl_data.tracks)

        for idx, track in enumerate(pl_data.tracks, start=1):
            match = find_best_ytmusic_match(yt_client.yt, track)
            matches.append(match)
            if progress_callback:
                progress_callback(pl_data.name, idx, total, match)

        video_ids = [m.video_id for m in matches if m.video_id]
        yt_playlist_id: Optional[str] = None
        yt_url: Optional[str] = None

        if not dry_run and video_ids:
            yt_playlist_id = yt_client.create_and_populate_playlist(
                title=pl_data.name,
                description=f"{pl_data.description}\n\nImported from {pl_data.source_url}".strip(),
                video_ids=video_ids,
                privacy_status=privacy_status,
            )
            yt_url = f"https://music.youtube.com/playlist?list={yt_playlist_id}"

        exact_count = sum(1 for m in matches if m.match_type == "exact")
        fallback_count = sum(
            1
            for m in matches
            if m.match_type in {"closest_song", "closest_video", "closest_fallback"}
        )
        not_found_count = sum(1 for m in matches if m.match_type == "not_found")

        summaries.append(
            PlaylistMigrationSummary(
                spotify_name=pl_data.name,
                spotify_url=pl_data.source_url,
                ytmusic_playlist_id=yt_playlist_id,
                ytmusic_url=yt_url,
                total_tracks=total,
                exact_matches=exact_count,
                closest_fallbacks=fallback_count,
                not_found=not_found_count,
                matches=matches,
            )
        )

    _save_report(summaries, report_dir)
    return summaries


def migrate_playlists(
    spotify_extractor: Optional[SpotifyExtractor],
    yt_client: YouTubeMusicClient,
    playlist_inputs: List[str],
    privacy_status: str = "PRIVATE",
    dry_run: bool = False,
    progress_callback: Optional[Callable[[str, int, int, MatchResult], None]] = None,
    report_dir: str = "reports",
) -> List[PlaylistMigrationSummary]:
    """Import one or more Spotify playlist links into YouTube Music."""
    playlists: List[PlaylistData] = []
    for pl_input in playlist_inputs:
        if spotify_extractor is not None:
            playlists.append(spotify_extractor.get_playlist(pl_input))
        else:
            playlists.append(fetch_public_playlist_no_api(pl_input))

    return migrate_playlist_data(
        yt_client=yt_client,
        playlists=playlists,
        privacy_status=privacy_status,
        dry_run=dry_run,
        progress_callback=progress_callback,
        report_dir=report_dir,
    )


def _save_report(summaries: List[PlaylistMigrationSummary], report_dir: str) -> Path:
    out_dir = Path(report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = out_dir / f"migration_report_{ts}.json"
    serializable = [asdict(s) for s in summaries]
    report_path.write_text(
        json.dumps(serializable, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report_path
