"""Command-line interface for importing Spotify playlists to YouTube Music."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress
from rich.table import Table

from src.importer import migrate_playlist_data, migrate_playlists
from src.matcher import MatchResult
from src.spotify_client import (
    PlaylistData,
    SpotifyExtractor,
    parse_exportify_csv,
    parse_spotify_privacy_json,
)
from src.ytmusic_client import YouTubeMusicClient, create_browser_auth_from_raw_headers


console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import Spotify playlists to YouTube Music (works on free Spotify accounts)."
    )
    parser.add_argument(
        "playlists",
        nargs="*",
        help="Spotify playlist URLs, or paths to Exportify .csv / Spotify Playlist1.json files.",
    )
    parser.add_argument(
        "--use-spotify-api",
        action="store_true",
        help="Use Spotify Web API (requires Spotify Premium Developer app).",
    )
    parser.add_argument(
        "--all-my-playlists",
        action="store_true",
        help="Import all playlists via Spotify Web API (requires --use-spotify-api).",
    )
    parser.add_argument(
        "--privacy",
        choices=["PRIVATE", "PUBLIC", "UNLISTED"],
        default="PRIVATE",
        help="Privacy status for created YouTube Music playlists (default: PRIVATE).",
    )
    parser.add_argument(
        "--auth-file",
        default=os.getenv("YTMUSIC_AUTH_FILE", "browser.json"),
        help="Path to ytmusicapi browser.json or oauth.json.",
    )
    parser.add_argument(
        "--setup-browser-auth",
        action="store_true",
        help="Paste raw YouTube Music browser headers to generate browser.json.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Match songs without creating playlists on YouTube Music.",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    if args.setup_browser_auth:
        console.print(
            "[bold cyan]Paste your raw YouTube Music request headers below, then press Ctrl+D (Linux/Mac) or Ctrl+Z + Enter (Windows):[/bold cyan]"
        )
        lines: List[str] = []
        while True:
            try:
                lines.append(input())
            except EOFError:
                break
        raw = "\n".join(lines).strip()
        out_path = create_browser_auth_from_raw_headers(raw, args.auth_file)
        console.print(f"[green]Saved YouTube Music auth to {out_path}[/green]")
        if not args.playlists and not args.all_my_playlists:
            return

    if not args.playlists and not args.all_my_playlists:
        console.print(
            "[yellow]Provide Exportify .csv / Playlist1.json file paths or public playlist URLs.[/yellow]"
        )
        raise SystemExit(1)

    yt_client = YouTubeMusicClient(auth_file=args.auth_file)

    file_inputs = [p for p in args.playlists if Path(p).is_file()]
    link_inputs = [p for p in args.playlists if not Path(p).is_file()]

    with Progress() as progress:
        tasks = {}

        def on_track(pl_name: str, idx: int, total: int, match: MatchResult) -> None:
            if pl_name not in tasks:
                tasks[pl_name] = progress.add_task(f"[cyan]{pl_name}", total=total)
            progress.update(tasks[pl_name], completed=idx)

        summaries = []
        if file_inputs:
            parsed_playlists: List[PlaylistData] = []
            for fp in file_inputs:
                path_obj = Path(fp)
                text = path_obj.read_text(encoding="utf-8", errors="replace")
                if path_obj.suffix.lower() == ".csv":
                    parsed_playlists.append(parse_exportify_csv(text, playlist_name=path_obj.stem))
                else:
                    parsed_playlists.extend(parse_spotify_privacy_json(text))
            summaries.extend(
                migrate_playlist_data(
                    yt_client=yt_client,
                    playlists=parsed_playlists,
                    privacy_status=args.privacy,
                    dry_run=args.dry_run,
                    progress_callback=on_track,
                )
            )

        if link_inputs or args.all_my_playlists:
            extractor = None
            if args.use_spotify_api or args.all_my_playlists:
                client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
                client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
                redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
                if not client_id or not client_secret:
                    console.print("[red]Missing SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET.[/red]")
                    raise SystemExit(1)
                extractor = SpotifyExtractor(
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=redirect_uri,
                    use_oauth=args.all_my_playlists,
                )
                if args.all_my_playlists:
                    link_inputs.extend(extractor.get_all_user_playlists())

            summaries.extend(
                migrate_playlists(
                    spotify_extractor=extractor,
                    yt_client=yt_client,
                    playlist_inputs=link_inputs,
                    privacy_status=args.privacy,
                    dry_run=args.dry_run,
                    progress_callback=on_track,
                )
            )

    table = Table(title="Migration Summary")
    table.add_column("Playlist", style="bold")
    table.add_column("Total", justify="right")
    table.add_column("Exact", justify="right", style="green")
    table.add_column("Closest Fallback", justify="right", style="yellow")
    table.add_column("Not Found", justify="right", style="red")
    table.add_column("YouTube Music URL")

    for s in summaries:
        table.add_row(
            s.spotify_name,
            str(s.total_tracks),
            str(s.exact_matches),
            str(s.closest_fallbacks),
            str(s.not_found),
            s.ytmusic_url or "(dry-run)",
        )

    console.print(table)
    console.print(f"[dim]Detailed JSON report saved under {Path('reports').resolve()}[/dim]")


if __name__ == "__main__":
    main()
