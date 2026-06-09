import os
import praw
from dotenv import load_dotenv

load_dotenv()

reddit = praw.Reddit(
    client_id=os.getenv("REDDIT_CLIENT_ID"),
    client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
    user_agent=os.getenv("REDDIT_USER_AGENT"),
)

def get_posts(subreddit: str, limit: int = 20):
    data = []

    for post in reddit.subreddit(subreddit).hot(limit=limit):
        data.append({
            "id": post.id,
            "subreddit": subreddit,
            "title": post.title,
            "text": post.selftext or "",
            "url": post.url,
            "score": post.score,
            "comments": post.num_comments,
            "is_video": post.is_video
        })

    return data