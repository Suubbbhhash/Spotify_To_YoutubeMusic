"""Fuzzy matching and closest-available fallback for YouTube Music tracks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from rapidfuzz import fuzz

from .spotify_client import TrackInfo


NOISE_PATTERNS = [
    re.compile(r"\((?:feat\.?|ft\.?|featuring)[^)]*\)", re.IGNORECASE),
    re.compile(r"\[(?:feat\.?|ft\.?|featuring)[^\]]*\]", re.IGNORECASE),
    re.compile(r"\((?:official\s*(?:music\s*)?video|audio|lyric\s*video|lyrics|hd|4k|remastered[^)]*)\)", re.IGNORECASE),
    re.compile(r"\[(?:official\s*(?:music\s*)?video|audio|lyric\s*video|lyrics|hd|4k|remastered[^\]]*)\]", re.IGNORECASE),
    re.compile(r"\s*-\s*(?:remaster(?:ed)?(?:\s*\d{4})?|mono|stereo|radio edit|single version)$", re.IGNORECASE),
]


def normalize_text(text: str) -> str:
    """Strip common noise tags and punctuation for cleaner fuzzy comparison."""
    cleaned = text.strip()
    for pattern in NOISE_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned


def parse_duration_seconds(duration_str: Optional[str]) -> Optional[int]:
    """Parse 'MM:SS' or 'HH:MM:SS' duration strings into seconds."""
    if not duration_str:
        return None
    parts = duration_str.strip().split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    return None


@dataclass
class MatchResult:
    """Best YouTube Music candidate found for a Spotify track."""

    spotify_track: TrackInfo
    video_id: Optional[str]
    matched_title: str
    matched_artists: str
    match_type: str  # "exact", "closest_song", "closest_video", "closest_fallback", "not_found"
    score: float
    search_query: str


def score_candidate(track: TrackInfo, candidate: Dict[str, Any]) -> float:
    """Compute a 0-100 similarity score combining title, artist, and duration."""
    cand_title = candidate.get("title") or ""
    cand_artists_list = [
        a.get("name", "") for a in candidate.get("artists", []) if a.get("name")
    ]
    cand_artists = ", ".join(cand_artists_list)

    norm_target_title = normalize_text(track.title)
    norm_cand_title = normalize_text(cand_title)
    title_score = fuzz.token_sort_ratio(norm_target_title, norm_cand_title)

    # Artist comparison (check against combined artists and primary artist)
    norm_target_artist = normalize_text(track.artists_str)
    norm_primary_artist = normalize_text(track.primary_artist)
    norm_cand_artist = normalize_text(cand_artists)

    if norm_cand_artist:
        artist_score = max(
            fuzz.token_set_ratio(norm_target_artist, norm_cand_artist),
            fuzz.token_set_ratio(norm_primary_artist, norm_cand_artist),
        )
    else:
        # YouTube videos sometimes put the artist in the title instead of artists field
        artist_score = fuzz.partial_ratio(norm_primary_artist, norm_cand_title)

    # Duration closeness bonus/penalty (up to +/- 10 points)
    duration_bonus = 0.0
    target_sec = track.duration_ms / 1000.0 if track.duration_ms else 0.0
    cand_sec = candidate.get("duration_seconds") or parse_duration_seconds(
        candidate.get("duration")
    )
    if target_sec > 0 and cand_sec:
        diff = abs(target_sec - cand_sec)
        if diff <= 3:
            duration_bonus = 8.0
        elif diff <= 10:
            duration_bonus = 4.0
        elif diff > 45:
            duration_bonus = -10.0

    # Weighted composite score clamped to [0, 100]
    composite = 0.62 * title_score + 0.38 * artist_score + duration_bonus
    return max(0.0, min(100.0, round(composite, 2)))


def find_best_ytmusic_match(
    yt: Any,
    track: TrackInfo,
    exact_threshold: float = 82.0,
) -> MatchResult:
    """
    Search YouTube Music for a track.
    1. Search official 'songs' first.
    2. If no high-confidence song match, search 'videos' (covers/lyrics/live/uploads).
    3. If still weak, run a relaxed unfiltered search with primary artist + clean title
       and pick the closest available item so almost nothing is left behind.
    """
    clean_title = normalize_text(track.title) or track.title
    primary_query = f"{track.title} {track.primary_artist}".strip()
    relaxed_query = f"{clean_title} {track.primary_artist}".strip()

    best_candidate: Optional[Dict[str, Any]] = None
    best_score = -1.0
    best_tier = "not_found"
    used_query = primary_query

    search_tiers = [
        ("songs", primary_query, "closest_song"),
        ("videos", primary_query, "closest_video"),
        (None, relaxed_query, "closest_fallback"),
        (None, clean_title, "closest_fallback"),
    ]

    for filt, query, tier_label in search_tiers:
        try:
            results = yt.search(query, filter=filt, limit=5) or []
        except Exception:
            results = []

        valid_results = [r for r in results if r.get("videoId")]
        for cand in valid_results:
            s = score_candidate(track, cand)
            if s > best_score:
                best_score = s
                best_candidate = cand
                best_tier = tier_label
                used_query = query

        # Stop early if we already found a strong song match
        if filt == "songs" and best_score >= exact_threshold:
            return _build_match_result(
                track, best_candidate, "exact", best_score, used_query
            )

        # If we found a decent match in videos or fallback, we can return it
        if best_candidate and best_score >= 65.0:
            break

    if not best_candidate:
        return MatchResult(
            spotify_track=track,
            video_id=None,
            matched_title="",
            matched_artists="",
            match_type="not_found",
            score=0.0,
            search_query=used_query,
        )

    final_type = "exact" if (best_tier == "closest_song" and best_score >= exact_threshold) else best_tier
    return _build_match_result(track, best_candidate, final_type, best_score, used_query)


def _build_match_result(
    track: TrackInfo,
    cand: Dict[str, Any],
    match_type: str,
    score: float,
    query: str,
) -> MatchResult:
    artists = ", ".join(
        a.get("name", "") for a in cand.get("artists", []) if a.get("name")
    )
    return MatchResult(
        spotify_track=track,
        video_id=cand.get("videoId"),
        matched_title=cand.get("title", ""),
        matched_artists=artists,
        match_type=match_type,
        score=score,
        search_query=query,
    )
