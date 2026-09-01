"""
generate_post.py
-----------------
Run this AFTER you've picked an item from today's list.

USAGE:
    python generate_post.py --url "https://makerworld.com/en/models/2926008-..." --category everyday
    python generate_post.py --url "https://cults3d.com/en/3d-model/..." --category novelty --photo-url "https://.../my-preferred-photo.jpg"

WHAT IT DOES:
  1. Tries to find real "made by the community" photos on the model's page
     (the actual printed object, not the designer's 3D render). This part is
     a best-effort heuristic -- these sites don't offer a clean public API
     for it, so it may occasionally miss. If it can't find one, it tells you
     plainly and you can pass --photo-url with a photo you picked by hand
     from the listing's "Makes" / "Made by" section.
  2. Downloads the candidate photo(s) into output/<slug>/photos/
  3. Generates 3 ready-to-edit Facebook captions into output/<slug>/captions.txt

Nothing is posted anywhere -- you review the folder and post manually.
"""

import os
import re
import argparse
import random
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# Words that tend to show up on designer renders/covers -- used to DEPRIORITIZE
# an image as "probably not a real photo of the finished print".
RENDER_HINTS = ["render", "cover", "thumbnail", "logo", "icon", "banner"]
# Words that tend to show up near real customer photos.
MAKE_HINTS = ["make", "made", "print", "community", "customer", "gallery", "photo"]


def slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9\s-]", "", text).strip().lower()
    return re.sub(r"[\s-]+", "-", text)[:60]


