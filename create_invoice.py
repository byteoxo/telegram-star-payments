"""Create a Telegram invoice link, payable in Telegram Stars (XTR) or in
real-world currencies (USD, EUR, ...) via an external payment provider
(Stripe / Smart Glocal / Tranzzo / YooKassa / ...).

Examples:

    # Stars (no provider token needed)
    uv run python create_invoice.py stars \
        --title "Plan - Basic" --description "Monthly plan" --stars 1400

    # USD via the Stripe test token configured in BotFather
    uv run python create_invoice.py fiat \
        --title "Plan - Basic" --description "Monthly plan" \
        --currency USD --amount 9.99
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from decimal import Decimal

from dotenv import load_dotenv
from telegram import Bot, LabeledPrice


# Currencies that don't use minor units (no fractional part).
# https://core.telegram.org/bots/payments/currencies.json
_ZERO_DECIMAL_CURRENCIES = {
    "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW",
    "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
}
# Three-decimal currencies (very rare).
_THREE_DECIMAL_CURRENCIES = {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"}


def _to_minor_units(amount: str | float | Decimal, currency: str) -> int:
    """Convert a human price (e.g. 9.99 USD, 1000 JPY) into Telegram's integer
    minor-unit amount (999, 1000)."""
    currency = currency.upper()
    decimals = (
        0 if currency in _ZERO_DECIMAL_CURRENCIES
        else 3 if currency in _THREE_DECIMAL_CURRENCIES
        else 2
    )
    value = Decimal(str(amount))
    minor = (value * (Decimal(10) ** decimals)).to_integral_value()
    return int(minor)


async def create_stars_invoice_link(
    bot_token: str,
    *,
    title: str,
    description: str,
    stars: int,
    payload: str | None = None,
    label: str | None = None,
) -> tuple[str, str]:
    """Create an invoice link payable in Telegram Stars."""
    if stars < 1:
        raise ValueError("Telegram Stars amount must be a positive integer.")

    payload = payload or f"order_{secrets.token_urlsafe(12)}"
    async with Bot(token=bot_token) as bot:
        url = await bot.create_invoice_link(
            title=title,
            description=description,
            payload=payload,
            provider_token="",  # MUST be empty for Stars
            currency="XTR",
            prices=[LabeledPrice(label=label or title, amount=stars)],
        )
    return url, payload


async def create_fiat_invoice_link(
    bot_token: str,
    provider_token: str,
    *,
    title: str,
    description: str,
    currency: str,
    amount: str | float | Decimal,
    payload: str | None = None,
    label: str | None = None,
    need_email: bool = False,
    need_name: bool = False,
    need_shipping_address: bool = False,
) -> tuple[str, str]:
    """Create an invoice link payable in a real currency (USD, EUR, ...)
    through whichever payment provider you connected in BotFather.

    ``amount`` accepts a human number (``9.99`` or ``"9.99"``); it is
    converted to Telegram's required integer minor units automatically.
    """
    if not provider_token:
        raise ValueError(
            "PROVIDER_TOKEN is required for fiat currencies. "
            "Get one in BotFather: My Bots → <bot> → Payments."
        )

    minor = _to_minor_units(amount, currency)
    if minor < 1:
        raise ValueError("Amount must be greater than zero.")

    payload = payload or f"order_{secrets.token_urlsafe(12)}"
    async with Bot(token=bot_token) as bot:
        url = await bot.create_invoice_link(
            title=title,
            description=description,
            payload=payload,
            provider_token=provider_token,
            currency=currency.upper(),
            prices=[LabeledPrice(label=label or title, amount=minor)],
            need_email=need_email,
            need_name=need_name,
            need_shipping_address=need_shipping_address,
            send_email_to_provider=need_email,
        )
    return url, payload


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--title", required=True, help="Product title shown on the invoice.")
    p.add_argument("--description", required=True, help="Product description.")
    p.add_argument("--payload", default=None, help="Your internal order id (auto-generated if omitted).")
    p.add_argument("--label", default=None, help="Line-item label (defaults to --title).")


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Create a Telegram invoice link.")
    sub = parser.add_subparsers(dest="kind", required=True)

    p_stars = sub.add_parser("stars", help="Telegram Stars (XTR) invoice link.")
    _add_common_args(p_stars)
    p_stars.add_argument("--stars", type=int, required=True, help="Price in Stars (integer).")

    p_fiat = sub.add_parser("fiat", help="Real-world currency (USD/EUR/...) invoice link.")
    _add_common_args(p_fiat)
    p_fiat.add_argument("--currency", required=True, help="ISO 4217 code, e.g. USD, EUR, JPY.")
    p_fiat.add_argument("--amount", required=True, help="Human price, e.g. 9.99")
    p_fiat.add_argument("--need-email", action="store_true", help="Ask the buyer for email.")
    p_fiat.add_argument("--need-name", action="store_true", help="Ask the buyer for name.")
    p_fiat.add_argument("--need-shipping-address", action="store_true",
                        help="Ask the buyer for a shipping address.")

    args = parser.parse_args()

    token = os.environ.get("BOT_TOKEN")
    if not token:
        sys.exit("ERROR: BOT_TOKEN is not set. Copy .env.example to .env and fill in your bot token.")

    if args.kind == "stars":
        url, payload = asyncio.run(
            create_stars_invoice_link(
                token,
                title=args.title,
                description=args.description,
                stars=args.stars,
                payload=args.payload,
                label=args.label,
            )
        )
    else:
        provider_token = os.environ.get("PROVIDER_TOKEN")
        if not provider_token:
            sys.exit(
                "ERROR: PROVIDER_TOKEN is not set. Get a payment-provider token from "
                "BotFather (My Bots → <bot> → Payments) and put it in .env."
            )
        url, payload = asyncio.run(
            create_fiat_invoice_link(
                token,
                provider_token,
                title=args.title,
                description=args.description,
                currency=args.currency,
                amount=args.amount,
                payload=args.payload,
                label=args.label,
                need_email=args.need_email,
                need_name=args.need_name,
                need_shipping_address=args.need_shipping_address,
            )
        )

    print(f"Invoice URL : {url}")
    print(f"Payload     : {payload}")
    print("\nShare the URL with your buyer. When they pay, your bot will receive")
    print("a `successful_payment` update whose `invoice_payload` equals the payload above.")


if __name__ == "__main__":
    main()
