"""
Database connection management.
"""

import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_recycle=3600,
    echo=False
)

SessionLocal = sessionmaker(bind=engine)


@contextmanager
def get_session():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        session.close()


def init_db():
    from data.storage.models import Base
    Base.metadata.create_all(engine)
    logger.info("Database tables created successfully")

    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
            conn.execute(text("""
                SELECT create_hypertable('daily_prices', 'date',
                    migrate_data => true,
                    if_not_exists => true
                )
            """))
            conn.commit()
            logger.info("TimescaleDB hypertable created on daily_prices")
    except Exception as e:
        logger.warning(f"TimescaleDB not available, using standard PostgreSQL: {e}")