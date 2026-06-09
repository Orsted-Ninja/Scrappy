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
        clean = re.sub(r'[\\/*?:"<>|]', "", text)
        clean = clean.strip().replace(" ", "_")
        clean = clean.rstrip("._")
        return clean[:50]  # Cap length to avoid OS path length limits

    async def download_post_assets(self, client: httpx.AsyncClient, post_item: dict, index: int):
        """Creates an isolated folder structure for this post and saves its assets individually."""
        safe_title = self._clean_folder_name(post_item["title"])
        if not safe_title:
            safe_title = f"untitled_post_{index + 1}"
            
        post_folder_name = f"post_{index + 1}_{safe_title}"
        post_folder_path = os.path.join(self.base_output_dir, post_folder_name)
        os.makedirs(post_folder_path, exist_ok=True)

        print(f" Archiving assets locally into folder: /{post_folder_name}")

        # 1. Save Meta & Title Asset (With absolute time of origin)
        title_path = os.path.join(post_folder_path, "post_details.txt")
        with open(title_path, "w", encoding="utf-8") as f:
            f.write(f"Title: {post_item['title']}\n")
            f.write(f"Author: {post_item['author']}\n")
            f.write(f"Post Origin Time (UTC): {post_item['created_at']}\n")
            f.write(f"URL: {post_item['url']}\n")

        # 2. Save Comments Asset (With individual comment origin timestamps)
        comments_path = os.path.join(post_folder_path, "comments.json")
        with open(comments_path, "w", encoding="utf-8") as f:
            json.dump(post_item["comments"], f, indent=4, ensure_ascii=False)

        # 3. Save Screenshot Asset
        temp_screenshot_path = post_item.get("temp_screenshot_path")
        if temp_screenshot_path and os.path.exists(temp_screenshot_path):
            final_screenshot_path = os.path.join(post_folder_path, "post_screenshot.png")
            os.rename(temp_screenshot_path, final_screenshot_path)

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

    async def scrape_comments_worker(self, context, post_item: dict, index: int):
        """Worker task executing concurrently to gather ALL comments and a browser screenshot."""
        page = await context.new_page()
        try:
            # Open post page
            await page.goto(post_item["url"], timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)

            # --- Scroll aggressively to trigger infinite scrolling for all comments ---
            last_height = await page.evaluate("document.body.scrollHeight")
            no_change_count = 0
            while no_change_count < 3:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                await page.wait_for_timeout(2500)
                new_height = await page.evaluate("document.body.scrollHeight")
                if new_height == last_height:
                    no_change_count += 1
                else:
                    no_change_count = 0
                    last_height = new_height

            # --- Capture Browser Window Screenshot ---
            temp_screenshot_path = os.path.join(self.base_output_dir, f"temp_ss_{index}.png")
            await page.screenshot(path=temp_screenshot_path, full_page=True)
            post_item["temp_screenshot_path"] = temp_screenshot_path

            # --- Deep Evaluation of Comments ---
            # Evaluates directly in browser context to bypass Shadow DOM retrieval issues
            comments_data = await page.evaluate("""() => {
                const commentNodes = document.querySelectorAll("shreddit-comment");
                const extracted = [];
                
                commentNodes.forEach(comment => {
                    const author = comment.getAttribute("author") || "Unknown_User";
                    
                    // Reddit saves the exact origin time inside a <time> element or attribute
                    let originTime = comment.getAttribute("created-timestamp") || comment.getAttribute("timestamp");
                    if (!originTime) {
                        const timeEl = comment.querySelector("time");
                        if (timeEl) originTime = timeEl.getAttribute("datetime");
                    }
                    
                    // Find the comment body text element safely
                    const bodyEl = comment.querySelector('div[slot="comment"]') || comment.querySelector('p') || comment.querySelector('#comment-content');
                    const bodyText = bodyEl ? bodyEl.innerText.trim() : "";
                    
                    if (bodyText) {
                        extracted.push({
                            "author": author.startsWith("u/") ? author : `u/${author}`,
                            "created_at": originTime || "Unknown_Origin_Time",
                            "body": bodyText
                        });
                    }
                });
                return extracted;
            }""")
            
            post_item["comments"] = comments_data
            
        except Exception as e:
            post_item["comments"] = [{"error": f"Failed to load comments/screenshot: {str(e)}"}]
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
                
                # Extract Post Origin Time directly using fallback layers
                post_time = await post.evaluate("""el => {
                    let t = el.getAttribute('created-timestamp') || el.getAttribute('timestamp');
                    if(!t) {
                        let timeEl = el.querySelector('time');
                        if(timeEl) t = timeEl.getAttribute('datetime');
                    }
                    return t;
                }""") or "Unknown_Origin_Time"

                image_el = post.locator("img[src*='preview.redd.it'], img[src*='i.redd.it']").first
                image_url = await image_el.get_attribute("src") if await image_el.count() > 0 else "No image URL"

                self.results.append({
                    "title": title.strip(),
                    "author": f"u/{author}",
                    "created_at": post_time,
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

            print(f"\nFeed Mapped. Fetching full comment threads & screenshots concurrently across {len(self.results)} links...")
            
            # Step 2: Grab all comments & screenshots concurrently in parallel tabs
            comment_tasks = [self.scrape_comments_worker(context, item, idx) for idx, item in enumerate(self.results)]
            await asyncio.gather(*comment_tasks)
            await browser.close()
            
            # Step 3: Parse and distribute data to individual post files
            print("\nCreating files and organizing assets...")
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
    
    print(f"TC {round(time.time() - start_time, 2)} seconds.\n")
