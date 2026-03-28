# Tierlister

An interactive tier list creator with integrated image search — like TierMaker, but you search and add images on the fly.

## Features

- **Tier rows** — S, A, B, C, D, F with classic colored labels
- **Item tray** — uncategorized images live here before ranking
- **Drag & drop** — move image tiles between any tier and the tray (desktop and touch)
- **Image search overlay** — click "+" to search Google Images via SerpApi, pick from the top 5 results, and add to the tray
- **Responsive** — fluid layout that works on desktop and mobile

## Tech Stack

- **Backend:** Python / Flask
- **Frontend:** Vanilla HTML, CSS, JavaScript (single-page, no build step)
- **Image Search:** SerpApi (Google Images)
- **Package management:** uv

## Project Structure

```
app.py                  # Flask app — serves UI and /api/search endpoint
templates/
  index.html            # Full SPA: tier grid, tray, drag-and-drop, search overlay
.env                    # SERP_KEY (not committed)
.venv/                  # Python virtual environment (not committed)
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
uv pip install flask google-search-results python-dotenv
```

Create a `.env` file:

```
SERP_KEY="your-serpapi-key"
```

## Run

```bash
source .venv/bin/activate
python app.py
```

The app starts on **http://0.0.0.0:5001** (accessible from other devices on your LAN).

## API

### `POST /api/search`

Search Google Images.

**Request body:**
```json
{ "query": "golden retriever" }
```

**Response:**
```json
{
  "images": [
    { "thumbnail": "https://…", "title": "…" }
  ]
}
```

Returns the top 5 results.
