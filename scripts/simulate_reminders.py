"""Essai en direct des rappels de rendez-vous, sans attendre la veille.

    python -m scripts.simulate_reminders                      # liste les rendez-vous confirmés à venir
    python -m scripts.simulate_reminders 12 --avant 20h       # fait « comme s'il était 20 h avant le rendez-vous n° 12 »
    python -m scripts.simulate_reminders 12 --avant 1h50      # rappel court (2 h avant)
    python -m scripts.simulate_reminders 12 --reset           # remet les rappels du n° 12 à zéro pour recommencer

Les rappels sont créés pour de vrai (alerte du site + courriel/SMS en « aperçu » visible dans Admin → Messages envoyés).
Seule l'heure de référence est simulée. Sur la base de développement uniquement.
"""
import argparse
import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.bookings import engine
from app.db.base import SessionLocal
from app.db.models.core import Appointment, AppointmentStatus
from app.notifications import reminders
from app.templating import fr_date, fr_time


def parse_delay(text: str) -> timedelta:
    """« 20h », « 1h50 », « 90m » -> durée."""
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m?)?", text.strip().lower())
    if not m or not (m.group(1) or m.group(2)):
        raise SystemExit("Durée invalide : écrivez par exemple 20h, 1h50 ou 90m.")
    return timedelta(hours=int(m.group(1) or 0), minutes=int(m.group(2) or 0))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("rendez_vous", nargs="?", type=int, help="numéro du rendez-vous")
    parser.add_argument("--avant", default="20h", help="temps avant le rendez-vous à simuler (défaut : 20h)")
    parser.add_argument("--reset", action="store_true", help="remet les rappels de ce rendez-vous à zéro")
    args = parser.parse_args()

    with SessionLocal() as db:
        if args.rendez_vous is None:
            rows = db.scalars(
                select(Appointment).options(selectinload(Appointment.client), selectinload(Appointment.service))
                .where(Appointment.status == AppointmentStatus.confirmed, Appointment.starts_at > engine.utcnow())
                .order_by(Appointment.starts_at)
            ).all()  # fmt: skip
            print("Rendez-vous confirmés à venir :")
            for a in rows:
                flags = ("veille envoyé " if a.reminder_day_sent_at else "") + ("2h envoyé" if a.reminder_hours_sent_at else "")
                print(f"  n° {a.id:<5} {fr_date(a.starts_at)} {fr_time(a.starts_at)}  {a.client.full_name} · {a.service.name}  [{flags.strip() or 'aucun rappel'}]")
            if not rows:
                print("  (aucun)")
            return
        appt = db.get(Appointment, args.rendez_vous)
        if not appt:
            raise SystemExit(f"Rendez-vous n° {args.rendez_vous} introuvable.")
        if args.reset:
            appt.reminder_day_sent_at = appt.reminder_hours_sent_at = None
            db.commit()
            print("Rappels remis à zéro.")
            return
        now = appt.starts_at - parse_delay(args.avant)
        print(f"Heure simulée : {fr_date(now)} {fr_time(now)} ({args.avant} avant le rendez-vous n° {appt.id}).")
        print("Résultat :", reminders.send_due_reminders(db, now, only_appointment_id=appt.id))
        print("Voir : la cloche de la cliente, et Admin → Messages envoyés.")


if __name__ == "__main__":
    main()
