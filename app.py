"""Flagship Streamlit UI for importing Spotify playlists to YouTube Music (No Premium Required)."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shlex
from pathlib import Path
from typing import Dict, List, Tuple

import streamlit as st
from dotenv import load_dotenv
from ytmusicapi import YTMusic

from src.importer import migrate_playlist_data, migrate_playlists
from src.matcher import MatchResult
from src.spotify_client import (
    PlaylistData,
    SpotifyExtractor,
    parse_exportify_csv,
    parse_spotify_privacy_json,
)
from src.ytmusic_client import YouTubeMusicClient, create_browser_auth_from_raw_headers


load_dotenv()

st.set_page_config(
    page_title="Spotify → YouTube Music Migrator",
    page_icon="🎵",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Flagship Dark Glassmorphism Theme
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    .stApp {
        background: radial-gradient(circle at 12% 10%, rgba(29, 185, 84, 0.10), transparent 38%),
                    radial-gradient(circle at 88% 12%, rgba(255, 0, 51, 0.10), transparent 38%),
                    #0b0e14;
        color: #f1f5f9;
    }
    .hero-card {
        background: linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.02));
        border: 1px solid rgba(255, 255, 255, 0.12);
        border-radius: 18px;
        padding: 1.6rem 2rem;
        margin-bottom: 1.25rem;
        backdrop-filter: blur(12px);
    }
    .hero-badge {
        display: inline-block;
        padding: 0.25rem 0.75rem;
        border-radius: 999px;
        font-size: 0.78rem;
        font-weight: 600;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        background: linear-gradient(90deg, rgba(29,185,84,0.22), rgba(255,0,51,0.22));
        border: 1px solid rgba(255,255,255,0.16);
        color: #e2e8f0;
        margin-bottom: 0.65rem;
    }
    .hero-title {
        font-size: 2.05rem;
        font-weight: 800;
        margin: 0;
        background: linear-gradient(90deg, #1DB954 0%, #f8fafc 50%, #FF0033 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .hero-sub {
        color: #94a3b8;
        font-size: 0.98rem;
        margin-top: 0.45rem;
        margin-bottom: 0;
    }
    .step-card {
        background: rgba(255, 255, 255, 0.035);
        border: 1px solid rgba(255, 255, 255, 0.09);
        border-left: 3px solid #1DB954;
        border-radius: 12px;
        padding: 0.95rem 1.15rem;
        margin-bottom: 0.75rem;
    }
    .step-card.yt {
        border-left-color: #FF0033;
    }
    .step-title {
        font-weight: 700;
        font-size: 0.96rem;
        color: #f8fafc;
        margin-bottom: 0.3rem;
    }
    .step-body {
        font-size: 0.88rem;
        color: #cbd5e1;
        line-height: 1.5;
    }
    .status-pill {
        display: inline-flex;
        align-items: center;
        gap: 0.45rem;
        padding: 0.32rem 0.8rem;
        border-radius: 999px;
        font-size: 0.82rem;
        font-weight: 600;
        margin-bottom: 0.75rem;
    }
    .status-ok {
        background: rgba(29, 185, 84, 0.16);
        border: 1px solid rgba(29, 185, 84, 0.45);
        color: #86efac;
    }
    .status-warn {
        background: rgba(245, 158, 11, 0.15);
        border: 1px solid rgba(245, 158, 11, 0.45);
        color: #fcd34d;
    }
    div[data-testid="stMetric"] {
        background: rgba(255, 255, 255, 0.04);
        border: 1px solid rgba(255, 255, 255, 0.09);
        border-radius: 12px;
        padding: 0.85rem 1rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Helpers: Auto-parse Copy as cURL (bash) + Strip content-encoding: gzip
# ---------------------------------------------------------------------------
def normalize_curl_or_headers(raw_input: str) -> str:
    """
    Accept either raw 'header: value' lines OR a full 'Copy as cURL (bash)'
    command from Chrome/Edge DevTools and return clean headers for ytmusicapi.
    """
    text = raw_input.strip()
    if not text:
        return ""

    if text.lstrip().startswith("curl "):
        # Join line continuations (`\` at EOL) and parse POSIX shell tokens
        unwrapped = re.sub(r"\\\r?\n\s*", " ", text)
        try:
            tokens = shlex.split(unwrapped)
        except ValueError:
            tokens = []

        headers: List[str] = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok in ("-H", "--header") and i + 1 < len(tokens):
                headers.append(tokens[i + 1])
                i += 2
            elif tok in ("-b", "--cookie") and i + 1 < len(tokens):
                headers.append(f"cookie: {tokens[i + 1]}")
                i += 2
            else:
                i += 1
        if headers:
            text = "\n".join(headers)

    # Strip content-encoding (e.g. gzip) so ytmusicapi doesn't send uncompressed JSON
    # labeled as gzip, which causes YouTube Music create_playlist to return an empty 400.
    cleaned_lines = [
        line
        for line in text.splitlines()
        if line.strip() and not line.lower().lstrip().startswith("content-encoding:")
    ]
    return "\n".join(cleaned_lines)


def save_clean_browser_auth(raw_input: str, output_path: str) -> str:
    """Parse raw headers or cURL, generate browser.json, and ensure content-encoding is removed."""
    cleaned_headers = normalize_curl_or_headers(raw_input)
    saved_path = create_browser_auth_from_raw_headers(cleaned_headers, output_path)

    auth_file = Path(saved_path)
    data = json.loads(auth_file.read_text(encoding="utf-8"))
    data.pop("content-encoding", None)
    data.pop("Content-Encoding", None)
    auth_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return saved_path


def test_ytmusic_connection(auth_path: str) -> Tuple[bool, str]:
    """Verify that browser.json/oauth.json can read the user's YouTube Music library."""
    try:
        yt = YTMusic(auth_path)
        playlists = yt.get_library_playlists(limit=1)
        return True, f"Connected! Found {len(playlists)} library playlist(s)."
    except Exception as exc:
        return False, f"Auth check failed: {exc}"


