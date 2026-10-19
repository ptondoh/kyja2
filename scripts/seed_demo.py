"""Données de démonstration pour voir l'application fonctionner en local.

Usage :
    python -m scripts.seed_demo            crée 2 coiffeuses (avec horaires), 2 bacs de lavage, 5 prestations, 2 produits
    python -m scripts.seed_demo --remove   retire uniquement ce que ce script a créé

Les comptes de démonstration utilisent l'adresse @demo.kyja ; leurs mots de passe sont générés
au hasard et affichés une seule fois (ce sont des comptes de coiffeuse, pas d'administrateur).
Ne PAS utiliser en production.
"""
import secrets
import sys
from decimal import Decimal

from sqlalchemy import delete, select

from app.auth.security import hash_password
from app.db.base import SessionLocal
from app.db.models.core import (
    Appointment,
    AppointmentResource,
    AvailabilitySlot,
    BookingPhoto,
    FinancialRecord,
    Hairdresser,
    Media,
    PricingMode,
    Product,
    Resource,
    Role,
    Service,
    ServiceResource,
    User,
)

DOMAIN = "@demo.kyja"
HAIRDRESSERS = [
    # (nom, email, coefficient, plages (jour 0 = lundi, début, fin en minutes))
    ("Marie Tremblay", f"marie{DOMAIN}", "1.30", [(d, 540, 1020) for d in range(0, 5)] + [(5, 540, 900)]),
    ("Sonia Gagnon", f"sonia{DOMAIN}", "1.00", [(d, 600, 1080) for d in (1, 2, 3, 4, 5)]),
]
RESOURCES = [("wash_basin", "Bac de lavage 1 (démo)"), ("wash_basin", "Bac de lavage 2 (démo)")]
SERVICES = [
    # (nom, catégorie, durée de base, repos, prix estimatif, équipements requis)
    ("Brushing", "Coiffure", 45, 10, "45.00", ["wash_basin"]),
    ("Coupe femme", "Coiffure", 60, 10, "65.00", ["wash_basin"]),
    ("Coloration complète", "Coloration", 120, 15, "120.00", ["wash_basin"]),
    ("Tresses collées", "Coiffure", 180, 15, "95.00", []),
    ("Soin profond", "Soins", 30, 5, "35.00", ["wash_basin"]),
]
PRODUCTS = [("Huile capillaire nourrissante", "34.50", 12), ("Shampoing hydratant", "22.00", 20)]


def create() -> None:
    with SessionLocal() as db:
        out = []
        for name, email, coeff, slots in HAIRDRESSERS:
            if db.scalar(select(User.id).where(User.email == email)):
                continue
            password = secrets.token_urlsafe(12)
            user = User(email=email, password_hash=hash_password(password), role=Role.hairdresser, full_name=name)
            db.add(user)
            db.flush()
            h = Hairdresser(user_id=user.id, duration_coefficient=Decimal(coeff))
            db.add(h)
            db.flush()
            db.add_all(AvailabilitySlot(hairdresser_id=h.id, weekday=d, start_minute=a, end_minute=b) for d, a, b in slots)
            out.append((email, password))
        for kind, name in RESOURCES:
            if not db.scalar(select(Resource.id).where(Resource.name == name)):
                db.add(Resource(kind=kind, name=name))
        for name, cat, base, buffer, price, kinds in SERVICES:
            if db.scalar(select(Service.id).where(Service.name == name)):
                continue
            svc = Service(
                name=name, category=cat, base_duration_min=base, buffer_min=buffer,
                pricing_mode=PricingMode.estimate, price=Decimal(price),
                description="Prestation de démonstration.",
            )  # fmt: skip
            db.add(svc)
            db.flush()
            db.add_all(ServiceResource(service_id=svc.id, resource_kind=k) for k in kinds)
        for name, price, stock in PRODUCTS:
            if not db.scalar(select(Product.id).where(Product.name == name)):
                db.add(Product(name=name, price=Decimal(price), stock=stock, description="Produit de démonstration."))
        db.commit()
    print("Données de démonstration créées.")
    for email, password in out:
        print(f"  coiffeuse {email} — mot de passe : {password}")
    print("Ajoutez des photos aux prestations depuis /admin/services. Retrait : python -m scripts.seed_demo --remove")


def remove() -> None:
    with SessionLocal() as db:
        uids = db.scalars(select(User.id).where(User.email.like(f"%{DOMAIN}"))).all()
        hids = db.scalars(select(Hairdresser.id).where(Hairdresser.user_id.in_(uids))).all() if uids else []
        sids = db.scalars(select(Service.id).where(Service.name.in_([s[0] for s in SERVICES]), Service.is_custom_request.is_(False))).all()
        appts = db.scalars(select(Appointment.id).where(Appointment.hairdresser_id.in_(hids))).all() if hids else []
        if appts:
            db.execute(delete(FinancialRecord).where(FinancialRecord.appointment_id.in_(appts)))
            db.execute(delete(AppointmentResource).where(AppointmentResource.appointment_id.in_(appts)))
            db.execute(delete(BookingPhoto).where(BookingPhoto.appointment_id.in_(appts)))
            db.execute(delete(Appointment).where(Appointment.id.in_(appts)))
        if hids:
            db.execute(delete(AvailabilitySlot).where(AvailabilitySlot.hairdresser_id.in_(hids)))
            db.execute(delete(Hairdresser).where(Hairdresser.id.in_(hids)))
        if sids:
            db.execute(delete(Media).where(Media.service_id.in_(sids)))
            db.execute(delete(ServiceResource).where(ServiceResource.service_id.in_(sids)))
            db.execute(delete(Service).where(Service.id.in_(sids)))
        db.execute(delete(Resource).where(Resource.name.in_([r[1] for r in RESOURCES])))
        db.execute(delete(Product).where(Product.name.in_([p[0] for p in PRODUCTS])))
        if uids:
            db.execute(delete(User).where(User.id.in_(uids)))
        db.commit()
    print("Données de démonstration retirées.")


if __name__ == "__main__":
    remove() if "--remove" in sys.argv else create()
