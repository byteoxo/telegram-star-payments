"""Telegram payments bot supporting both Stars (XTR) and real currencies (USD, EUR, ...).

What this script does:
  1. Runs a long-polling bot.
  2. /buy_stars <stars> <title>  -- creates a Stars invoice link
     /buy_fiat <currency> <amount> <title>  -- creates a Stripe/etc invoice
  3. Auto-approves the mandatory `pre_checkout_query` (you must answer it within
     10 seconds, otherwise Telegram cancels the payment).
  4. Listens for `successful_payment` updates -- this is the "payment complete"
     signal -- and stores them in an in-memory ledger.
  5. Optionally notifies an ADMIN_CHAT_ID and supports /refund (Stars only;
     fiat refunds happen in your provider's dashboard, e.g. Stripe).

Run:
    uv sync
    cp .env.example .env  # set BOT_TOKEN and (for fiat) PROVIDER_TOKEN
    uv run python main.py
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Update,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("payments-bot")


# Currency precision tables -- see https://core.telegram.org/bots/payments/currencies.json
_ZERO_DECIMAL_CURRENCIES = {
    "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW",
    "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
}
_THREE_DECIMAL_CURRENCIES = {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"}


def _decimals_for(currency: str) -> int:
    c = currency.upper()
    if c in _ZERO_DECIMAL_CURRENCIES:
        return 0
    if c in _THREE_DECIMAL_CURRENCIES:
        return 3
    return 2


def _to_minor_units(amount: str | float | Decimal, currency: str) -> int:
    """Convert a human price (9.99 USD, 1000 JPY) to Telegram's integer minor units."""
    decimals = _decimals_for(currency)
    return int((Decimal(str(amount)) * (Decimal(10) ** decimals)).to_integral_value())


def _format_money(minor_amount: int, currency: str) -> str:
    """Render Telegram's integer minor amount back as a human price (e.g. 999 USD -> 9.99 USD)."""
    decimals = _decimals_for(currency)
    if decimals == 0:
        return f"{minor_amount} {currency.upper()}"
    value = Decimal(minor_amount) / (Decimal(10) ** decimals)
    return f"{value:.{decimals}f} {currency.upper()}"


# ---------------------------------------------------------------------------
# In-memory ledger. Replace with a real DB (Postgres, SQLite, Redis, ...) for
# production -- you need to keep payment history to handle refunds and audits.
# ---------------------------------------------------------------------------

@dataclass
class PaymentRecord:
    payload: str
    user_id: int
    title: str
    currency: str  # "XTR" for Stars, ISO 4217 otherwise
    total_amount: int  # integer minor units (or star count if currency=XTR)
    telegram_payment_charge_id: str
    provider_payment_charge_id: str
    paid_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_stars(self) -> bool:
        return self.currency.upper() == "XTR"

    def display_amount(self) -> str:
        return f"{self.total_amount} ⭐" if self.is_stars else _format_money(self.total_amount, self.currency)


PAID_ORDERS: dict[str, PaymentRecord] = {}
PENDING_ORDERS: dict[str, dict] = {}  # payload -> {user_id, title, currency, total_amount}


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "👋 Telegram payments demo bot.\n\n"
        "Commands:\n"
        "  /buy_stars <stars> <title>           -- create a Stars (XTR) invoice\n"
        "  /buy_fiat <ccy> <amount> <title>     -- create a fiat (USD/EUR/...) invoice\n"
        "  /status <payload>                    -- check whether an order has been paid\n"
        "  /refund <payload>                    -- refund a Stars order (admin only)\n"
        "\nExamples:\n"
        "  /buy_stars 1400 Plan - Basic\n"
        "  /buy_fiat USD 9.99 Plan - Basic"
    )