def summaries_to_csv(summaries) -> str:
    """Convert migration results into a downloadable CSV audit log."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Playlist",
            "Spotify Track",
            "Spotify Artists",
            "YouTube Match",
            "YouTube Artists",
            "Match Type",
            "Confidence (%)",
            "YouTube Video ID",
        ]
    )
    for s in summaries:
        for m in s.matches:
            writer.writerow(
                [
                    s.spotify_name,
                    m.spotify_track.title,
                    m.spotify_track.artists_str,
                    m.matched_title,
                    m.matched_artists,
                    m.match_type,
                    m.score,
                    m.video_id or "",
                ]
            )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Hero Header
# ---------------------------------------------------------------------------
st.markdown(
    """
    <div class="hero-card">
        <div class="hero-badge">Zero Track Cap • Free Spotify Supported • Closest-Match Fallback</div>
        <h1 class="hero-title">Spotify → YouTube Music Migrator</h1>
        <p class="hero-sub">
            Import every song from your Spotify playlists into YouTube Music using multi-tier
            RapidFuzz matching—automatically falling back to the closest available audio, lyric video, or upload.
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Sidebar: YouTube Music Auth & Settings
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("1. YouTube Music Auth")
    auth_file_path = st.text_input(
        "Auth JSON Path",
        value=os.getenv("YTMUSIC_AUTH_FILE", "browser.json"),
        help="Default is browser.json in your project folder (or oauth.json).",
    )

    auth_exists = Path(auth_file_path).exists()
    if auth_exists:
        st.markdown(
            f'<div class="status-pill status-ok">● Auth file found: {auth_file_path}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="status-pill status-warn">● No auth file yet — paste cURL below</div>',
            unsafe_allow_html=True,
        )

    auth_mode = st.radio(
        "Setup Mode",
        ["Use existing browser.json / oauth.json", "Paste cURL (bash) or Raw Headers"],
    )

    if auth_mode == "Paste cURL (bash) or Raw Headers":
        raw_input = st.text_area(
            "Paste 'Copy as cURL (bash)' or raw headers",
            height=160,
            placeholder="curl --url 'https://music.youtube.com/youtubei/v1/browse?...' \\\n  -H 'authorization: ...' \\\n  -b '...'",
        )
        if st.button("Auto-Parse & Save browser.json", use_container_width=True):
            if not raw_input.strip():
                st.warning("Paste your cURL (bash) or headers first.")
            else:
                try:
                    out = save_clean_browser_auth(raw_input, auth_file_path)
                    st.success(f"Saved clean auth to {out} (stripped content-encoding).")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not parse headers: {exc}")

    if auth_exists and st.button("Test YouTube Music Connection", use_container_width=True):
        ok, msg = test_ytmusic_connection(auth_file_path)
        if ok:
            st.success(msg)
        else:
            st.error(msg)

    st.divider()
    st.header("2. Import Options")
    privacy = st.selectbox("Playlist Privacy", ["PRIVATE", "UNLISTED", "PUBLIC"])
    dry_run = st.checkbox("Dry Run (match only, don't create playlists)", value=False)


