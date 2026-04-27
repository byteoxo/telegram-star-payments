"""Telegram Stars (XTR) payment bot.

What this script does:
  1. Runs a long-polling bot.
  2. Lets a user run /buy <stars> <title> -- the bot generates an invoice link
     and replies with a button that opens Telegram's native Stars payment sheet.
  3. Auto-approves the mandatory `pre_checkout_query` (you must answer it within
     10 seconds, otherwise Telegram cancels the payment).
  4. Listens for `successful_payment` updates -- this is how you "know" the
     payment is complete -- and stores them in an in-memory ledger.
  5. Optionally notifies an ADMIN_CHAT_ID and supports /refund.

Run:
    uv sync
    cp .env.example .env  # then put your BOT_TOKEN in .env
    uv run python main.py
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

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
logger = logging.getLogger("star-bot")


# ---------------------------------------------------------------------------
# In-memory ledger. Replace with a real DB (Postgres, SQLite, Redis, ...) for
# production -- you need to keep payment history to handle refunds and audits.
# ---------------------------------------------------------------------------

@dataclass
class PaymentRecord:
    payload: str
    user_id: int
    title: str
    stars: int
    telegram_payment_charge_id: str
    provider_payment_charge_id: str
    paid_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# payload -> PaymentRecord
PAID_ORDERS: dict[str, PaymentRecord] = {}
# payload -> {user_id, title, stars} (orders we created, not yet paid)
PENDING_ORDERS: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "👋 Telegram Stars demo bot.\n\n"
        "Commands:\n"
        "  /buy <stars> <title> -- create a Stars invoice link\n"
        "  /status <payload>    -- check whether an order has been paid\n"
        "  /refund <payload>    -- refund a paid order (admin only)\n"
    )


async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/buy 1400 Plan - Basic"""
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            "Usage: /buy <stars> <title>\nExample: /buy 1400 Plan - Basic"
        )
        return

    stars = int(context.args[0])
    title = " ".join(context.args[1:]).strip() or "Untitled"
    description = f"Pay {stars} ⭐ for: {title}"
    payload = f"order_{secrets.token_urlsafe(12)}"

    bot = context.bot
    invoice_url = await bot.create_invoice_link(
        title=title,
        description=description,
        payload=payload,
        provider_token="",     # MUST be empty for Stars
        currency="XTR",        # Telegram Stars
        prices=[LabeledPrice(label=title, amount=stars)],
    )

    PENDING_ORDERS[payload] = {
        "user_id": update.effective_user.id,
        "title": title,
        "stars": stars,
    }

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(text=f"⭐ Pay {stars} Stars", url=invoice_url)]]
    )
    await update.effective_message.reply_text(
        f"Invoice created.\n\n"
        f"Title  : {title}\n"
        f"Price  : {stars} ⭐\n"
        f"Payload: <code>{payload}</code>\n"
        f"URL    : {invoice_url}",
        reply_markup=keyboard,
        parse_mode="HTML",
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
            f"Stars   : {rec.stars}\n"
            f"Charge  : {rec.telegram_payment_charge_id}\n"
            f"Paid at : {rec.paid_at.isoformat(timespec='seconds')}"
        )
    elif payload in PENDING_ORDERS:
        await update.effective_message.reply_text("⏳ PENDING -- invoice sent, no payment yet.")
    else:
        await update.effective_message.reply_text("❓ Unknown payload.")


async def cmd_refund(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refund a Stars payment by its payload. Admin-only by ADMIN_CHAT_ID."""
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

    ok = await context.bot.refund_star_payment(
        user_id=rec.user_id,
        telegram_payment_charge_id=rec.telegram_payment_charge_id,
    )
    await update.effective_message.reply_text("✅ Refunded." if ok else "❌ Refund failed.")


# --- Payment lifecycle ------------------------------------------------------

async def on_pre_checkout(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    """Telegram fires this BEFORE charging the user. We have ~10s to answer.

    For Stars there's nothing extra to validate (Telegram already showed the
    user the price), so we just confirm. If you wanted to deny, call
    answer(ok=False, error_message=...).
    """
    query = update.pre_checkout_query
    payload = query.invoice_payload
    expected = PENDING_ORDERS.get(payload)

    if expected is None:
        await query.answer(ok=False, error_message="Unknown order. Please request a new invoice.")
        logger.warning("Rejected pre_checkout for unknown payload=%s", payload)
        return

    await query.answer(ok=True)
    logger.info("pre_checkout OK payload=%s user=%s", payload, query.from_user.id)


async def on_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fires once Telegram has actually charged the user's Stars wallet."""
    sp = update.message.successful_payment
    user = update.effective_user
    payload = sp.invoice_payload
    pending = PENDING_ORDERS.pop(payload, None)
    title = (pending or {}).get("title", "Unknown")

    record = PaymentRecord(
        payload=payload,
        user_id=user.id,
        title=title,
        stars=sp.total_amount,  # for XTR this is the integer star count
        telegram_payment_charge_id=sp.telegram_payment_charge_id,
        provider_payment_charge_id=sp.provider_payment_charge_id or "",
    )
    PAID_ORDERS[payload] = record

    logger.info(
        "PAID payload=%s user=%s stars=%s charge=%s",
        payload, user.id, sp.total_amount, sp.telegram_payment_charge_id,
    )

    await update.message.reply_text(
        f"✅ Payment received!\n"
        f"Title  : {title}\n"
        f"Stars  : {sp.total_amount} ⭐\n"
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
                    f"💰 New Stars payment\n"
                    f"User    : {user.id} (@{user.username})\n"
                    f"Title   : {title}\n"
                    f"Stars   : {sp.total_amount}\n"
                    f"Payload : {payload}\n"
                    f"Charge  : {sp.telegram_payment_charge_id}"
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
    app.add_handler(CommandHandler("buy", cmd_buy))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("refund", cmd_refund))

    app.add_handler(PreCheckoutQueryHandler(on_pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, on_successful_payment))

    logger.info("Bot started. Waiting for messages and payments...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
