import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.bookings import engine
from app.bookings.engine import BookingError, SlotUnavailable, compute_duration
from app.db.base import SessionLocal
from app.db.models.core import Appointment, AppointmentStatus, BookingMode, FinancialRecord, PricingMode
from app.db.models.settings import set_setting
from tests.conftest import (
    local_start,
    make_client_user,
    make_hairdresser,
    make_resource,
    make_service,
    next_weekday,
)


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s


MONDAY = None


def monday():
    return next_weekday(0)


# --- compute_duration (PRD 4.A) ------------------------------------------------


@pytest.mark.parametrize(
    "base,coeff,buffer,expected",
    [
        (60, "1.00", 0, 60),
        (60, "1.30", 10, 100),  # 78 min -> 90 (pas de 15) + 10
        (45, "0.85", 0, 45),  # 38,25 -> 45
        (90, "1.00", 15, 105),
        (30, "1.00", 0, 30),
        (20, "1.00", 0, 30),  # arrondi au pas supérieur
    ],
)
def test_compute_duration(base, coeff, buffer, expected):
    assert compute_duration(base, Decimal(coeff), buffer, 15) == expected


def test_compute_duration_rejects_invalid():
    for args in ((0, "1", 0), (60, "1", -5)):
        with pytest.raises(ValueError):
            compute_duration(args[0], Decimal(args[1]), args[2])


# --- disponibilités ------------------------------------------------------------------


def test_slots_depend_on_hairdresser_coefficient(db):
    fast = make_hairdresser(db, "Rapide", "1.00")
    slow = make_hairdresser(db, "Lente", "1.50")
    svc = make_service(db, base=60)
    day = monday()
    now = engine.utcnow()
    fast_slots = engine.free_starts(db, svc, fast, day, now)
    slow_slots = engine.free_starts(db, svc, slow, day, now)
    assert fast_slots[0] == local_start(day, 9) and fast_slots[-1] == local_start(day, 16)
    # 60 min x 1,5 = 90 min : le dernier début possible est 15 h 30
    assert slow_slots[-1] == local_start(day, 15, 30)
    assert engine.quote_for(db, svc, slow).duration_min == 90


def test_no_slot_outside_availability_days(db):
    h = make_hairdresser(db, "Marie", slots=((0, 540, 1020),))  # lundi seulement
    svc = make_service(db)
    tuesday = monday() + timedelta(days=1)
    assert engine.free_starts(db, svc, h, tuesday, engine.utcnow()) == []


def test_buffer_is_part_of_the_blocked_time(db):
    h = make_hairdresser(db, "Marie", slots=((0, 9 * 60, 11 * 60),))
    svc = make_service(db, base=60, buffer=30)  # 90 min au total
    day = monday()
    starts = engine.free_starts(db, svc, h, day, engine.utcnow())
    assert starts[-1] == local_start(day, 9, 30)  # 9 h 30 + 90 min = 11 h pile


def test_any_hairdresser_assigns_first_available(db):
    first = make_hairdresser(db, "Alice")
    second = make_hairdresser(db, "Bea")
    svc = make_service(db)
    client = make_client_user(db)
    day = monday()
    t = local_start(day, 10)
    a1 = engine.create_booking(db, client=client, service=svc, hairdresser_id=None, start=t)
    a2 = engine.create_booking(db, client=client, service=svc, hairdresser_id=None, start=t)
    assert a1.hairdresser_id == first.id and a2.hairdresser_id == second.id
    with pytest.raises(SlotUnavailable):
        engine.create_booking(db, client=client, service=svc, hairdresser_id=None, start=t)
    # le créneau n'est plus proposé du tout
    assert t not in [s for s, _ in engine.slots_for(db, svc, None, day)]


# --- réservation ----------------------------------------------------------------------


def test_booking_memorizes_calculation_and_price_catalog_mode(db):
    h = make_hairdresser(db, "Marie", "1.30")
    svc = make_service(db, base=60, buffer=10, price="80.00")
    client = make_client_user(db)
    start = local_start(monday(), 9)
    a = engine.create_booking(db, client=client, service=svc, hairdresser_id=h.id, start=start)
    assert a.booking_mode == BookingMode.catalog
    assert (a.base_duration_min, a.coefficient_used, a.buffer_min, a.computed_duration_min) == (60, Decimal("1.30"), 10, 100)
    assert a.ends_at - a.starts_at == timedelta(minutes=100)
    assert a.estimated_price == Decimal("80.00") and a.final_price is None


def test_custom_mode_has_no_estimated_price(db):
    h = make_hairdresser(db, "Marie")
    svc = make_service(db, name="TEST Sur mesure", base=120, mode=PricingMode.quote)
    a = engine.create_booking(db, client=make_client_user(db), service=svc, hairdresser_id=h.id,
                              start=local_start(monday(), 9), notes="  Tresses africaines, longueur taille  ")  # fmt: skip
    assert a.booking_mode == BookingMode.custom and a.estimated_price is None
    assert a.client_notes == "Tresses africaines, longueur taille"


