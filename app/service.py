from app.db import SessionLocal, Post

def save_posts(posts: list[dict]):
    session = SessionLocal()

    for p in posts:
        exists = session.get(Post, p["id"])

        if exists:
            continue

        db_post = Post(
            id=p["id"],
            subreddit=p["subreddit"],
            title=p["title"],
            text=p["text"],
            url=p["url"],
            score=p["score"],
            comments=p["comments"]
        )

        session.add(db_post)

    session.commit()
    session.close()


def get_posts(subreddit: str):
    session = SessionLocal()

    result = session.query(Post).filter(Post.subreddit == subreddit).all()

    session.close()

    return [
        {
            "id": r.id,
            "title": r.title,
            "score": r.score,
            "url": r.url
        }
        for r in result
    ]