async def _create_and_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    title: str,
    description: str,
    currency: str,
    minor_amount: int,
    provider_token: str,
    button_text: str,
    display_price: str,
) -> None:
    payload = f"order_{secrets.token_urlsafe(12)}"
    invoice_url = await context.bot.create_invoice_link(
        title=title,
        description=description,
        payload=payload,
        provider_token=provider_token,
        currency=currency,
        prices=[LabeledPrice(label=title, amount=minor_amount)],
    )

    PENDING_ORDERS[payload] = {
        "user_id": update.effective_user.id,
        "title": title,
        "currency": currency,
        "total_amount": minor_amount,
    }

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(text=button_text, url=invoice_url)]])
    await update.effective_message.reply_text(
        f"Invoice created.\n\n"
        f"Title   : {title}\n"
        f"Price   : {display_price}\n"
        f"Payload : <code>{payload}</code>\n"
        f"URL     : {invoice_url}",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


async def cmd_buy_stars(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/buy_stars 1400 Plan - Basic"""
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            "Usage: /buy_stars <stars> <title>\nExample: /buy_stars 1400 Plan - Basic"
        )
        return

    stars = int(context.args[0])
    title = " ".join(context.args[1:]).strip() or "Untitled"
    await _create_and_reply(
        update, context,
        title=title,
        description=f"Pay {stars} ⭐ for: {title}",
        currency="XTR",
        minor_amount=stars,
        provider_token="",  # MUST be empty for Stars
        button_text=f"⭐ Pay {stars} Stars",
        display_price=f"{stars} ⭐",
    )


async def cmd_buy_fiat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/buy_fiat USD 9.99 Plan - Basic"""
    if len(context.args) < 3:
        await update.effective_message.reply_text(
            "Usage: /buy_fiat <currency> <amount> <title>\n"
            "Example: /buy_fiat USD 9.99 Plan - Basic"
        )
        return

    currency = context.args[0].upper()
    amount_raw = context.args[1]
    title = " ".join(context.args[2:]).strip() or "Untitled"

    provider_token = os.environ.get("PROVIDER_TOKEN")
    if not provider_token:
        await update.effective_message.reply_text(
            "PROVIDER_TOKEN is not configured. Connect a payment provider in BotFather "
            "(My Bots → <bot> → Payments) and put the token into .env."
        )
        return

    try:
        minor_amount = _to_minor_units(amount_raw, currency)
    except Exception:  # noqa: BLE001
        await update.effective_message.reply_text(f"Invalid amount: {amount_raw!r}")
        return
    if minor_amount < 1:
        await update.effective_message.reply_text("Amount must be greater than zero.")
        return

    display_price = _format_money(minor_amount, currency)
    await _create_and_reply(
        update, context,
        title=title,
        description=f"Pay {display_price} for: {title}",
        currency=currency,
        minor_amount=minor_amount,
        provider_token=provider_token,
        button_text=f"💳 Pay {display_price}",
        display_price=display_price,
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Usage: /status <payload>")
        return
    payload = context.args[0]
    if payload in PAID_ORDERS:
        rec = PAID_ORDERS[payload]
        await update.effective_message.reply_text(
            f"✅ PAID\n"
            f"Payload : {rec.payload}\n"
            f"Title   : {rec.title}\n"
            f"Amount  : {rec.display_amount()}\n"
            f"Charge  : {rec.telegram_payment_charge_id}\n"
            f"Paid at : {rec.paid_at.isoformat(timespec='seconds')}"
        )
    elif payload in PENDING_ORDERS:
        await update.effective_message.reply_text("⏳ PENDING -- invoice sent, no payment yet.")
    else:
        await update.effective_message.reply_text("❓ Unknown payload.")


async def cmd_refund(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refund a Stars payment by payload. Fiat refunds happen in the provider dashboard."""
    admin_id = os.environ.get("ADMIN_CHAT_ID")
    if admin_id and str(update.effective_user.id) != admin_id:
        await update.effective_message.reply_text("⛔ Not authorized.")
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /refund <payload>")
        return

    payload = context.args[0]
    rec = PAID_ORDERS.get(payload)
    if not rec:
        await update.effective_message.reply_text("Payload not found among paid orders.")
        return
    if not rec.is_stars:
        await update.effective_message.reply_text(
            "Refunds for fiat payments are not done via Bot API. "
            f"Refund this charge in your payment provider's dashboard "
            f"(provider charge id: {rec.provider_payment_charge_id})."
        )
        return

    ok = await context.bot.refund_star_payment(
        user_id=rec.user_id,
        telegram_payment_charge_id=rec.telegram_payment_charge_id,
    )
    await update.effective_message.reply_text("✅ Refunded." if ok else "❌ Refund failed.")


# ---------------------------------------------------------------------------
# Payment lifecycle (works the same for Stars and fiat)
# ---------------------------------------------------------------------------

async def on_pre_checkout(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Telegram fires this BEFORE charging the user. We have ~10s to answer."""
    query = update.pre_checkout_query
    payload = query.invoice_payload
    expected = PENDING_ORDERS.get(payload)

    if expected is None:
        await query.answer(ok=False, error_message="Unknown order. Please request a new invoice.")
        logger.warning("Rejected pre_checkout for unknown payload=%s", payload)
        return

    if query.currency != expected["currency"] or query.total_amount != expected["total_amount"]:
        await query.answer(ok=False, error_message="Order details mismatch. Please retry.")
        logger.warning(
            "pre_checkout mismatch payload=%s expected=%s/%s got=%s/%s",
            payload, expected["currency"], expected["total_amount"],
            query.currency, query.total_amount,
        )
        return

    await query.answer(ok=True)
    logger.info(
        "pre_checkout OK payload=%s user=%s %s/%s",
        payload, query.from_user.id, query.currency, query.total_amount,
    )


async def on_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fires once Telegram (or the provider) has actually charged the user."""
    sp = update.message.successful_payment
    user = update.effective_user
    payload = sp.invoice_payload
    pending = PENDING_ORDERS.pop(payload, None)
    title = (pending or {}).get("title", "Unknown")

    record = PaymentRecord(
        payload=payload,
        user_id=user.id,
        title=title,
        currency=sp.currency,
        total_amount=sp.total_amount,
        telegram_payment_charge_id=sp.telegram_payment_charge_id,
        provider_payment_charge_id=sp.provider_payment_charge_id or "",
    )
    PAID_ORDERS[payload] = record

    logger.info(
        "PAID payload=%s user=%s %s charge=%s",
        payload, user.id, record.display_amount(), sp.telegram_payment_charge_id,
    )

    await update.message.reply_text(
        f"✅ Payment received!\n"
        f"Title  : {title}\n"
        f"Amount : {record.display_amount()}\n"
        f"Charge : <code>{sp.telegram_payment_charge_id}</code>\n\n"
        f"Thank you! 🎉",
        parse_mode="HTML",
    )

    admin_id = os.environ.get("ADMIN_CHAT_ID")
    if admin_id:
        try:
            await context.bot.send_message(
                chat_id=int(admin_id),
                text=(
                    f"💰 New payment\n"
                    f"User    : {user.id} (@{user.username})\n"
                    f"Title   : {title}\n"
                    f"Amount  : {record.display_amount()}\n"
                    f"Payload : {payload}\n"
                    f"Telegram: {sp.telegram_payment_charge_id}\n"
                    f"Provider: {sp.provider_payment_charge_id or '-'}"
                ),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to notify admin: %s", e)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    load_dotenv()
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("buy_stars", cmd_buy_stars))
    app.add_handler(CommandHandler("buy_fiat", cmd_buy_fiat))
    app.add_handler(CommandHandler("buy", cmd_buy_stars))  # backward-compat alias
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("refund", cmd_refund))

    app.add_handler(PreCheckoutQueryHandler(on_pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, on_successful_payment))

    logger.info("Bot started. Waiting for messages and payments...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