def test_changing_coefficient_later_does_not_alter_existing_booking(db):
    h = make_hairdresser(db, "Marie", "1.00")
    svc = make_service(db)
    a = engine.create_booking(db, client=make_client_user(db), service=svc, hairdresser_id=h.id, start=local_start(monday(), 9))
    h.duration_coefficient = Decimal("2.00")
    db.commit()
    db.refresh(a)
    assert a.coefficient_used == Decimal("1.00") and a.computed_duration_min == 60


def test_overlap_rejected_but_back_to_back_allowed(db):
    h = make_hairdresser(db, "Marie")
    svc = make_service(db, base=60)
    client = make_client_user(db)
    day = monday()
    engine.create_booking(db, client=client, service=svc, hairdresser_id=h.id, start=local_start(day, 10))
    for bad in (local_start(day, 9, 30), local_start(day, 10), local_start(day, 10, 45)):
        with pytest.raises(SlotUnavailable):
            engine.create_booking(db, client=client, service=svc, hairdresser_id=h.id, start=bad)
    engine.create_booking(db, client=client, service=svc, hairdresser_id=h.id, start=local_start(day, 11))  # collé après
    engine.create_booking(db, client=client, service=svc, hairdresser_id=h.id, start=local_start(day, 9))  # collé avant


def test_must_fit_inside_availability_window(db):
    h = make_hairdresser(db, "Marie", slots=((0, 9 * 60, 17 * 60),))
    svc = make_service(db, base=60)
    client = make_client_user(db)
    day = monday()
    for bad in (local_start(day, 8), local_start(day, 16, 30)):  # avant l'ouverture / déborde après 17 h
        with pytest.raises(SlotUnavailable):
            engine.create_booking(db, client=client, service=svc, hairdresser_id=h.id, start=bad)


def test_time_bounds_and_alignment(db):
    h = make_hairdresser(db, "Marie", slots=tuple((d, 540, 1020) for d in range(7)))
    svc = make_service(db)
    client = make_client_user(db)
    now = engine.utcnow()
    with pytest.raises(SlotUnavailable):  # dans le passé
        engine.create_booking(db, client=client, service=svc, hairdresser_id=h.id, start=now - timedelta(days=1), now=now)
    with pytest.raises(SlotUnavailable):  # sous le délai minimal
        engine.create_booking(db, client=client, service=svc, hairdresser_id=h.id, start=now + timedelta(minutes=10), now=now)
    with pytest.raises(BookingError):  # au-delà de l'horizon (60 j)
        engine.create_booking(db, client=client, service=svc, hairdresser_id=h.id, start=now + timedelta(days=90), now=now)
    with pytest.raises(BookingError):  # pas aligné sur le pas du calendrier
        engine.create_booking(db, client=client, service=svc, hairdresser_id=h.id, start=local_start(monday(), 9, 7))


def test_inactive_service_or_hairdresser_rejected(db):
    h = make_hairdresser(db, "Marie")
    svc = make_service(db)
    client = make_client_user(db)
    svc.is_active = False
    db.commit()
    with pytest.raises(BookingError):
        engine.create_booking(db, client=client, service=svc, hairdresser_id=h.id, start=local_start(monday(), 9))
    svc.is_active, h.is_active = True, False
    db.commit()
    with pytest.raises(BookingError):
        engine.create_booking(db, client=client, service=svc, hairdresser_id=h.id, start=local_start(monday(), 9))


# --- équipements ----------------------------------------------------------------------


def test_single_basin_blocks_second_hairdresser_same_time(db):
    a = make_hairdresser(db, "Alice")
    b = make_hairdresser(db, "Bea")
    make_resource(db, "wash_basin", "TEST Bac 1")
    svc = make_service(db, kinds=("wash_basin",))
    client = make_client_user(db)
    day = monday()
    t = local_start(day, 10)
    engine.create_booking(db, client=client, service=svc, hairdresser_id=a.id, start=t)
    with pytest.raises(SlotUnavailable):  # Bea est libre mais le seul bac est pris
        engine.create_booking(db, client=client, service=svc, hairdresser_id=b.id, start=t)
    assert t not in [s for s, _ in engine.slots_for(db, svc, b.id, day)]
    engine.create_booking(db, client=client, service=svc, hairdresser_id=b.id, start=local_start(day, 11))  # autre heure : ok


def test_two_basins_allow_two_simultaneous_bookings(db):
    a = make_hairdresser(db, "Alice")
    b = make_hairdresser(db, "Bea")
    make_resource(db, "wash_basin", "TEST Bac 1")
    make_resource(db, "wash_basin", "TEST Bac 2")
    svc = make_service(db, kinds=("wash_basin",))
    client = make_client_user(db)
    t = local_start(monday(), 10)
    x = engine.create_booking(db, client=client, service=svc, hairdresser_id=a.id, start=t)
    y = engine.create_booking(db, client=client, service=svc, hairdresser_id=b.id, start=t)
    assert {r.resource_id for r in x.resources}.isdisjoint({r.resource_id for r in y.resources})