def find_make_gallery_urls(model_url: str) -> list:
    """
    Best-effort: look for a 'Makes' / 'Community made' section on the model
    page (or a linked sub-page) and pull image URLs from it.
    Falls back to an empty list if nothing confident is found.
    """
    candidates = []
    try:
        resp = requests.get(model_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[!] Could not fetch model page: {e}")
        return candidates

    soup = BeautifulSoup(resp.text, "html.parser")
    domain = urlparse(model_url).netloc

    # 1) Look for a link to a dedicated "makes" sub-page (common pattern on
    #    MakerWorld / Cults3D / Printables / Thingiverse).
    make_link = None
    for a in soup.find_all("a", href=True):
        link_text = a.get_text(strip=True).lower()
        if any(h in link_text for h in ["make", "community", "gallery"]) and "http" not in a["href"][:4]:
            make_link = urljoin(model_url, a["href"])
            break

    pages_to_scan = [resp.text]
    if make_link and make_link != model_url:
        try:
            r2 = requests.get(make_link, headers=HEADERS, timeout=20)
            if r2.ok:
                pages_to_scan.append(r2.text)
        except requests.RequestException:
            pass

    for page_html in pages_to_scan:
        s = BeautifulSoup(page_html, "html.parser")
        for img in s.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            if not src:
                continue
            alt = (img.get("alt") or "").lower()
            classes = " ".join(img.get("class", [])).lower()
            surrounding = alt + " " + classes

            if any(h in surrounding for h in RENDER_HINTS):
                continue  # skip obvious render/cover images
            if any(h in surrounding for h in MAKE_HINTS) or make_link:
                full_src = urljoin(model_url, src)
                if full_src not in candidates:
                    candidates.append(full_src)

    return candidates[:6]


def download_images(urls: list, out_dir: str) -> list:
    os.makedirs(out_dir, exist_ok=True)
    saved = []
    for i, url in enumerate(urls, 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            ext = os.path.splitext(urlparse(url).path)[1] or ".jpg"
            path = os.path.join(out_dir, f"photo_{i}{ext}")
            with open(path, "wb") as f:
                f.write(r.content)
            saved.append(path)
            print(f"Saved {path}")
        except requests.RequestException as e:
            print(f"[!] Failed to download {url}: {e}")
    return saved


# ---- Caption generation (template-based, free, no API needed) ----

HOOKS_EVERYDAY = [
    "Okay, this one's actually useful. \U0001F440",
    "Found the fix for a problem you didn't know had a fix.",
    "Printed this and now I want to print one for every room.",
    "This is the kind of upgrade that quietly makes your day easier.",
    "Small print, big difference in how tidy this space feels.",
]

HOOKS_NOVELTY = [
    "Okay this one is just too fun not to share. \U0001F525",
    "Printed purely because it made me smile -- worth it.",
    "Not everything I print needs a job to justify existing.",
    "This is peak 'why did I make this' energy and I regret nothing.",
    "Sometimes the printer's just for fun. Exhibit A:",
]

WHY_LINES = {
    "everyday": [
        "No more clutter, no more losing it, no more asking 'where did that go.'",
        "Simple design, does exactly what it says on the tin.",
        "One of those prints that earns its spot on the desk/shelf immediately.",
        "Printed in a couple hours, useful for a lot longer than that.",
    ],
    "novelty": [
        "Zero practical use, 100% worth the filament.",
        "Great little conversation piece if anyone asks what's on my desk.",
        "Print-in-place, no supports, no fuss -- just satisfying to watch come together.",
        "This is going straight on the shelf of prints I'm way too proud of.",
    ],
}

CTA_LINES = [
    "File's free -- link in the comments if you want to print your own.",
    "Grabbed this one for free, happy to share the link if you want it.",
    "Free download, easy print -- DM me if you want the source.",
    "Totally free file -- comment below and I'll send the link over.",
]

HASHTAGS = {
    "everyday": "#3DPrinting #3DPrintedGifts #HomeOrganization #MakerLife #PrintedNotBought",
    "novelty": "#3DPrinting #3DPrintedArt #MakerLife #PrintInPlace #GeekGifts",
}


def generate_captions(title: str, category: str, source_url: str, n: int = 3) -> list:
    hooks = HOOKS_EVERYDAY if category == "everyday" else HOOKS_NOVELTY
    why_pool = WHY_LINES.get(category, WHY_LINES["everyday"])
    hashtags = HASHTAGS.get(category, HASHTAGS["everyday"])

    captions = []
    used_hooks = random.sample(hooks, min(n, len(hooks)))
    for hook in used_hooks:
        why = random.choice(why_pool)
        cta = random.choice(CTA_LINES)
        caption = f"{hook}\n\n\u201c{title}\u201d -- {why}\n\n{cta}\n\n{hashtags}"
        captions.append(caption)
    return captions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="URL of the model page you chose")
    parser.add_argument("--title", default=None, help="Override title (else pulled from page <title>)")
    parser.add_argument("--category", choices=["everyday", "novelty"], default="everyday")
    parser.add_argument("--photo-url", default=None,
                         help="Manually supply a finished-print photo URL if auto-detection misses")
    args = parser.parse_args()

    title = args.title
    if not title:
        try:
            r = requests.get(args.url, headers=HEADERS, timeout=20)
            soup = BeautifulSoup(r.text, "html.parser")
            title = soup.title.get_text(strip=True) if soup.title else args.url
        except requests.RequestException:
            title = args.url

    slug = slugify(title)
    out_dir = os.path.join("output", slug)
    photos_dir = os.path.join(out_dir, "photos")

    photo_urls = [args.photo_url] if args.photo_url else find_make_gallery_urls(args.url)

    if not photo_urls:
        print("\n[!] Could not confidently find a real 'finished print' photo automatically.")
        print("    Open the listing, find the 'Makes' / 'Made by' section, right-click a")
        print("    real photo -> Copy Image Address, then re-run with:")
        print(f"      python generate_post.py --url \"{args.url}\" --category {args.category} --photo-url \"<paste here>\"\n")
    else:
        download_images(photo_urls, photos_dir)

    captions = generate_captions(title, args.category, args.url)
    os.makedirs(out_dir, exist_ok=True)
    captions_path = os.path.join(out_dir, "captions.txt")
    with open(captions_path, "w", encoding="utf-8") as f:
        for i, c in enumerate(captions, 1):
            f.write(f"--- Option {i} ---\n{c}\n\n")

    print(f"\nCaptions written to {captions_path}")
    print(f"Photos (if found) in {photos_dir}")
    print("Review both, pick your favorite caption + photo, and post manually.")


if __name__ == "__main__":
    main()
