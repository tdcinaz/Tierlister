import os
import hashlib
import json
import logging
import sqlite3
import base64
import subprocess
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import requests as http_requests
from flask import Flask, render_template, request, jsonify, Response
from serpapi.google_search import GoogleSearch
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

MAX_THUMBNAIL_SIZE = 300  # px – longest side after resize

# ── SQLite image cache ──────────────────────────────────────────────

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "image_cache.db")


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS image_cache "
        "(key TEXT PRIMARY KEY, images TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS query_canonicals "
        "(normalized_query TEXT PRIMARY KEY, canonical TEXT)"
    )
    conn.commit()
    return conn


def _cache_key(query: str) -> str:
    sanitized = query.lower().replace(" ", "")
    return hashlib.sha256(sanitized.encode()).hexdigest()


def _cache_get(key: str):
    conn = _get_db()
    row = conn.execute(
        "SELECT images FROM image_cache WHERE key = ?", (key,)
    ).fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    return None


def _cache_put(key: str, images: list):
    conn = _get_db()
    conn.execute(
        "INSERT OR REPLACE INTO image_cache (key, images) VALUES (?, ?)",
        (key, json.dumps(images)),
    )
    conn.commit()
    conn.close()


# ── Query canonicalization (Gemini) ─────────────────────────────────

def _gemini_canonicalize(query: str) -> str:
    """Ask Gemini for the canonical name of the search subject."""
    api_key = os.environ.get("GEMINI_KEY")
    if not api_key:
        return query

    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        "models/gemini-2.5-flash:generateContent"
    )
    payload = {
        "contents": [{
            "parts": [{
                "text": (
                    "What is the single most common canonical name for the "
                    "following search subject? Reply with ONLY the name, "
                    "nothing else. If the input is already a common/generic "
                    "term (like 'pizza' or 'sunset'), return it unchanged.\n\n"
                    f"Input: {query}"
                )
            }]
        }],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 2048,
        },
    }
    try:
        resp = http_requests.post(
            url, params={"key": api_key}, json=payload, timeout=30
        )
        resp.raise_for_status()
        parts = (
            resp.json()
            .get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [])
        )
        text = ""
        for part in parts:
            if not part.get("thought"):
                text = part.get("text", "").strip()
        return text if text else query
    except Exception as e:
        logger.error(f"[canonicalize] Gemini error: {e}")
        return query


def _resolve_canonical(query: str) -> tuple[str, bool]:
    """Return (canonical_query, was_remapped) using cached mapping or Gemini."""
    normalized = query.lower().replace(" ", "")
    conn = _get_db()
    row = conn.execute(
        "SELECT canonical FROM query_canonicals WHERE normalized_query = ?",
        (normalized,),
    ).fetchone()
    conn.close()

    if row:
        canonical = row[0]
        remapped = _cache_key(canonical) != _cache_key(query)
        return canonical, remapped

    import time as _time
    t0 = _time.perf_counter()
    canonical = _gemini_canonicalize(query)
    t_gemini = _time.perf_counter() - t0
    print(f"[canonicalize] '{query}' -> '{canonical}' ({t_gemini:.2f}s)")

    conn = _get_db()
    conn.execute(
        "INSERT OR REPLACE INTO query_canonicals (normalized_query, canonical) "
        "VALUES (?, ?)",
        (normalized, canonical),
    )
    conn.commit()
    conn.close()

    remapped = _cache_key(canonical) != _cache_key(query)
    return canonical, remapped


# ── Image processing helpers ────────────────────────────────────────

def _download_and_process(url: str) -> str | None:
    """Download a thumbnail, center-crop to square, resize, return as data URI."""
    try:
        resp = http_requests.get(url, timeout=5)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content))
        img = img.convert("RGB")

        # Crop to square: top-crop for tall images, center-crop otherwise
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top = 0 if h > w * 1.2 else (h - side) // 2
        img = img.crop((left, top, left + side, top + side))

        # Resize to max dimension
        if side > MAX_THUMBNAIL_SIZE:
            img = img.resize(
                (MAX_THUMBNAIL_SIZE, MAX_THUMBNAIL_SIZE), Image.LANCZOS
            )

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        return None


# ── Batch list generation (Gemini) ──────────────────────────────────

