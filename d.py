import asyncio
import json
import os
import re
import time
from urllib.parse import urlparse
import httpx
from playwright.async_api import async_playwright

class AutonomousWebScraper:
    def __init__(self, target_url: str, limit: int):
        self.target_url = target_url if target_url.startswith("http") else f"https://{target_url}"
        self.limit = limit
        self.results = []
        
        # Deduce a clean site name from the domain (e.g., "google" or "youtube")
        parsed_url = urlparse(self.target_url)
        self.domain_prefix = f"{parsed_url.scheme}://{parsed_url.netloc}"
        domain_parts = parsed_url.netloc.split('.')
        self.site_name = domain_parts[-2] if len(domain_parts) > 1 else "extracted_site"
        
        # Build local repository tree
        self.base_output_dir = f"autonomous_{self.site_name}"
        os.makedirs(self.base_output_dir, exist_ok=True)

    def _clean_folder_name(self, text: str) -> str:
        """Sanitizes strings to protect local OS file path allocation constraints."""
        clean = re.sub(r'[\\/*?:"<>|]', "", text)
        clean = clean.strip().replace(" ", "_")
        clean = clean.rstrip("._")
        return clean[:45]

    async def auto_discover_feed(self, page) -> list:
        """
        AI Semantic Selector Engine. Analytically maps the layout tree 
        of ANY website to isolate primary content blocks autonomously.
        """
        print("🤖 Analyzing page layout mechanics semantically...")
        discovered_nodes = []

        # Step A: Identify likely content containers using structural combinations
        # We target structural layouts common to Google cards, YouTube grids, and Yahoo news loops
        container_queries = [
            "article", "[role='article']", "div[class*='card']", 
            "div[class*='item']", "div[class*='post']", "li[class*='result']",
            "div[id*='result']", "ytd-video-renderer", "div.g" # Backup selectors for Google/YouTube grids
        ]
        
        containers = await page.locator(", ".join(container_queries)).all()
        
        # Fallback Strategy: If the page layout is obscure, group links that contain bold headers
        if len(containers) < 3:
            containers = await page.locator("div:has(h3), div:has(h2), div:has(h1)").all()

        for block in containers:
            try:
                # 1. Isolate Title text based on weight dominance
                title = ""
                title_el = block.locator("h1, h2, h3, h4, a[class*='title'], div[class*='title']").first
                if await title_el.count() > 0:
                    title = await title_el.inner_text()
                
                # If no clear header element, pick the link with the most characters
                if not title.strip():
                    links = await block.locator("a").all()
                    for link in links:
                        text = await link.inner_text()
                        if len(text) > len(title):
                            title = text

                # 2. Isolate corresponding Hyperlinks
                href = None
                link_el = block.locator("a[href]").first
                if await link_el.count() > 0:
                    href = await link_el.get_attribute("href")
                
                # Validation checks: Reject noise elements, ads, and unlinked containers
                if not title.strip() or not href or len(title.strip()) < 5 or href.startswith("javascript"):
                    continue
                    
                full_url = href if href.startswith("https") else f"{self.domain_prefix}{href}"
                
                # 3. Isolate visual Media Attachment elements
                media_url = None
                img_el = block.locator("img[src]").first
                if await img_el.count() > 0:
                    media_url = await img_el.get_attribute("src")

                discovered_nodes.append({
                    "title": title.strip(),
                    "url": full_url,
                    "media_url": media_url
                })
            except Exception:
                continue
                
        return discovered_nodes

    async def auto_extract_deep_text(self, context, item: dict):
        """Navigates inside individual targets to grab core paragraphs autonomously."""
        page = await context.new_page()
        try:
            await page.goto(item["url"], timeout=30000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            
            # Autonomous paragraph harvester: extracts text blocks that read like articles/comments
            text_blocks = await page.locator("p, div[class*='comment'], span[class*='text']").all()
            parsed_text = []
            
            for block in text_blocks[:8]:  # Sample top 8 blocks
                text = await block.inner_text()
                if len(text.strip()) > 25 and text.strip() not in parsed_text:
                    parsed_text.append(text.strip())
                    
            item["deep_content"] = parsed_text
        except Exception:
            item["deep_content"] = ["Sub-page text mining timeout/restricted."]
        finally:
            await page.close()

    async def archive_item(self, client: httpx.AsyncClient, item: dict, idx: int):
        """Asynchronously builds the local archive directory structures per post."""
        safe_title = self._clean_folder_name(item["title"]) or f"item_{idx + 1}"
        folder_name = f"item_{idx + 1}_{safe_title}"
        folder_path = os.path.join(self.base_output_dir, folder_name)
        os.makedirs(folder_path, exist_ok=True)

        print(f"📁 Exporting isolated asset node: /{self.base_output_dir}/{folder_name}")

        # Save Structural Dataset
        manifest = {"title": item["title"], "source_url": item["url"]}
        with open(os.path.join(folder_path, "index_manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=4, ensure_ascii=False)

        # Save Deep Extracted Text / Comments Blocks
        with open(os.path.join(folder_path, "inner_content_logs.json"), "w", encoding="utf-8") as f:
            json.dump(item.get("deep_content", []), f, indent=4, ensure_ascii=False)

        # Download Media Binary Assets if located
        img = item["media_url"]
        if img and img.startswith("http"):
            try:
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                res = await client.get(img, headers=headers, timeout=10.0)
                if res.status_code == 200:
                    ext = ".png" if ".png" in img else ".jpeg"
                    with open(os.path.join(folder_path, f"source_graphic{ext}"), "wb") as f:
                        f.write(res.content)
            except Exception:
                pass

    async def run(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1440, "height": 900}
            )
            
            page = await context.new_page()
            print(f"📡 Launching autonomous probe targeting: {self.target_url}")
            
            await page.goto(self.target_url, timeout=60000)
            await page.wait_for_timeout(6000) # Give dynamic script rendering a clear initialization buffer

            # Execute the heuristic visual layout parsing algorithm
            raw_nodes = await self.auto_discover_feed(page)
            
            # Clean duplicate URL strings
            seen = set()
            for node in raw_nodes:
                if node["url"] not in seen:
                    seen.add(node["url"])
                    self.results.append(node)
                    
            self.results = self.results[:self.limit]
            await page.close()

            if not self.results:
                print("❌ Semantic heuristics were unable to isolate clear data matrices on this URL layout.")
                await browser.close()
                return

            print(f"⚡ Discovered {len(self.results)} data paths. Spawning parallel child workers...")
            deep_tasks = [self.auto_extract_deep_text(context, item) for item in self.results]
            await asyncio.gather(*deep_tasks)
            await browser.close()

            print("\n🗂️ Distributing raw files into isolated folder hierarchies...")
            async with httpx.AsyncClient() as client:
                storage_tasks = [self.archive_item(client, item, idx) for idx, item in enumerate(self.results)]
                await asyncio.gather(*storage_tasks)

            print("\n" + "="*15 + " AUTONOMOUS DATA EXTRACTION CYCLE TERMINATED " + "="*15)
            print(f"🎉 Process Complete. Storage Engine Target compiled at: /{self.base_output_dir}\n")

if __name__ == "__main__":
    url_input = input("Enter ANY target URL website domain (e.g., youtube.com or news.yahoo.com): ").strip()
    volume_input = int(input("Target extraction item volume limit count: "))
    
    clock = time.time()
    scraper = AutonomousWebScraper(target_url=url_input, limit=volume_input)
    asyncio.run(scraper.run())
    print(f"⏱️ Fully autonomous execution completed in {round(time.time() - clock, 2)} seconds.\n")