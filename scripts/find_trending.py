"""
find_trending.py
-----------------
Runs once a day (via GitHub Actions). Scrapes trending/free pages on
MakerWorld and Cults3D, splits results into "everyday" and "novelty"
buckets, writes:
  - data/latest_picks.json   (machine-readable, used by generate_post.py)
  - docs/index.html          (human-readable page, published via GitHub Pages)
And optionally pushes a Telegram message with the day's picks.

v2: uses cloudscraper instead of plain requests, because MakerWorld/Cults3D
sit behind bot-detection that blocks plain requests silently (you get a
200 response with an empty/near-empty page, no error). cloudscraper solves
the common cases of this. Debug logging is included so if a source still
comes back empty, the Actions log will show the response status/length
and a content snippet to help diagnose why.
"""

import os
import re
import json
import time
import cloudscraper
from datetime import datetime, timezone
from bs4 import BeautifulSoup

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

MAX_PER_CATEGORY = 8

# a fresh scraper "session" that mimics a real browser's TLS/JS-challenge handling
scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False}
)


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
        resp = scraper.get(source["url"], timeout=25)
    except Exception as e:
        print(f"[!] {source['name']}: request failed entirely: {e}")
        return items

    print(f"[DEBUG] {source['name']}: status={resp.status_code}, "
          f"content_length={len(resp.text)}")

    if resp.status_code != 200:
        print(f"[!] {source['name']}: non-200 response, first 300 chars:\n{resp.text[:300]}")
        return items

    soup = BeautifulSoup(resp.text, "html.parser")
    seen = set()

    all_links = soup.find_all("a", href=True)
    print(f"[DEBUG] {source['name']}: found {len(all_links)} total <a> tags on page")

    for a in all_links:
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

    if not items:
        print(f"[!] {source['name']}: 0 matching items. Page snippet for debugging:")
        print(resp.text[:500])

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

    empty_note = ""
    if not picks["everyday"] and not picks["novelty"]:
        empty_note = ("<p style='color:#b00'>No items found today -- check the "
                       "Actions log for the 'scrape' step for debug details.</p>")

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
  {empty_note}

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
    pages_url = os.environ.get("PAGES_URL", "")

    if not token or not chat_id:
        print("Telegram not configured -- skipping push.")
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
        scraper.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown",
                  "disable_web_page_preview": True},
            timeout=15,
        )
        print("Telegram message sent.")
    except Exception as e:
        print(f"[!] Telegram send failed: {e}")


def main():
    picks = build_daily_picks()
    write_json(picks)
    write_html(picks)
    send_telegram(picks)
    print(f"\nDone: {len(picks['everyday'])} everyday + {len(picks['novelty'])} novelty items.")


if __name__ == "__main__":
    main()