def _generate_item_list(category: str) -> list[str]:
    """Ask Gemini to generate a list of items for a category."""
    api_key = os.environ.get("GEMINI_KEY")
    if not api_key:
        return []

    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        "models/gemini-2.5-flash:generateContent"
    )
    payload = {
        "contents": [{
            "parts": [{
                "text": (
                    "List all of the most well-known items in the following "
                    "category. Return ONLY a JSON array of strings, nothing "
                    "else. Each string should be the commonly recognized name "
                    "of the item. Return between 8 and 25 items depending on "
                    "how many well-known items exist. Example format: "
                    '[\"Item 1\", \"Item 2\", \"Item 3\"]\n\n'
                    f"Category: {category}"
                )
            }]
        }],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 8192,
        },
    }
    try:
        resp = http_requests.post(
            url, params={"key": api_key}, json=payload, timeout=30
        )
        resp.raise_for_status()
        parts = (
            resp.json()
            .get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [])
        )
        text = ""
        for part in parts:
            if not part.get("thought"):
                text = part.get("text", "").strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        items = json.loads(text)
        if isinstance(items, list):
            return [str(i).strip() for i in items if str(i).strip()]
        return []
    except Exception as e:
        logger.error(f"[batch-generate] Gemini error: {e}")
        return []


# ── Routes ──────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/search", methods=["POST"])
def search_images():
    data = request.get_json()
    query = (data.get("query") or "").strip() if data else ""
    if not query:
        return jsonify({"error": "No query provided"}), 400

    canonical, remapped = _resolve_canonical(query)
    key = _cache_key(canonical)

    # Check cache first
    cached = _cache_get(key)
    if cached is not None:
        resp = {"images": cached, "cached": True}
        if remapped:
            resp["canonical"] = canonical
        return jsonify(resp)

    # Cache miss – call SerpApi
    try:
        import time as _time

        t0 = _time.perf_counter()
        search = GoogleSearch({
            "q": query,
            "tbm": "isch",
            "num": 5,
            "api_key": os.environ["SERP_KEY"],
        })
        results = search.get_dict()
        t_api = _time.perf_counter() - t0

        raw_images = results.get("images_results", [])[:5]

        # Download & process thumbnails in parallel
        thumb_urls = [img.get("thumbnail") for img in raw_images]
        t1 = _time.perf_counter()
        with ThreadPoolExecutor(max_workers=5) as pool:
            data_uris = list(pool.map(
                lambda u: _download_and_process(u) if u else None,
                thumb_urls,
            ))
        t_download = _time.perf_counter() - t1

        print(f"[profile] SerpApi: {t_api:.2f}s | Downloads: {t_download:.2f}s | Total: {t_api + t_download:.2f}s")

        images = []
        for img, data_uri in zip(raw_images, data_uris):
            images.append({
                "thumbnail": data_uri or img.get("thumbnail"),
                "title": img.get("title"),
                "width": img.get("original_width"),
                "height": img.get("original_height"),
            })

        _cache_put(key, images)
        resp = {"images": images, "cached": False}
        if remapped:
            resp["canonical"] = canonical
        return jsonify(resp)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/batch-generate", methods=["POST"])
def batch_generate():
    data = request.get_json()
    category = (data.get("category") or "").strip() if data else ""
    if not category:
        return jsonify({"error": "No category provided"}), 400

    items = _generate_item_list(category)
    if not items:
        return jsonify({"error": "Could not generate items for that category."}), 500

    return jsonify({"items": items[:10]})


@app.route("/api/batch-search", methods=["POST"])
def batch_search():
    """Process a list of items through canonicalization + cache check.

    Returns cached image results immediately and flags cache misses
    so the frontend can query them individually via /api/search.
    """
    data = request.get_json()
    items = data.get("items", []) if data else []
    if not items or not isinstance(items, list):
        return jsonify({"error": "No items provided"}), 400

    results = []
    for item_name in items:
        item_name = str(item_name).strip()
        if not item_name:
            continue
        canonical, remapped = _resolve_canonical(item_name)
        key = _cache_key(canonical)
        cached = _cache_get(key)
        entry = {
            "item": item_name,
            "canonical": canonical if remapped else None,
        }
        if cached is not None:
            entry["images"] = cached
            entry["cached"] = True
        else:
            entry["images"] = None
            entry["cached"] = False
        results.append(entry)

    return jsonify({"results": results})


@app.route("/logs")
def view_logs():
    lines = request.args.get("n", "200")
    try:
        result = subprocess.run(
            ["journalctl", "-u", "tierlister", "--no-pager", "-n", lines],
            capture_output=True, text=True, timeout=5,
        )
        return Response(result.stdout or result.stderr, mimetype="text/plain")
    except Exception as e:
        return Response(f"Error reading logs: {e}", mimetype="text/plain"), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5001)
