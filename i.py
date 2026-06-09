import asyncio
import json
import os
import re
import time
import httpx
from playwright.async_api import async_playwright

class PerPostRedditArchiver:
    def __init__(self, subreddit: str, limit: int):
        self.subreddit = subreddit
        self.limit = limit
        self.base_url = f"https://www.reddit.com/r/{subreddit}/"
        self.results = []
        
        # Core subreddit parent folder path
        self.base_output_dir = f"scraped_{self.subreddit}"
        os.makedirs(self.base_output_dir, exist_ok=True)

    def _clean_folder_name(self, text: str) -> str:
        """Removes illegal characters, replaces spaces, and strips trailing dots/spaces for Windows safety."""
        # 1. Remove illegal OS characters: \ / * ? : " < > |
        clean = re.sub(r'[\\/*?:"<>|]', "", text)
        # 2. Replace spaces with underscores
        clean = clean.strip().replace(" ", "_")
        # 3. CRITICAL: Strip any trailing dots or underscores so Windows doesn't choke
        clean = clean.rstrip("._")
        return clean[:50]  # Cap length to avoid OS path length limits

    async def download_post_assets(self, client: httpx.AsyncClient, post_item: dict, index: int):
        """Creates an isolated folder structure for this post and saves its assets individually."""
        # 1. Create unique, safe folder name layout for the post
        safe_title = self._clean_folder_name(post_item["title"])
        if not safe_title:
            safe_title = f"untitled_post_{index + 1}"
            
        post_folder_name = f"post_{index + 1}_{safe_title}"
        post_folder_path = os.path.join(self.base_output_dir, post_folder_name)
        os.makedirs(post_folder_path, exist_ok=True)

        print(f"📂 Archiving assets locally into folder: /{post_folder_name}")

        # 2. Save Title Asset
        title_path = os.path.join(post_folder_path, "title.txt")
        with open(title_path, "w", encoding="utf-8") as f:
            f.write(post_item["title"])

        # 3. Save Comments Asset
        comments_path = os.path.join(post_folder_path, "comments.json")
        with open(comments_path, "w", encoding="utf-8") as f:
            json.dump(post_item["comments"], f, indent=4, ensure_ascii=False)

        # 4. Download and Save Image Asset
        img_url = post_item["image_source_url"]
        if img_url and "No image" not in img_url:
            try:
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                response = await client.get(img_url, headers=headers, timeout=15.0)
                if response.status_code == 200:
                    ext = ".png" if ".png" in img_url else ".jpeg"
                    image_path = os.path.join(post_folder_path, f"meme_image{ext}")
                    with open(image_path, "wb") as f:
                        f.write(response.content)
            except Exception as e:
                with open(os.path.join(post_folder_path, "media_download_error.txt"), "w") as err_f:
                    err_f.write(f"Image download failed: {str(e)}")

    async def scrape_comments_worker(self, context, post_item: dict):
        """Worker task executing concurrently to gather comment layers from separate tabs."""
        page = await context.new_page()
        try:
            await page.goto(post_item["url"], timeout=45000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            comment_elements = await page.locator("shreddit-comment").all()
            captured_comments = []
            
            for comment in comment_elements[:5]:  # Grabs top 5 comments
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
            post_item["comments"] = [{"error": f"Failed to load comments: {str(e)}"}]
        finally:
            await page.close()

    async def run(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )
            
            # Step 1: Map out the Main Subreddit Feed
            main_page = await context.new_page()
            print(f" Connecting to r/{self.subreddit} ")
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
                    "comments": []
                })

                if len(self.results) >= self.limit:
                    break

            await main_page.close()

            if not self.results:
                print("Mapping failed. No structural elements collected.")
                await browser.close()
                return

            print(f"\n⚡ Feed Mapped. Fetching deep comment layouts concurrently across {len(self.results)} links...")
            
            # Step 2: Grab comments concurrently in parallel tabs
            comment_tasks = [self.scrape_comments_worker(context, item) for item in self.results]
            await asyncio.gather(*comment_tasks)
            await browser.close()
            
            # Step 3: Parse and distribute data to individual post files
            print("\nCreating files")
            async with httpx.AsyncClient() as client:
                storage_tasks = [
                    self.download_post_assets(client, item, idx) 
                    for idx, item in enumerate(self.results)
                ]
                await asyncio.gather(*storage_tasks)

            print("\n" + "DONE")
            print(f" Check your new database tree at: /{self.base_output_dir}\n")

if __name__ == "__main__":
    target_sub = input("Enter target subreddit : ").strip()
    target_limit = int(input("Target extraction volume: "))
    
    start_time = time.time()
    scraper = PerPostRedditArchiver(subreddit=target_sub, limit=target_limit)
    asyncio.run(scraper.run())
    
    print(f"TC{round(time.time() - start_time, 2)} seconds.\n")