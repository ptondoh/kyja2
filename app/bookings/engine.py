"""Moteur de réservation (voir PRD 4.A et ARCHITECTURE « Moteur de Réservation »).

Tous les horaires sont stockés en UTC ; les disponibilités hebdomadaires des coiffeuses sont
exprimées en heure locale du salon (`salon_timezone`).
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.core import (
    Appointment,
    AppointmentResource,
    AppointmentStatus,
    AvailabilitySlot,
    BookingMode,
    BookingPhoto,
    FinancialRecord,
    Hairdresser,
    PricingMode,
    Resource,
    Service,
    ServiceResource,
    User,
)
from app.db.models.settings import get_int_setting


MAX_PHOTOS = 10


class BookingError(Exception):
    """Réservation impossible ; le message est affichable à la cliente."""


class SlotUnavailable(BookingError):
    pass


def salon_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().salon_timezone)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- Calcul de la durée -------------------------------------------------------


def compute_duration(base_min: int, coefficient: Decimal | float | str, buffer_min: int, step: int = 15) -> int:
    """Durée = arrondi_au_pas(durée de base × coefficient) + temps de repos (PRD 4.A)."""
    if base_min <= 0 or step <= 0 or buffer_min < 0:
        raise ValueError("paramètres de durée invalides")
    raw = Decimal(base_min) * Decimal(str(coefficient))
    steps = (raw / step).to_integral_value(rounding=ROUND_CEILING)
    return int(steps) * step + buffer_min


@dataclass(frozen=True)
class Quote:
    """Résultat du calcul pour une coiffeuse donnée (mémorisé dans le rendez-vous)."""

    base_duration_min: int
    coefficient: Decimal
    buffer_min: int
    duration_min: int
    estimated_price: Decimal | None


def quote_for(db: Session, service: Service, hairdresser: Hairdresser) -> Quote:
    step = get_int_setting(db, "calendar_step_min")
    coeff = Decimal(hairdresser.duration_coefficient)
    return Quote(
        base_duration_min=service.base_duration_min,
        coefficient=coeff,
        buffer_min=service.buffer_min,
        duration_min=compute_duration(service.base_duration_min, coeff, service.buffer_min, step),
        estimated_price=service.price if service.pricing_mode == PricingMode.estimate else None,
    )


def booking_mode_for(service: Service) -> BookingMode:
    return BookingMode.catalog if service.pricing_mode == PricingMode.estimate else BookingMode.custom


# --- Disponibilités -------------------------------------------------------------


def _local_dt(day: date, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, minute // 60, minute % 60, tzinfo=salon_tz())


def _required_kinds(db: Session, service_id: int) -> list[str]:
    return sorted(
        db.scalars(
            select(ServiceResource.resource_kind).where(ServiceResource.service_id == service_id)
        ).all()
    )


def _confirmed_overlap(start: datetime, end: datetime):
    return (
        Appointment.status == AppointmentStatus.confirmed,
        Appointment.starts_at < end,
        Appointment.ends_at > start,
    )


def _windows(db: Session, hairdresser_id: int, day: date) -> list[tuple[datetime, datetime]]:
    slots = db.scalars(
        select(AvailabilitySlot)
        .where(AvailabilitySlot.hairdresser_id == hairdresser_id, AvailabilitySlot.weekday == day.weekday())
        .order_by(AvailabilitySlot.start_minute)
    ).all()
    return [
        (
            _local_dt(day, s.start_minute).astimezone(timezone.utc),
            _local_dt(day, s.end_minute).astimezone(timezone.utc),
        )
        for s in slots
    ]


def _free_resource_ids(db: Session, kind: str, start: datetime, end: datetime, lock: bool) -> list[int]:
    candidates = select(Resource.id).where(Resource.kind == kind, Resource.is_active.is_(True)).order_by(Resource.id)
    if lock:
        # Verrou dans une requête SÉPARÉE : après une attente de verrou, PostgreSQL ne ré-évalue pas
        # les sous-requêtes (instantané périmé). On relit donc les réservations APRÈS avoir le verrou.
        candidates = candidates.with_for_update()
    ids = list(db.scalars(candidates).all())
    busy = set(
        db.scalars(
            select(AppointmentResource.resource_id)
            .join(Appointment)
            .where(AppointmentResource.resource_id.in_(ids), *_confirmed_overlap(start, end))
        ).all()
    )
    return [i for i in ids if i not in busy]


def _bounds(db: Session, now: datetime) -> tuple[datetime, datetime]:
    earliest = now + timedelta(minutes=get_int_setting(db, "booking_min_notice_min"))
    latest = now + timedelta(days=get_int_setting(db, "booking_horizon_days"))
    return earliest, latest


def free_starts(
    db: Session, service: Service, hairdresser: Hairdresser, day: date, now: datetime | None = None, limit: int | None = None
) -> list[datetime]:
    """Débuts possibles (UTC) pour cette coiffeuse ce jour-là : la durée calculée doit tenir en entier."""
    now = now or utcnow()
    step = get_int_setting(db, "calendar_step_min")
    duration = timedelta(minutes=quote_for(db, service, hairdresser).duration_min)
    earliest, latest = _bounds(db, now)
    kinds = _required_kinds(db, service.id)

    windows = _windows(db, hairdresser.id, day)
    if not windows:
        return []
    day_start, day_end = min(w[0] for w in windows), max(w[1] for w in windows)
    busy = db.execute(
        select(Appointment.starts_at, Appointment.ends_at).where(
            Appointment.hairdresser_id == hairdresser.id, *_confirmed_overlap(day_start, day_end)
        )
    ).all()

    out = []
    for w_start, w_end in windows:
        t = w_start
        while t + duration <= w_end:
            end = t + duration
            if earliest <= t <= latest and not any(b0 < end and b1 > t for b0, b1 in busy):
                if all(_free_resource_ids(db, k, t, end, lock=False) for k in kinds):
                    out.append(t)
                    if limit and len(out) >= limit:
                        return out
            t += timedelta(minutes=step)
    return out


def active_hairdressers(db: Session) -> list[Hairdresser]:
    return list(
        db.scalars(select(Hairdresser).join(User).where(Hairdresser.is_active.is_(True), User.is_active.is_(True)).order_by(Hairdresser.id))
    )


def available_days(
    db: Session, service: Service, hairdresser_id: int | None, month_first: date, now: datetime | None = None
) -> set[date]:
    """Jours du mois où au moins un créneau est réservable (pour griser le calendrier)."""
    now = now or utcnow()
    candidates = [h for h in active_hairdressers(db) if hairdresser_id in (None, h.id)]
    today = now.astimezone(salon_tz()).date()
    last = (now + timedelta(days=get_int_setting(db, "booking_horizon_days"))).astimezone(salon_tz()).date()
    days: set[date] = set()
    day = month_first
    while day.month == month_first.month:
        if today <= day <= last:
            for h in candidates:
                if free_starts(db, service, h, day, now, limit=1):
                    days.add(day)
                    break
        day += timedelta(days=1)
    return days


def has_any_availability(db: Session) -> bool:
    """Au moins une coiffeuse active a des plages de disponibilité (sinon : message d'aide au salon)."""
    ids = [h.id for h in active_hairdressers(db)]
    return bool(ids) and bool(db.scalar(select(AvailabilitySlot.id).where(AvailabilitySlot.hairdresser_id.in_(ids)).limit(1)))


def custom_request_service(db: Session) -> Service:
    """Prestation intégrée « Demande sur mesure » (mode 2). Créée à l'installation par la migration ;
    recréée ici si elle manque. Sa durée de base et son temps de repos se règlent dans l'admin."""
    existing = db.scalar(select(Service).where(Service.is_custom_request.is_(True)))
    if existing:
        return existing
    svc = Service(
        name="Demande sur mesure",
        description="Coiffure décrite par la cliente. Prix confirmé au salon.",
        base_duration_min=120,
        buffer_min=15,
        pricing_mode=PricingMode.quote,
        price=None,
        is_active=True,
        is_custom_request=True,
    )
    db.add(svc)
    try:
        db.commit()
    except IntegrityError:  # créée en parallèle par une autre requête
        db.rollback()
        return db.scalar(select(Service).where(Service.is_custom_request.is_(True)))
    return svc


def slots_for(db: Session, service: Service, hairdresser_id: int | None, day: date, now: datetime | None = None) -> list[tuple[datetime, Hairdresser]]:
    """Créneaux proposés. `hairdresser_id=None` (« peu importe ») : première coiffeuse disponible."""
    candidates = [h for h in active_hairdressers(db) if hairdresser_id in (None, h.id)]
    chosen: dict[datetime, Hairdresser] = {}
    for h in candidates:
        for start in free_starts(db, service, h, day, now):
            chosen.setdefault(start, h)
    return sorted(chosen.items())


# --- Réservation (transaction atomique) ---------------------------------------


def create_booking(
    db: Session,
    *,
    client: User,
    service: Service,
    hairdresser_id: int | None,
    start: datetime,
    notes: str | None = None,
    photo_ids: list[int] | None = None,
    now: datetime | None = None,
) -> Appointment:
    """Crée le rendez-vous. Prix, durée et coefficient sont **recalculés ici** : le formulaire n'est jamais cru."""
    now = now or utcnow()
    if not service.is_active:
        raise BookingError("Cette prestation n'est plus disponible.")
    if start.tzinfo is None:
        raise BookingError("Horaire invalide.")
    start = start.astimezone(timezone.utc)
    step = get_int_setting(db, "calendar_step_min")
    earliest, latest = _bounds(db, now)
    if start < earliest:
        raise SlotUnavailable("Ce créneau est trop proche ou déjà passé.")
    if start > latest:
        raise BookingError("Ce créneau est trop éloigné dans le temps.")
    if start.minute % step or start.second or start.microsecond:
        raise BookingError("Horaire invalide.")

    mode = booking_mode_for(service)
    photo_ids = sorted(set(photo_ids or [])) if mode == BookingMode.custom else []  # photos : mode sur mesure seulement
    if len(photo_ids) > MAX_PHOTOS:
        raise BookingError(f"Maximum {MAX_PHOTOS} photos par demande.")
    kinds = _required_kinds(db, service.id)
    day = start.astimezone(salon_tz()).date()
    candidate_ids = [h.id for h in active_hairdressers(db) if hairdresser_id in (None, h.id)]
    if not candidate_ids:
        raise BookingError("Cette coiffeuse n'est pas disponible.")
    if db.in_transaction():
        db.commit()  # les validations ci-dessus sont faites avant le BEGIN : transaction courte

    db.execute(text("SET LOCAL statement_timeout = '5s'"))
    db.execute(text("SET LOCAL lock_timeout = '3s'"))
    for hid in candidate_ids:  # ordre croissant : même ordre de verrouillage partout, pas d'interblocage
        h = db.scalar(select(Hairdresser).where(Hairdresser.id == hid).with_for_update())
        quote = quote_for(db, service, h)
        end = start + timedelta(minutes=quote.duration_min)

        if not any(w0 <= start and end <= w1 for w0, w1 in _windows(db, h.id, day)):
            continue
        if db.scalar(select(Appointment.id).where(Appointment.hairdresser_id == h.id, *_confirmed_overlap(start, end)).limit(1)):
            continue
        resource_ids, ok = [], True
        for kind in kinds:
            free = _free_resource_ids(db, kind, start, end, lock=True)
            if not free:
                ok = False
                break
            resource_ids.append(free[0])
        if not ok:
            continue

        appt = Appointment(
            client_id=client.id,
            hairdresser_id=h.id,
            service_id=service.id,
            booking_mode=mode,
            client_notes=(notes or "").strip()[:2000] or None,
            starts_at=start,
            ends_at=end,
            status=AppointmentStatus.confirmed,
            base_duration_min=quote.base_duration_min,
            coefficient_used=quote.coefficient,
            buffer_min=quote.buffer_min,
            computed_duration_min=quote.duration_min,
            estimated_price=quote.estimated_price,
        )
        appt.resources = [AppointmentResource(resource_id=rid) for rid in resource_ids]
        db.add(appt)
        if photo_ids:  # rattachées dans la même transaction que la réservation
            db.flush()
            attached = db.execute(
                update(BookingPhoto)
                .where(
                    BookingPhoto.id.in_(photo_ids),
                    BookingPhoto.client_id == client.id,
                    BookingPhoto.appointment_id.is_(None),
                )
                .values(appointment_id=appt.id)
            ).rowcount
            if attached != len(photo_ids):
                db.rollback()
                raise BookingError("Une des photos n'est plus disponible. Merci de la renvoyer.")
        db.commit()
        return appt

    db.rollback()
    raise SlotUnavailable("Ce créneau vient d'être réservé. Merci d'en choisir un autre.")


def cancel_booking(db: Session, appt: Appointment, now: datetime | None = None) -> None:
    if appt.status != AppointmentStatus.confirmed:
        raise BookingError("Ce rendez-vous ne peut plus être annulé.")
    if appt.starts_at <= (now or utcnow()):
        raise BookingError("Ce rendez-vous est déjà commencé ou passé.")
    appt.status = AppointmentStatus.cancelled
    db.commit()


def set_final_price(db: Session, appt: Appointment, price: Decimal) -> None:
    """Prix final saisi au salon : clôt la prestation et alimente la finance (PRD 4.A / 4.B)."""
    if appt.status == AppointmentStatus.cancelled:
        raise BookingError("Un rendez-vous annulé n'a pas de prix final.")
    if price < 0:
        raise BookingError("Prix invalide.")
    appt.final_price = price
    appt.status = AppointmentStatus.completed
    record = db.scalar(select(FinancialRecord).where(FinancialRecord.appointment_id == appt.id))
    if record:
        record.amount = price
    else:
        db.add(FinancialRecord(appointment_id=appt.id, amount=price, method="salon"))
    db.commit()