# ---------------------------------------------------------------------------
# Built-In Step-by-Step Guide (Every Real Pitfall Solved)
# ---------------------------------------------------------------------------
with st.expander("Step-by-Step Setup Guide (Read First — Avoid Common Pitfalls)", expanded=not auth_exists):
    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown(
            """
            <div class="step-card">
                <div class="step-title">Step 1 — Export Your Spotify Playlists (100% Free, No Cap)</div>
                <div class="step-body">
                    Spotify's February 2026 Web API rules require <strong>Spotify Premium</strong> for Developer Mode apps.
                    On a free account, use either:<br>
                    • <strong><a href="https://exportify.net/" target="_blank">Exportify</a></strong>: Log in with Spotify and click <em>Export</em> on any playlist to download a <code>.csv</code> (no track limit).<br>
                    • <strong><a href="https://www.spotify.com/account/privacy/" target="_blank">Spotify Privacy Data</a></strong>: Request your data and upload <code>Playlist1.json</code> to import every playlist at once.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_right:
        st.markdown(
            """
            <div class="step-card yt">
                <div class="step-title">Step 2 — Grab YouTube Music Auth via Copy as cURL (bash)</div>
                <div class="step-body">
                    1. Open <a href="https://music.youtube.com" target="_blank">music.youtube.com</a> (signed in) → press <code>Ctrl+Shift+I</code> → <strong>Network</strong> tab.<br>
                    2. Type <code>/browse</code> in the filter box, then click <strong>Library</strong>.<br>
                    3. Click a <code>browse?...</code> row and verify <strong>POST</strong> + <strong>Status 200</strong> (skip <code>OPTIONS</code>/<code>GET</code>).<br>
                    4. Right-click that row → <strong>Copy</strong> → <strong>Copy as cURL (bash)</strong> (Chrome/Edge hides <em>Copy request headers</em>).<br>
                    5. Paste the full cURL directly into the sidebar and click <strong>Auto-Parse & Save browser.json</strong>—the app automatically extracts <code>cookie</code>, <code>x-goog-authuser</code>, and strips <code>content-encoding: gzip</code> so <code>create_playlist</code> won't fail.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Main Import Tabs
# ---------------------------------------------------------------------------
tab_files, tab_links, tab_api = st.tabs(
    [
        "Free & Unlimited (Exportify CSV / Playlist1.json)",
        "Public Playlist Links (No API Key, ≤100 tracks)",
        "Spotify Web API (Premium Only)",
    ]
)

with tab_files:
    st.markdown(
        "Upload one or more `.csv` files from [Exportify](https://exportify.net/) or `Playlist1.json` "
        "from your [Spotify Privacy Data Download](https://www.spotify.com/account/privacy/)."
    )
    uploaded_files = st.file_uploader(
        "Drop .csv or .json files here",
        type=["csv", "json"],
        accept_multiple_files=True,
    )
    start_files = st.button("Import Uploaded Playlists", type="primary", use_container_width=True)

with tab_links:
    st.markdown(
        "Paste **public** Spotify playlist URLs (one per line). No API key needed "
        "*(Spotify's public embed page caps at 100 tracks—use Exportify CSV for larger playlists)*."
    )
    playlist_text = st.text_area(
        "Public Spotify Playlist URLs",
        height=130,
        placeholder="https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
    )
    start_links = st.button("Import Public Links", type="primary", use_container_width=True)

with tab_api:
    st.info("Spotify's February 2026 rules require an active Spotify Premium account for Developer Mode apps.")
    c1, c2 = st.columns(2)
    client_id = c1.text_input(
        "Spotify Client ID (32-char hex from Developer Dashboard)",
        value=os.getenv("SPOTIFY_CLIENT_ID", ""),
        type="password",
    )
    client_secret = c2.text_input(
        "Spotify Client Secret",
        value=os.getenv("SPOTIFY_CLIENT_SECRET", ""),
        type="password",
    )
    api_playlist_text = st.text_area(
        "Spotify Playlist URLs (API Mode)",
        height=100,
        placeholder="https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M",
    )
    import_all_mine = st.checkbox("Import ALL playlists from my Spotify account (uses OAuth)")
    start_api = st.button("Import via Spotify API", type="primary", use_container_width=True)


# ---------------------------------------------------------------------------
# Execution & Results Rendering
# ---------------------------------------------------------------------------
def ensure_yt_client() -> YouTubeMusicClient:
    if not Path(auth_file_path).exists():
        st.error(
            f"Auth file '{auth_file_path}' not found. Follow Step 2 in the guide and save browser.json first."
        )
        st.stop()
    return YouTubeMusicClient(auth_file=auth_file_path)


def render_summaries(summaries) -> None:
    st.success("Migration complete!")
    st.download_button(
        "Download Full Match Report (CSV)",
        data=summaries_to_csv(summaries),
        file_name="ytmusic_migration_report.csv",
        mime="text/csv",
    )

    for s in summaries:
        st.markdown(f"### {s.spotify_name}")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Tracks", s.total_tracks)
        m2.metric("Exact Matches", s.exact_matches)
        m3.metric("Closest Fallbacks", s.closest_fallbacks)
        m4.metric("Not Found", s.not_found)

        if s.ytmusic_url:
            st.markdown(f"**YouTube Music Playlist:** [{s.ytmusic_url}]({s.ytmusic_url})")

        rows = [
            {
                "Spotify Track": m.spotify_track.title,
                "Spotify Artist": m.spotify_track.artists_str,
                "YouTube Match": m.matched_title,
                "YouTube Artist": m.matched_artists,
                "Match Type": m.match_type,
                "Confidence (%)": m.score,
            }
            for m in s.matches
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)


if start_files:
    if not uploaded_files:
        st.warning("Upload at least one .csv or .json file first.")
        st.stop()
    yt_client = ensure_yt_client()
    playlists: List[PlaylistData] = []
    for f in uploaded_files:
        content = f.read().decode("utf-8", errors="replace")
        if f.name.lower().endswith(".csv"):
            playlists.append(parse_exportify_csv(content, playlist_name=Path(f.name).stem))
        else:
            playlists.extend(parse_spotify_privacy_json(content))

    progress_bar = st.progress(0)
    status_text = st.empty()

    def on_progress(pl_name: str, idx: int, total: int, match: MatchResult) -> None:
        progress_bar.progress(int((idx / max(total, 1)) * 100))
        status_text.text(
            f"[{pl_name}] {idx}/{total}: {match.spotify_track.title} → "
            f"{match.matched_title or 'Not found'} ({match.match_type}, {match.score}%)"
        )

    summaries = migrate_playlist_data(
        yt_client=yt_client,
        playlists=playlists,
        privacy_status=privacy,
        dry_run=dry_run,
        progress_callback=on_progress,
    )
    render_summaries(summaries)

elif start_links:
    links = [line.strip() for line in playlist_text.splitlines() if line.strip()]
    if not links:
        st.warning("Paste at least one public Spotify playlist URL.")
        st.stop()
    yt_client = ensure_yt_client()
    progress_bar = st.progress(0)
    status_text = st.empty()

    def on_progress(pl_name: str, idx: int, total: int, match: MatchResult) -> None:
        progress_bar.progress(int((idx / max(total, 1)) * 100))
        status_text.text(
            f"[{pl_name}] {idx}/{total}: {match.spotify_track.title} → "
            f"{match.matched_title or 'Not found'} ({match.match_type}, {match.score}%)"
        )

    summaries = migrate_playlists(
        spotify_extractor=None,
        yt_client=yt_client,
        playlist_inputs=links,
        privacy_status=privacy,
        dry_run=dry_run,
        progress_callback=on_progress,
    )
    render_summaries(summaries)

elif start_api:
    if not client_id.strip() or not client_secret.strip():
        st.error("Enter your Spotify Client ID and Client Secret.")
        st.stop()
    yt_client = ensure_yt_client()
    extractor = SpotifyExtractor(
        client_id=client_id.strip(),
        client_secret=client_secret.strip(),
        use_oauth=import_all_mine,
    )
    links = [line.strip() for line in api_playlist_text.splitlines() if line.strip()]
    if import_all_mine:
        with st.spinner("Fetching all playlists from your Spotify account..."):
            links.extend(extractor.get_all_user_playlists())
    if not links:
        st.warning("Add at least one playlist URL or check 'Import ALL playlists'.")
        st.stop()
    progress_bar = st.progress(0)
    status_text = st.empty()

    def on_progress(pl_name: str, idx: int, total: int, match: MatchResult) -> None:
        progress_bar.progress(int((idx / max(total, 1)) * 100))
        status_text.text(
            f"[{pl_name}] {idx}/{total}: {match.spotify_track.title} → "
            f"{match.matched_title or 'Not found'} ({match.match_type}, {match.score}%)"
        )

    summaries = migrate_playlists(
        spotify_extractor=extractor,
        yt_client=yt_client,
        playlist_inputs=links,
        privacy_status=privacy,
        dry_run=dry_run,
        progress_callback=on_progress,
    )
    render_summaries(summaries)