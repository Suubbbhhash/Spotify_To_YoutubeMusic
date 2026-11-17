# Spotify → YouTube Music Playlist Migrator

[](https://www.python.org/)
[](https://streamlit.io/)
[](https://ytmusicapi.readthedocs.io/en/stable/)
[](https://rapidfuzz.github.io/RapidFuzz/)

Import **all songs (no track cap)** from Spotify playlists into YouTube Music—designed to work on **free Spotify accounts** without needing Spotify Premium or a Developer API key, with multi-tier fuzzy matching that automatically picks the closest available audio, lyric video, or upload when an exact studio track is missing.

---

## Features

- **Works on Free Spotify Accounts**: Since Spotify's [February 2026 Web API update](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide) requires Spotify Premium for Developer Mode apps, this tool supports:
  - **Unlimited CSV / JSON Imports**: Upload `.csv` exports from [Exportify](https://exportify.net/) or `Playlist1.json` from your [Spotify Privacy Data Download](https://www.spotify.com/account/privacy/) with zero track limits.
  - **No-API Public Links**: Paste public `open.spotify.com/playlist/...` URLs directly (up to 100 tracks per link via Spotify's embed endpoint).
  - **Optional Web API Mode**: Full paginated extraction via [Spotipy](https://spotipy.readthedocs.io/) for Premium accounts.
- **One-Click `Copy as cURL (bash)` Auth**: Paste `Copy as cURL (bash)` directly from Chrome/Edge DevTools into the Streamlit sidebar—the app automatically extracts headers/cookies and strips `content-encoding: gzip` so playlist creation works cleanly.
- **4-Tier Closest-Match Fallback**: Uses [RapidFuzz](https://rapidfuzz.github.io/RapidFuzz/) (normalized title, artist, and duration scoring) across `songs` → `videos` → relaxed `title + artist` → `title` only.
- **Resilient Batched Uploads & Reports**: Inserts tracks in deduplicated batches of 50 with retry fallback, saves JSON logs to `reports/`, and offers one-click CSV report downloads in the UI.

---

## Project Structure

```text
spotify_to_ytmusic/
├── app.py                  # Flagship Streamlit UI + built-in setup guide & cURL parser
├── cli.py                  # Rich terminal CLI
├── requirements.txt        # Python dependencies
├── .env.example            # Optional Spotify Web API environment template
├── src/
│   ├── __init__.py
│   ├── spotify_client.py   # Exportify CSV, Privacy JSON, public embed, & Spotipy extractors
│   ├── matcher.py          # RapidFuzz normalization & 4-tier closest-match scoring
│   ├── ytmusic_client.py   # ytmusicapi wrapper with batched insertion & retries
│   └── importer.py         # End-to-end migration orchestrator & JSON reporting
└── tests/
    └── test_matcher.py     # Offline unit tests for URL parsing & fuzzy matching
```

---

## Quick Start

### 1. Install Dependencies
```bash
git clone <your-repo-url>
cd spotify_to_ytmusic
pip install -r requirements.txt
```

### 2. Launch the Web UI
```bash
streamlit run app.py
```

### 3. Authenticate YouTube Music (`browser.json`)
1. Open [YouTube Music](https://music.youtube.com) in Chrome or Edge (logged in).
2. Press `Ctrl+Shift+I` → **Network** tab → filter by `/browse`.
3. Click **Library** so a `browse?...` **POST** request (`Status 200`) appears.
4. Right-click that request → **Copy** → **Copy as cURL (bash)**.
5. In the Streamlit sidebar, select **Paste cURL (bash) or Raw Headers**, paste it in, and click **Auto-Parse & Save browser.json** (then click **Test YouTube Music Connection** to verify).

> **Alternative (OAuth):** You can also run `ytmusicapi oauth` with a Google Cloud `TVs and Limited Input devices` Client ID (see the [ytmusicapi OAuth guide](https://ytmusicapi.readthedocs.io/en/1.12.2/setup/oauth.html)) and set **Auth JSON Path** to `oauth.json`.

### 4. Export & Import Your Spotify Playlists
- **Recommended (Free & Unlimited):** Export playlists as `.csv` from [Exportify](https://exportify.net/) or download `Playlist1.json` from [Spotify Privacy Settings](https://www.spotify.com/account/privacy/), upload them in the first tab, and click **Import Uploaded Playlists**.
- **CLI Usage:**
  ```bash
  # Import from Exportify CSV or Playlist1.json
  python cli.py my_playlist.csv --privacy PRIVATE

  # Import a public Spotify playlist link (no API key)
  python cli.py "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M" --privacy PRIVATE

  # Preview matches without creating playlists
  python cli.py my_playlist.csv --dry-run
  ```

---

## Troubleshooting

| Issue | Cause | Fix |
| :--- | :--- | :--- |
| `YTMusicUserError: missing cookie, x-goog-authuser` | Copied Response Headers or an `OPTIONS`/`GET` request instead of `/browse` `POST` | Filter by `/browse`, verify **POST + Status 200**, and use **Copy as cURL (bash)** |
| `JSONDecodeError: Expecting value` on `create_playlist` | `content-encoding: gzip` header saved inside `browser.json` | Re-save via the updated **Auto-Parse & Save** button in `app.py` (automatically strips `content-encoding`) |
| `client_id: Invalid` / Spotify Web API blocked | Random ID or free Spotify account under [Feb 2026 API rules](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide) | Use the **Free & Unlimited (CSV / Playlist1.json)** tab instead of Web API |
