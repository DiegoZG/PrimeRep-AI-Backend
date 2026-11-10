"""Conservative application admission accounting; never a provider invoice claim."""
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import engine
from app.core.feature_flag_service import flag_environment
from app.core.settings import settings
from app.models.equipment_scan import AIRequestReservation, AISpendPeriod


class AIBudgetUnavailable(Exception):
    pass


@dataclass(frozen=True)
class AIReservation:
    id: str
    environment: str
    model: str
    maximum_micro_usd: int


def _money(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount > 1_000_000:
            raise ValueError
        return int((amount * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
    except (InvalidOperation, ValueError, OverflowError):
        raise AIBudgetUnavailable("AI application budget is unavailable") from None


def price_micro_usd(model, input_tokens, output_tokens):
    if model != "gpt-6-luna" or not all(type(value) is int and 0 <= value <= 1_050_000 for value in (input_tokens, output_tokens)):
        raise AIBudgetUnavailable("AI pricing configuration is unavailable")
    long = input_tokens > 272_000
    # Full uncached input plus cache-write headroom; output already includes reasoning.
    amount = Decimal(input_tokens) * Decimal("0.10") * (2 if long else 1) * Decimal("1.25")
    amount += Decimal(output_tokens) * Decimal("0.50") * (Decimal("1.5") if long else 1)
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


def budget_lock(db, environment):
    key = int.from_bytes(hashlib.sha256(("primerep-ai-budget:" + environment).encode()).digest()[:8], "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def _periods(now):
    return ("lifetime", now.strftime("%Y-%m"), now.strftime("%Y-%m-%d"))


def reserve(purpose, model, max_input_tokens, max_output_tokens, *, bind=None, now=None, db=None):
    now = now or datetime.now(timezone.utc)
    environment = flag_environment()
    ceiling = price_micro_usd(model, max_input_tokens, max_output_tokens)
    caps = tuple(_money(value) for value in (settings.OPENAI_TEST_BUDGET_USD, settings.OPENAI_MONTHLY_BUDGET_USD, settings.OPENAI_DAILY_BUDGET_USD))
    if purpose not in {"exercise_qa", "coach_week", "coach_day", "coach_feed", "equipment_scan", "draft_content"}:
        raise AIBudgetUnavailable("AI purpose is unavailable")

    def write(session):
        budget_lock(session, environment)
        rows = []
        for period, cap in zip(_periods(now), caps):
            row = session.get(AISpendPeriod, (environment, period))
            if row is None:
                row = AISpendPeriod(environment=environment, period=period, committed_micro_usd=0)
                session.add(row)
            if row.committed_micro_usd + ceiling > cap:
                raise AIBudgetUnavailable("AI application budget reached")
            rows.append(row)
        for row in rows:
            row.committed_micro_usd += ceiling
        identifier = str(uuid.uuid4())
        session.add(AIRequestReservation(id=identifier, environment=environment, purpose=purpose, model=model, status="reserved", reserved_micro_usd=ceiling, created_at=now))
        return AIReservation(identifier, environment, model, ceiling)

    try:
        if db is not None:
            return write(db)
        with Session(bind=bind or engine) as session:
            with session.begin():
                result = write(session)
        return result
    except AIBudgetUnavailable:
        raise
    except Exception:
        raise AIBudgetUnavailable("AI application budget is unavailable") from None


def settle(reservation, usage=None, *, known_zero=False, bind=None, now=None):
    """Unknown usage retains the full reservation; settlement is idempotent."""
    now = now or datetime.now(timezone.utc)
    try:
        with Session(bind=bind or engine) as db:
            with db.begin():
                budget_lock(db, reservation.environment)
                row = db.get(AIRequestReservation, reservation.id)
                if row is None or row.status == "settled":
                    return
                if known_zero:
                    tokens = (0, 0, 0)
                elif usage is not None:
                    tokens = (getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None), getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0))
                    if not all(type(value) is int and 0 <= value <= 1_050_000 for value in tokens):
                        tokens = None
                else:
                    tokens = None
                if tokens is None:
                    row.status = "unknown"
                    return
                accounted = price_micro_usd(row.model, tokens[0], tokens[1])
                for period in _periods(row.created_at):
                    ledger = db.get(AISpendPeriod, (row.environment, period))
                    if ledger is None:
                        raise AIBudgetUnavailable("AI reservation ledger is unavailable")
                    ledger.committed_micro_usd = max(0, ledger.committed_micro_usd - row.reserved_micro_usd + accounted)
                row.status = "settled"
                row.accounted_micro_usd = accounted
                row.input_tokens, row.output_tokens, row.reasoning_tokens = tokens
                row.settled_at = now
    except Exception:
        # Keep a previously admitted conservative charge when settlement is unavailable.
        return
