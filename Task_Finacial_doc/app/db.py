from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

     # SQLite database configuration
SQLALCHEMY_DATABASE_URL = "sqlite:///./app.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
)


SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

        # Base class for all database models
Base = declarative_base()


def get_db():
                    # Create and close database session for each request
    db = SessionLocal()

    try:
        yield db

    finally:
        db.close()