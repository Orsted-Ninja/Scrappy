from fastapi import FastAPI
from app.db import init_db
from app.scraper import run_scraper
from app.service import get_posts

app = FastAPI()

init_db()


@app.get("/")
def home():
    return {"message": "Reddit API working"}


@app.post("/scrape/{subreddit}")
def scrape(subreddit: str, limit: int = 20):
    count = run_scraper(subreddit, limit)
    return {"scraped": count}


@app.get("/posts/{subreddit}")
def posts(subreddit: str):
    return get_posts(subreddit)