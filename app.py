import os
from flask import Flask, render_template, request, jsonify
from serpapi.google_search import GoogleSearch
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/search", methods=["POST"])
def search_images():
    data = request.get_json()
    query = (data.get("query") or "").strip() if data else ""
    if not query:
        return jsonify({"error": "No query provided"}), 400

    try:
        search = GoogleSearch({
            "q": query,
            "tbm": "isch",
            "num": 5,
            "api_key": os.environ["SERP_KEY"],
        })
        results = search.get_dict()
        images = results.get("images_results", [])[:5]
        return jsonify({
            "images": [
                {"thumbnail": img.get("thumbnail"), "title": img.get("title")}
                for img in images
            ]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
