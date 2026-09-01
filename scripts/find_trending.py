"""
find_trending.py
-----------------
Runs once a day (via GitHub Actions). Scrapes trending/free pages on
MakerWorld and Cults3D, splits results into "everyday" and "novelty"
buckets, writes:
  - data/latest_picks.json   (machine-readable, used by generate_post.py)
  - docs/index.html          (human-readable page, published via GitHub Pages)
And optionally pushes a Telegram message with the day's picks.

This script only ever reads public pages. No login, no API key required
for the scraping part. Telegram push is optional (set TELEGRAM_BOT_TOKEN
and TELEGRAM_CHAT_ID as GitHub Secrets to enable it).
"""

import os
import re
import json
import time
import requests
from datetime import datetime, timezone
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# Each source: where to look, how to recognize a model link, and how to
# turn a relative link into a full URL.
SOURCES = [
    {
        "name": "MakerWorld",
        "url": "https://makerworld.com/en/collections/9539194-trending",
        "link_pattern": re.compile(r"^/en/models/\d+-"),
        "base": "https://makerworld.com",
    },
    {
        "name": "Cults3D",
        "url": "https://cults3d.com/en/tags/free",
        "link_pattern": re.compile(r"^/en/3d-model/"),
        "base": "https://cults3d.com",
    },
]

EVERYDAY_KEYWORDS = [
    "holder", "stand", "organizer", "organiser", "storage", "hook", "bracket",
    "vase", "pot", "shelf", "bookend", "case", "box", "mount", "hanger",
    "rack", "tray", "lamp", "light", "table", "desk", "kitchen", "bathroom",
    "clip", "coaster", "planter", "cable", "charger", "remote", "soap",
]

NOVELTY_KEYWORDS = [
    "figure", "statue", "fidget", "cosplay", "sword", "katana", "gundam",
    "toy", "dragon", "miniature", "bust", "game", "puzzle", "cute",
    "keychain", "gadget", "wind-up", "articulated", "fan art", "cat",
    "dinosaur", "skull", "fantasy", "anime", "hopper", "spinner",
]

MAX_PER_CATEGORY = 8  # aim for 5-10 per your request


def guess_category(title: str) -> str:
    t = title.lower()
    everyday_hits = sum(1 for k in EVERYDAY_KEYWORDS if k in t)
    novelty_hits = sum(1 for k in NOVELTY_KEYWORDS if k in t)
    if everyday_hits > novelty_hits:
        return "everyday"
    if novelty_hits > everyday_hits:
        return "novelty"
    return "everyday" if everyday_hits else "uncategorized"


def scrape_source(source: dict, limit: int = 30) -> list:
    items = []
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[!] Could not fetch {source['name']}: {e}")
        return items

    soup = BeautifulSoup(resp.text, "html.parser")
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not source["link_pattern"].match(href):
            continue
        full_url = href if href.startswith("http") else source["base"] + href
        if full_url in seen:
            continue

        title = a.get_text(strip=True)
        img_tag = a.find("img")
        if not title and img_tag:
            title = img_tag.get("alt", "").strip()
        if not title or len(title) < 3:
            continue

        thumbnail = ""
        if img_tag:
            thumbnail = img_tag.get("src") or img_tag.get("data-src") or ""

        seen.add(full_url)
        items.append({
            "source": source["name"],
            "title": title,
            "url": full_url,
            "thumbnail": thumbnail,
            "category": guess_category(title),
        })

        if len(items) >= limit:
            break

    return items


def build_daily_picks() -> dict:
    all_items = []
    for source in SOURCES:
        print(f"Scraping {source['name']} ...")
        found = scrape_source(source)
        print(f"  -> {len(found)} candidate items")
        all_items.extend(found)
        time.sleep(2)

    everyday = [i for i in all_items if i["category"] == "everyday"][:MAX_PER_CATEGORY]
    novelty = [i for i in all_items if i["category"] == "novelty"][:MAX_PER_CATEGORY]

    # top up to at least 5 each from "uncategorized" if a bucket is thin
    leftovers = [i for i in all_items if i["category"] == "uncategorized"]
    while len(everyday) < 5 and leftovers:
        everyday.append(leftovers.pop(0))
    while len(novelty) < 5 and leftovers:
        novelty.append(leftovers.pop(0))

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "everyday": everyday,
        "novelty": novelty,
    }


