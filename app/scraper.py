from app.reddit_client import get_posts
from app.service import save_posts

def run_scraper(subreddit: str, limit: int = 20):
    posts = get_posts(subreddit, limit)
    save_posts(posts)
    return len(posts)