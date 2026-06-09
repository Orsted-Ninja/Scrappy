import os
from sqlalchemy import create_engine, Column, String, Integer, Text
from sqlalchemy.orm import declarative_base, sessionmaker

# ✅ Ensure DB folder exists
os.makedirs("data", exist_ok=True)

# ✅ SQLite DB path
DATABASE_URL = "sqlite:///data/reddit.db"

# Engine
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}  # important for FastAPI
)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base model
Base = declarative_base()


# -----------------------
# DB Model
# -----------------------
class Post(Base):
    __tablename__ = "posts"

    id = Column(String, primary_key=True, index=True)
    subreddit = Column(String, index=True)
    title = Column(Text)
    text = Column(Text)
    url = Column(Text)
    score = Column(Integer)
    comments = Column(Integer)


# -----------------------
# Create tables
# -----------------------
def init_db():
    Base.metadata.create_all(bind=engine)