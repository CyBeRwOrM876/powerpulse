"""Database models (SQLAlchemy 2.0). SQLite for the hackathon, PostgreSQL by changing PP_DB_URL."""
from __future__ import annotations
import time
from sqlalchemy import (create_engine, Integer, String, Float, Boolean, Text, ForeignKey, BigInteger,
                        UniqueConstraint, Index, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from .config import settings


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    pw_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(16), default="owner")  # owner | verifier | admin
    created: Mapped[int] = mapped_column(BigInteger, default=lambda: int(time.time()))


class Business(Base):
    __tablename__ = "businesses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    sector: Mapped[str] = mapped_column(String(40), default="custom")
    region: Mapped[str] = mapped_column(String(80))
    group_id: Mapped[str] = mapped_column(String(20))
    schedule_id: Mapped[str] = mapped_column(String(60))
    days_per_week: Mapped[float] = mapped_column(Float, default=6)
    tariff: Mapped[float] = mapped_column(Float, default=1.8)
    diesel_price: Mapped[float] = mapped_column(Float, default=30.0)
    existing_gen_kw: Mapped[float] = mapped_column(Float, default=0.0)
    equipment: Mapped[list["Equipment"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class Equipment(Base):
    __tablename__ = "equipment"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    power_kw: Mapped[float] = mapped_column(Float)
    hours_per_day: Mapped[float] = mapped_column(Float)
    start_hour: Mapped[int] = mapped_column(Integer, default=8)
    critical: Mapped[bool] = mapped_column(Boolean, default=False)
    flexible: Mapped[bool] = mapped_column(Boolean, default=False)
    value_per_hour: Mapped[float] = mapped_column(Float, default=0.0)
    spoil_per_hour: Mapped[float] = mapped_column(Float, default=0.0)
    channel: Mapped[int | None] = mapped_column(Integer, nullable=True)  # CT channel on the node, 0-5


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    device_key: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    secret_hex: Mapped[str] = mapped_column(String(64))
    last_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    last_ts: Mapped[int] = mapped_column(BigInteger, default=0)
    last_grid: Mapped[int] = mapped_column(Integer, default=1)


class Reading(Base):
    __tablename__ = "readings"
    __table_args__ = (UniqueConstraint("device_id", "seq"), Index("ix_reading_dev_ts", "device_id", "ts"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"))
    seq: Mapped[int] = mapped_column(BigInteger)
    ts: Mapped[int] = mapped_column(BigInteger)
    v: Mapped[float] = mapped_column(Float)
    pf: Mapped[float] = mapped_column(Float)
    f: Mapped[float] = mapped_column(Float)
    grid: Mapped[int] = mapped_column(Integer)
    watts: Mapped[str] = mapped_column(Text)  # comma-separated integer watts per channel


class Outage(Base):
    __tablename__ = "outages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    start_ts: Mapped[int] = mapped_column(BigInteger)
    end_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source: Mapped[str] = mapped_column(String(16))  # measured | diary


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)
    created: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[str] = mapped_column(Text)  # canonical JSON, exactly what was signed
    sig_hex: Mapped[str] = mapped_column(String(128))
    key_id: Mapped[str] = mapped_column(String(32))


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[int] = mapped_column(BigInteger)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(80))
    detail: Mapped[str] = mapped_column(Text, default="")
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


def make_engine(url: str | None = None):
    url = url or settings.db_url
    kw = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    eng = create_engine(url, pool_pre_ping=True, **kw)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragmas(dbapi, _):
            cur = dbapi.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return eng


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(eng=None):
    Base.metadata.create_all(eng or engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
