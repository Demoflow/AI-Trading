"""
SQLAlchemy models for the trading database.

Three core tables:
  - stocks: Your universe metadata
  - daily_prices: OHLCV bars (the workhorse table)
  - data_quality_log: Tracks ingestion issues
"""

from sqlalchemy import (
    Column, String, Float, BigInteger, Date, DateTime,
    Integer, Boolean, ForeignKey, UniqueConstraint, Index, Text,
    create_engine
)
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()


class Stock(Base):
    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), unique=True, nullable=False, index=True)
    name = Column(String(200))
    sector = Column(String(100))
    industry = Column(String(100))
    market_cap_tier = Column(String(20))
    is_active = Column(Boolean, default=True)
    added_date = Column(Date, default=datetime.utcnow)

    prices = relationship("DailyPrice", back_populates="stock")


class DailyPrice(Base):
    __tablename__ = "daily_prices"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False)
    symbol = Column(String(10), nullable=False)
    date = Column(Date, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    adj_close = Column(Float, nullable=False)
    volume = Column(BigInteger, nullable=False)

    data_source = Column(String(50), default="yfinance")
    ingested_at = Column(DateTime, default=datetime.utcnow)

    stock = relationship("Stock", back_populates="prices")

    __table_args__ = (
        UniqueConstraint("symbol", "date", name="uq_symbol_date"),
        Index("idx_symbol_date", "symbol", "date"),
        Index("idx_date", "date"),
    )


class DataQualityLog(Base):
    __tablename__ = "data_quality_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False)
    date = Column(Date)
    issue_type = Column(String(50), nullable=False)
    description = Column(Text)
    severity = Column(String(20))
    resolved = Column(Boolean, default=False)
    detected_at = Column(DateTime, default=datetime.utcnow)


class EarningsCalendar(Base):
    __tablename__ = "earnings_calendar"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False)
    earnings_date = Column(Date, nullable=False)
    timing = Column(String(20))
    eps_estimate = Column(Float)
    eps_actual = Column(Float)
    surprise_pct = Column(Float)

    __table_args__ = (
        UniqueConstraint("symbol", "earnings_date", name="uq_earnings"),
    )
    