# --- annulation et prix final -------------------------------------------------------


def test_cancel_frees_the_slot_and_past_cannot_be_cancelled(db):
    h = make_hairdresser(db, "Marie")
    svc = make_service(db)
    client = make_client_user(db)
    t = local_start(monday(), 10)
    a = engine.create_booking(db, client=client, service=svc, hairdresser_id=h.id, start=t)
    engine.cancel_booking(db, a)
    assert a.status == AppointmentStatus.cancelled
    engine.create_booking(db, client=client, service=svc, hairdresser_id=h.id, start=t)  # re-réservable
    with pytest.raises(BookingError):
        engine.cancel_booking(db, a)  # déjà annulé
    other = engine.create_booking(db, client=client, service=svc, hairdresser_id=h.id, start=local_start(monday(), 14))
    with pytest.raises(BookingError):
        engine.cancel_booking(db, other, now=other.starts_at + timedelta(minutes=5))


def test_final_price_completes_and_feeds_finance(db):
    h = make_hairdresser(db, "Marie")
    svc = make_service(db, price="50.00")
    a = engine.create_booking(db, client=make_client_user(db), service=svc, hairdresser_id=h.id, start=local_start(monday(), 9))
    engine.set_final_price(db, a, Decimal("65.00"))
    assert a.status == AppointmentStatus.completed and a.final_price == Decimal("65.00")
    assert a.estimated_price == Decimal("50.00")  # l'écart reste visible
    rec = db.scalar(select(FinancialRecord).where(FinancialRecord.appointment_id == a.id))
    assert rec.amount == Decimal("65.00")
    engine.set_final_price(db, a, Decimal("70.00"))  # correction : un seul enregistrement financier
    assert len(db.scalars(select(FinancialRecord).where(FinancialRecord.appointment_id == a.id)).all()) == 1
    with pytest.raises(BookingError):
        engine.set_final_price(db, a, Decimal("-1"))


def test_cancelled_booking_has_no_final_price(db):
    h = make_hairdresser(db, "Marie")
    svc = make_service(db)
    a = engine.create_booking(db, client=make_client_user(db), service=svc, hairdresser_id=h.id, start=local_start(monday(), 9))
    engine.cancel_booking(db, a)
    with pytest.raises(BookingError):
        engine.set_final_price(db, a, Decimal("10"))


# --- concurrence --------------------------------------------------------------------------


def test_concurrent_bookings_only_one_wins(db):
    h = make_hairdresser(db, "Marie")
    svc = make_service(db)
    clients = [make_client_user(db, f"c{i}@test.kyja") for i in range(6)]
    t = local_start(monday(), 10)
    results, barrier = [], threading.Barrier(len(clients))

    def attempt(client):
        with SessionLocal() as s:
            barrier.wait()
            try:
                engine.create_booking(s, client=client, service=s.get(type(svc), svc.id), hairdresser_id=h.id, start=t)
                results.append("ok")
            except SlotUnavailable:
                results.append("conflit")

    threads = [threading.Thread(target=attempt, args=(c,)) for c in clients]
    [th.start() for th in threads]
    [th.join(timeout=30) for th in threads]
    assert sorted(results) == ["conflit"] * 5 + ["ok"]
    assert len(db.scalars(select(Appointment).where(Appointment.hairdresser_id == h.id)).all()) == 1


def test_concurrent_resource_contention_single_basin(db):
    a = make_hairdresser(db, "Alice")
    b = make_hairdresser(db, "Bea")
    make_resource(db, "wash_basin", "TEST Bac 1")
    svc = make_service(db, kinds=("wash_basin",))
    clients = [make_client_user(db, f"r{i}@test.kyja") for i in range(4)]
    t = local_start(monday(), 10)
    results, barrier = [], threading.Barrier(len(clients))

    def attempt(client, hid):
        with SessionLocal() as s:
            barrier.wait()
            try:
                engine.create_booking(s, client=client, service=s.get(type(svc), svc.id), hairdresser_id=hid, start=t)
                results.append("ok")
            except SlotUnavailable:
                results.append("conflit")

    threads = [threading.Thread(target=attempt, args=(c, (a.id, b.id)[i % 2])) for i, c in enumerate(clients)]
    [th.start() for th in threads]
    [th.join(timeout=30) for th in threads]
    assert results.count("ok") == 1  # un seul bac : une seule réservation, même avec deux coiffeuses libres


def test_calendar_step_setting_changes_slot_grid(db):
    h = make_hairdresser(db, "Marie", slots=((0, 540, 660),))
    svc = make_service(db, base=30)
    set_setting(db, "calendar_step_min", "30")
    starts = engine.free_starts(db, svc, h, monday(), engine.utcnow())
    assert [s.minute for s in starts] == [0, 30, 0, 30][: len(starts)] and len(starts) == 4
