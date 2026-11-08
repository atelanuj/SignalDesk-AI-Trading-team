import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text, Numeric, Integer, CheckConstraint, UniqueConstraint, create_engine
from decimal import Decimal
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import DATABASE_URL
from .migrations import apply_migrations


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Scan(Base):
    __tablename__ = 'scans'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    status: Mapped[str] = mapped_column(String(20), default='queued', index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    request: Mapped[dict] = mapped_column(JSONB)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Conversation(Base):
    __tablename__ = 'conversations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(100), default='New conversation')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Message(Base):
    __tablename__ = 'messages'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey('conversations.id', ondelete='CASCADE'), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    scan_id: Mapped[str | None] = mapped_column(String(36), ForeignKey('scans.id', ondelete='SET NULL'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Setting(Base):
    __tablename__ = 'settings'
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)


class PriceCache(Base):
    __tablename__ = 'price_cache'
    symbol: Mapped[str] = mapped_column(String(30), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    data: Mapped[dict] = mapped_column(JSONB)


class PaperTrade(Base):
    __tablename__ = 'paper_trades'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    market: Mapped[str] = mapped_column(String(2), index=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(10), default='OPEN', index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    entry_fees: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=0)
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    exit_fees: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=0)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default='')
    reflection: Mapped[str] = mapped_column(Text, default='')
    scan_id: Mapped[str | None] = mapped_column(String(36), ForeignKey('scans.id', ondelete='SET NULL'), nullable=True)
    signal_snapshot: Mapped[dict] = mapped_column(JSONB)
    wallet_market: Mapped[str | None] = mapped_column(String(2), nullable=True)


class PaperWallet(Base):
    __tablename__ = 'paper_wallets'
    __table_args__ = (CheckConstraint('cash >= 0', name='paper_wallet_cash_nonnegative'),)
    market: Mapped[str] = mapped_column(String(2), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3))
    cash: Mapped[Decimal] = mapped_column(Numeric(24, 4))
    starter_cash: Mapped[Decimal] = mapped_column(Numeric(24, 4))
    total_funding: Mapped[Decimal] = mapped_column(Numeric(24, 4))
    carry_in_funding: Mapped[Decimal] = mapped_column(Numeric(24, 4), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PaperCashMovement(Base):
    __tablename__ = 'paper_cash_movements'
    __table_args__ = (UniqueConstraint('trade_id', 'kind', name='paper_cash_trade_kind_unique'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    market: Mapped[str] = mapped_column(String(2), ForeignKey('paper_wallets.market'), index=True)
    trade_id: Mapped[str | None] = mapped_column(String(36), ForeignKey('paper_trades.id', ondelete='CASCADE'), nullable=True)
    kind: Mapped[str] = mapped_column(String(24))
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ResearchRun(Base):
    __tablename__ = 'research_runs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey('conversations.id', ondelete='CASCADE'), index=True)
    scan_id: Mapped[str | None] = mapped_column(String(36), ForeignKey('scans.id', ondelete='SET NULL'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    status: Mapped[str] = mapped_column(String(20), default='running')
    reports: Mapped[dict] = mapped_column(JSONB, default=dict)


class ResearchSourceCache(Base):
    __tablename__ = 'research_source_cache'
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    data: Mapped[dict] = mapped_column(JSONB)


engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)


def init_db():
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        apply_migrations(connection)
