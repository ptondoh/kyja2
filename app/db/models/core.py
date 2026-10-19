import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Role(str, enum.Enum):
    client = "client"
    hairdresser = "hairdresser"
    admin = "admin"


class MediaType(str, enum.Enum):
    image = "image"
    video = "video"


class MediaStatus(str, enum.Enum):
    processing = "processing"
    ready = "ready"
    failed = "failed"


class AppointmentStatus(str, enum.Enum):
    confirmed = "confirmed"
    cancelled = "cancelled"
    completed = "completed"


class ExperienceLevel(str, enum.Enum):
    junior = "junior"
    senior = "senior"
    expert = "expert"


class User(Base):
    """Compte de connexion (cliente, coiffeuse ou admin)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role, name="role"), default=Role.client)
    full_name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(50))
    address: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConsentRecord(Base):
    """Preuve légale de consentement (LPRPDE) : jamais supprimée ni anonymisée."""

    __tablename__ = "consent_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[str] = mapped_column(String(50))  # ex: marketing, terms
    granted: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(100), index=True)  # ex: coiffure, soin
    description: Mapped[str | None] = mapped_column(Text)
    base_duration_min: Mapped[int] = mapped_column(Integer)
    buffer_min: Mapped[int] = mapped_column(Integer, default=0)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    media: Mapped[list["Media"]] = relationship(
        back_populates="service", order_by="Media.position", cascade="all, delete-orphan"
    )
    resource_types: Mapped[list["ServiceResource"]] = relationship(cascade="all, delete-orphan")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    stock: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    media: Mapped[list["Media"]] = relationship(
        back_populates="product", order_by="Media.position", cascade="all, delete-orphan"
    )


class Media(Base):
    """Image ou vidéo rattachée à un service OU à un produit."""

    __tablename__ = "media"
    __table_args__ = (
        CheckConstraint(
            "(service_id IS NOT NULL)::int + (product_id IS NOT NULL)::int = 1",
            name="media_one_owner",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int | None] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[MediaType] = mapped_column(Enum(MediaType, name="media_type"))
    url: Mapped[str | None] = mapped_column(String(1000))
    storage_key: Mapped[str | None] = mapped_column(String(500))
    poster_url: Mapped[str | None] = mapped_column(String(1000))
    poster_key: Mapped[str | None] = mapped_column(String(500))
    error: Mapped[str | None] = mapped_column(String(500))
    mime_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    duration_s: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[MediaStatus] = mapped_column(
        Enum(MediaStatus, name="media_status"), default=MediaStatus.processing
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    service: Mapped[Service | None] = relationship(back_populates="media")
    product: Mapped[Product | None] = relationship(back_populates="media")


class Hairdresser(Base):
    __tablename__ = "hairdressers"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    experience_level: Mapped[ExperienceLevel] = mapped_column(
        Enum(ExperienceLevel, name="experience_level"), default=ExperienceLevel.senior
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship()
    slots: Mapped[list["AvailabilitySlot"]] = relationship(cascade="all, delete-orphan")


class AvailabilitySlot(Base):
    """Plage de disponibilité hebdomadaire récurrente (weekday 0 = lundi)."""

    __tablename__ = "availability_slots"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="slot_weekday"),
        CheckConstraint("start_minute < end_minute", name="slot_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hairdresser_id: Mapped[int] = mapped_column(
        ForeignKey("hairdressers.id", ondelete="CASCADE"), index=True
    )
    weekday: Mapped[int] = mapped_column(Integer)
    start_minute: Mapped[int] = mapped_column(Integer)  # minutes depuis minuit
    end_minute: Mapped[int] = mapped_column(Integer)


class Resource(Base):
    """Équipement partagé (bac de lavage, casque…)."""

    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)  # ex: wash_basin, dryer_hood
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ServiceResource(Base):
    """Type d'équipement requis par une prestation."""

    __tablename__ = "service_resources"
    __table_args__ = (UniqueConstraint("service_id", "resource_kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"))
    resource_kind: Mapped[str] = mapped_column(String(50))


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (CheckConstraint("starts_at < ends_at", name="appt_range"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    hairdresser_id: Mapped[int] = mapped_column(ForeignKey("hairdressers.id"), index=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[AppointmentStatus] = mapped_column(
        Enum(AppointmentStatus, name="appointment_status"), default=AppointmentStatus.confirmed
    )
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    resources: Mapped[list["AppointmentResource"]] = relationship(cascade="all, delete-orphan")


class AppointmentResource(Base):
    __tablename__ = "appointment_resources"

    appointment_id: Mapped[int] = mapped_column(
        ForeignKey("appointments.id", ondelete="CASCADE"), primary_key=True
    )
    resource_id: Mapped[int] = mapped_column(ForeignKey("resources.id"), primary_key=True)


class FinancialRecord(Base):
    """Trace comptable : jamais supprimée, anonymisée lors du droit à l'oubli."""

    __tablename__ = "financial_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int | None] = mapped_column(ForeignKey("appointments.id"), unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    method: Mapped[str] = mapped_column(String(30), default="salon")  # cash / interac
    validated_by_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
