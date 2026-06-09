from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, asdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; Scrapper/1.0; +https://example.com)"


@dataclass
class ScrapedItem:
    source: str
    title: str = ""
    text: str = ""
    url: str = ""
    content_type: str = ""
    media: str = ""


class GenericPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.description = ""
        self.text_chunks: list[str] = []
        self.links: list[dict[str, str]] = []
        self.images: list[str] = []
        self.videos: list[str] = []
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs):
        attr_map = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            name = attr_map.get("name", "").lower()
            prop = attr_map.get("property", "").lower()
            content = attr_map.get("content", "").strip()
            if content and (name == "description" or prop in {"og:description", "twitter:description"}):
                self.description = content
        if tag == "a":
            href = attr_map.get("href", "").strip()
            text = attr_map.get("title", "").strip()
            if href:
                self.links.append({"href": href, "text": text})
        if tag == "img":
            src = attr_map.get("src", "").strip()
            if src:
                self.images.append(src)
        if tag in {"video", "source"}:
            src = attr_map.get("src", "").strip()
            if src:
                self.videos.append(src)

    def handle_endtag(self, tag: str):
        if tag in {"script", "style", "noscript"} and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str):
        if self._skip_depth > 0:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title += text
        else:
            self.text_chunks.append(text)


def fetch_url(url: str, timeout: int = 20) -> tuple[str, str]:
    request = Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get_content_type()
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace"), content_type


def safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return value.strip("._") or "download"


def download_file(url: str, output_dir: Path, fallback_name: str = "media") -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(url)
    name = Path(parsed.path).name or fallback_name
    target = output_dir / safe_filename(name)
    request = Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    with urlopen(request, timeout=30) as response, target.open("wb") as handle:
        handle.write(response.read())
    return str(target)


def scrape_reddit_subreddit(subreddit: str, limit: int = 25) -> list[ScrapedItem]:
    url = f"https://www.reddit.com/r/{subreddit}/.json?limit={limit}"
    html, _ = fetch_url(url)
    payload = json.loads(html)
    children = payload.get("data", {}).get("children", [])
    items: list[ScrapedItem] = []

    for child in children:
        data = child.get("data", {})
        title = data.get("title", "")
        text = data.get("selftext", "") or data.get("body", "")
        permalink = data.get("permalink", "")
        post_url = f"https://www.reddit.com{permalink}" if permalink else data.get("url", "")
        media = ""
        if data.get("is_video") and data.get("media", {}).get("reddit_video", {}).get("fallback_url"):
            media = data["media"]["reddit_video"]["fallback_url"]
        elif data.get("preview", {}).get("images"):
            media = data["preview"]["images"][0].get("source", {}).get("url", "").replace("&amp;", "&")
        elif data.get("url_overridden_by_dest"):
            media = data["url_overridden_by_dest"]

        content_type = "text"
        if media:
            if re.search(r"\.(png|jpe?g|gif|webp)(\?|$)", media, re.I):
                content_type = "image"
            elif re.search(r"\.(mp4|mov|webm)(\?|$)", media, re.I) or data.get("is_video"):
                content_type = "video"

        items.append(
            ScrapedItem(
                source=f"reddit:r/{subreddit}",
                title=title,
                text=text,
                url=post_url,
                content_type=content_type,
                media=media,
            )
        )
    return items


def scrape_generic_page(url: str) -> list[ScrapedItem]:
    html, content_type = fetch_url(url)
    if "json" in content_type:
        return [ScrapedItem(source=url, text=html, url=url, content_type="json")]

    parser = GenericPageParser()
    parser.feed(html)
    text = " ".join(parser.text_chunks)
    title = parser.title.strip()
    if not title:
        title = urlparse(url).netloc
    media_links = parser.images[:3] + parser.videos[:3]
    media = ", ".join(urljoin(url, link) for link in media_links)
    return [ScrapedItem(source=url, title=title, text=text[:5000], url=url, content_type="html", media=media)]


def scrape_url(target: str, limit: int = 25) -> list[ScrapedItem]:
    if target.startswith("http://") or target.startswith("https://"):
        parsed = urlparse(target)
        if "reddit.com" in parsed.netloc and "/r/" in parsed.path:
            match = re.search(r"/r/([^/]+)", parsed.path)
            if match:
                return scrape_reddit_subreddit(match.group(1), limit=limit)
        return scrape_generic_page(target)
    return scrape_reddit_subreddit(target, limit=limit)


def export_json(items: Iterable[ScrapedItem], output_path: Path) -> None:
    output_path.write_text(json.dumps([asdict(item) for item in items], indent=2, ensure_ascii=False), encoding="utf-8")


def export_csv(items: Iterable[ScrapedItem], output_path: Path) -> None:
    fieldnames = ["source", "title", "text", "url", "content_type", "media"]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in items:
            writer.writerow(asdict(item))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape Reddit posts or generic public web pages.")
    parser.add_argument("target", help="Subreddit name, subreddit URL, or public webpage URL")
    parser.add_argument("-n", "--limit", type=int, default=25, help="Maximum Reddit posts to fetch")
    parser.add_argument("-o", "--output", help="Write results to a file instead of stdout")
    parser.add_argument("--format", choices=("json", "csv"), default="json", help="Output format")
    parser.add_argument("--download-media", action="store_true", help="Download referenced media files")
    parser.add_argument("--media-dir", default="downloads", help="Directory for downloaded media")
    args = parser.parse_args(argv)

    try:
        items = scrape_url(args.target, limit=args.limit)
    except HTTPError as exc:
        print(f"HTTP error: {exc.code} {exc.reason}", file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"Network error: {exc.reason}", file=sys.stderr)
        return 1
    except json.JSONDecodeError:
        print("Could not parse response as JSON. The target may be blocking access.", file=sys.stderr)
        return 1

    if args.download_media:
        media_dir = Path(args.media_dir)
        for item in items:
            if item.media and item.content_type in {"image", "video"}:
                try:
                    item.media = download_file(item.media, media_dir, fallback_name=safe_filename(item.title))
                except Exception as exc:  # pragma: no cover - best-effort download
                    item.media = f"FAILED: {exc}"

    if args.output:
        output_path = Path(args.output)
        if args.format == "csv":
            export_csv(items, output_path)
        else:
            export_json(items, output_path)
    else:
        if args.format == "csv":
            writer = csv.DictWriter(sys.stdout, fieldnames=["source", "title", "text", "url", "content_type", "media"])
            writer.writeheader()
            for item in items:
                writer.writerow(asdict(item))
        else:
            print(json.dumps([asdict(item) for item in items], indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