def write_json(picks: dict, path: str = "data/latest_picks.json"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(picks, f, indent=2, ensure_ascii=False)
    print(f"Wrote {path}")


def write_html(picks: dict, path: str = "docs/index.html"):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    def card(item, idx):
        thumb = item["thumbnail"] or "https://via.placeholder.com/300x200?text=No+Image"
        return f"""
        <div class="card">
          <img src="{thumb}" alt="{item['title']}" loading="lazy">
          <div class="card-body">
            <span class="source">{item['source']}</span>
            <h3>{idx}. {item['title']}</h3>
            <a href="{item['url']}" target="_blank" rel="noopener">Open listing &rarr;</a>
          </div>
        </div>"""

    everyday_cards = "".join(card(i, n + 1) for n, i in enumerate(picks["everyday"]))
    novelty_cards = "".join(card(i, n + 1) for n, i in enumerate(picks["novelty"]))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Today's Trending 3D Prints</title>
<style>
  body {{ font-family: -apple-system, Arial, sans-serif; background:#f7f7f8; margin:0; padding:24px; color:#1a1a1a; }}
  h1 {{ font-size: 22px; }}
  h2 {{ font-size: 18px; margin-top: 32px; border-bottom: 2px solid #333; padding-bottom: 6px; }}
  .meta {{ color:#666; font-size: 13px; margin-bottom: 20px; }}
  .grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap:16px; }}
  .card {{ background:#fff; border-radius:10px; overflow:hidden; box-shadow:0 1px 4px rgba(0,0,0,.1); }}
  .card img {{ width:100%; height:150px; object-fit:cover; background:#eee; }}
  .card-body {{ padding:10px 12px; }}
  .source {{ font-size:11px; text-transform:uppercase; color:#888; letter-spacing:.5px; }}
  .card-body h3 {{ font-size:14px; margin:4px 0 8px; }}
  .card-body a {{ font-size:13px; color:#0a58ff; text-decoration:none; }}
</style>
</head>
<body>
  <h1>Today's Trending 3D Prints</h1>
  <div class="meta">Generated {picks['generated_at_utc']} &middot; all free downloads</div>

  <h2>Everyday / Household</h2>
  <div class="grid">{everyday_cards}</div>

  <h2>Fun / Not Everyday</h2>
  <div class="grid">{novelty_cards}</div>
</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {path}")


def send_telegram(picks: dict):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    pages_url = os.environ.get("PAGES_URL", "")  # e.g. https://yourname.github.io/3d-trend-poster/

    if not token or not chat_id:
        print("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing) -- skipping push.")
        return

    lines = ["*Today's Trending 3D Prints* \U0001F5A8\n", "*Everyday:*"]
    for i, item in enumerate(picks["everyday"], 1):
        lines.append(f"{i}. {item['title']} ({item['source']}) - {item['url']}")
    lines.append("\n*Fun / Novelty:*")
    for i, item in enumerate(picks["novelty"], 1):
        lines.append(f"{i}. {item['title']} ({item['source']}) - {item['url']}")
    if pages_url:
        lines.append(f"\nBrowse with pictures: {pages_url}")

    text = "\n".join(lines)
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown",
                  "disable_web_page_preview": True},
            timeout=15,
        )
        print("Telegram message sent.")
    except requests.RequestException as e:
        print(f"[!] Telegram send failed: {e}")


def main():
    picks = build_daily_picks()
    write_json(picks)
    write_html(picks)
    send_telegram(picks)
    print(f"\nDone: {len(picks['everyday'])} everyday + {len(picks['novelty'])} novelty items.")


if __name__ == "__main__":
    main()
