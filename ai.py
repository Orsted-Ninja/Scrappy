import asyncio
import json
import os
import time
import httpx
from playwright.async_api import async_playwright

class ProductionRedditScraper:
    def __init__(self, subreddit: str, limit: int):
        self.subreddit = subreddit
        self.limit = limit
        self.base_url = f"https://www.reddit.com/r/{subreddit}/"
        self.results = []
        
        # Setup local file system paths
        self.output_dir = f"scraped_{self.subreddit}"
        self.images_dir = os.path.join(self.output_dir, "images")
        os.makedirs(self.images_dir, exist_ok=True)

    async def download_image_asset(self, client: httpx.AsyncClient, url: str, index: int):
        """Asynchronously downloads the target image file directly to the local disk."""
        if not url or "No image" in url:
            return "N/A"
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            response = await client.get(url, headers=headers, timeout=15.0)
            if response.status_code == 200:
                # Deduce clean extension or default to jpeg
                ext = ".png" if ".png" in url else ".jpeg"
                filename = f"post_{index + 1}{ext}"
                filepath = os.path.join(self.images_dir, filename)
                
                with open(filepath, "wb") as f:
                    f.write(response.content)
                return filepath
        except Exception as e:
            return f"Download Failed: {str(e)}"
        return "N/A"

    async def scrape_comments_worker(self, context, post_item: dict):
        """Worker task running concurrently inside an isolated browser tab context."""
        page = await context.new_page()
        try:
            await page.goto(post_item["url"], timeout=45000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000) # Cooldown for structural hydration

            print(f"💬 Digging comments from: '{post_item['title'][:35]}...'")
            comment_elements = await page.locator("shreddit-comment").all()
            
            captured_comments = []
            for comment in comment_elements[:5]: # Extract top 5 comments
                author = await comment.get_attribute("author") or "Unknown"
                body_element = comment.locator("div[-slot='comment'], p").first
                body_text = await body_element.inner_text() if await body_element.count() > 0 else ""
                
                if body_text.strip():
                    captured_comments.append({
                        "author": f"u/{author}",
                        "body": body_text.strip()
                    })
            post_item["comments"] = captured_comments
        except Exception as e:
            post_item["comments"] = [{"error": f"Task Timeout/Block: {str(e)}"}]
        finally:
            await page.close()

    async def run(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False, # Set to True if running on an external server context
                args=["--disable-blink-features=AutomationControlled"]
            )
            
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )
            
            # Step 1: Parse Directory Feed Index
            main_page = await context.new_page()
            print(f"📡 Establishing secure pipeline to r/{self.subreddit}...")
            await main_page.goto(self.base_url, timeout=60000)
            await main_page.wait_for_timeout(5000)

            post_elements = await main_page.locator("shreddit-post").all()
            seen_links = set()

            for post in post_elements:
                href = await post.get_attribute("permalink")
                if not href:
                    continue
                
                full_url = href if href.startswith("https") else f"https://www.reddit.com{href}"
                if full_url in seen_links:
                    continue
                seen_links.add(full_url)

                title = await post.get_attribute("post-title") or "No Title"
                author = await post.get_attribute("author") or "Unknown"
                
                image_el = post.locator("img[src*='preview.redd.it'], img[src*='i.redd.it']").first
                image_url = await image_el.get_attribute("src") if await image_el.count() > 0 else "No image URL"

                self.results.append({
                    "title": title.strip(),
                    "author": f"u/{author}",
                    "url": full_url,
                    "image_source_url": image_url,
                    "local_image_path": "Pending",
                    "comments": []
                })

                if len(self.results) >= self.limit:
                    break

            await main_page.close()

            if not self.results:
                print("❌ Fatal: Zero structural components captured from feed mapping sequence.")
                await browser.close()
                return

            print(f"⚡ Feed mapped. Firing concurrent browser contexts across {len(self.results)} target nodes...")
            
            # Step 2: Extract Thread Comments in Parallel 
            comment_tasks = [self.scrape_comments_worker(context, item) for item in self.results]
            await asyncio.gather(*comment_tasks)
            await browser.close()
            
            # Step 3: Run High-Speed Async Asset Downloader
            print("\n🖼️ Launching concurrent media download pipeline...")
            async with httpx.AsyncClient() as client:
                download_tasks = [
                    self.download_image_asset(client, item["image_source_url"], idx)
                    for idx, item in enumerate(self.results)
                ]
                local_paths = await asyncio.gather(*download_tasks)
                
                # Map downloaded filepaths back to results schema
                for idx, path in enumerate(local_paths):
                    self.results[idx]["local_image_path"] = path

            # Step 4: Persist Clean Structural Data block to Local Disk
            json_filename = os.path.join(self.output_dir, "metadata_manifest.json")
            with open(json_filename, "w", encoding="utf-8") as f:
                json.dump(self.results, f, indent=4, ensure_ascii=False)
                
            print("\n" + "="*15 + " DATA PROCESSING AND AGGREGATION COMPLETE " + "="*15)
            print(f"📁 Root output directory generated: {self.output_dir}")
            print(f"💾 Structured metadata manifest compiled to: {json_filename}")
            print(f"🖼️ Images saved in: {self.images_dir}")

if __name__ == "__main__":
    # Ensure any required non-standard runtime library is verified
    try:
        import httpx
    except ImportError:
        print("Required Dependency missing. Please execute: pip install httpx")
        exit()

    target_sub = input("Enter target subreddit (e.g., memes): ").strip()
    target_limit = int(input("Target extraction volume (integer value): "))
    
    start_execution_clock = time.time()
    scraper = ProductionRedditScraper(subreddit=target_sub, limit=target_limit)
    asyncio.run(scraper.run())
    
    print(f"⏱️ Overall job executed completely in {round(time.time() - start_execution_clock, 2)} seconds.\n")