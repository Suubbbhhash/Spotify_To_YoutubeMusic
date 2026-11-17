"""YouTube Music authentication, playlist creation, and batched track insertion."""

from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional

import ytmusicapi
from ytmusicapi import YTMusic


def create_browser_auth_from_raw_headers(
    raw_headers: str,
    output_path: str = "browser.json",
) -> str:
    """Convert raw copied browser request headers into ytmusicapi browser.json."""
    out = Path(output_path)
    ytmusicapi.setup(filepath=str(out), headers_raw=raw_headers)
    return str(out)


class YouTubeMusicClient:
    """Wrapper around ytmusicapi for resilient playlist creation and population."""

    def __init__(self, auth_file: str = "browser.json") -> None:
        if not Path(auth_file).exists():
            raise FileNotFoundError(
                f"YouTube Music auth file '{auth_file}' not found. "
                "Generate browser.json or oauth.json first."
            )
        self.yt = YTMusic(auth_file)

    def create_and_populate_playlist(
        self,
        title: str,
        description: str,
        video_ids: List[str],
        privacy_status: str = "PRIVATE",
        batch_size: int = 50,
    ) -> str:
        """Create a YouTube Music playlist and add all video IDs in safe batches."""
        safe_desc = (description or "Imported from Spotify")[:500]
        playlist_id = self.yt.create_playlist(
            title=title,
            description=safe_desc,
            privacy_status=privacy_status,
        )

        # Deduplicate while preserving order
        seen = set()
        unique_ids: List[str] = []
        for vid in video_ids:
            if vid and vid not in seen:
                seen.add(vid)
                unique_ids.append(vid)

        for idx in range(0, len(unique_ids), batch_size):
            chunk = unique_ids[idx : idx + batch_size]
            self._add_batch_with_retry(playlist_id, chunk)
            time.sleep(0.4)

        return playlist_id

    def _add_batch_with_retry(
        self,
        playlist_id: str,
        video_ids: List[str],
        max_retries: int = 3,
    ) -> None:
        last_err: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                self.yt.add_playlist_items(
                    playlistId=playlist_id,
                    videoIds=video_ids,
                    duplicates=False,
                )
                return
            except Exception as exc:
                last_err = exc
                time.sleep(1.5 * (attempt + 1))

        # Fallback: add one-by-one if a single bad videoId poisoned the batch
        for vid in video_ids:
            try:
                self.yt.add_playlist_items(
                    playlistId=playlist_id,
                    videoIds=[vid],
                    duplicates=False,
                )
            except Exception:
                continue
        if last_err:
            # Individual fallback handled what it could
            pass
