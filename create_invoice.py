"""Create a Telegram Stars (XTR) invoice link.

Usage:
    uv run python create_invoice.py --title "Plan - Basic" --description "Monthly plan" --stars 1400

The script prints a link like:
    https://t.me/$8T-Xwi0VYFeVDAAABzfGCSlot30
which, when opened in Telegram, shows the in-app "Pay with Stars" sheet.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys

from dotenv import load_dotenv
from telegram import Bot, LabeledPrice


async def create_stars_invoice_link(
    bot_token: str,
    *,
    title: str,
    description: str,
    stars: int,
    payload: str | None = None,
    label: str | None = None,
) -> tuple[str, str]:
    """Create an invoice link payable in Telegram Stars.

    Returns a tuple of ``(invoice_url, payload)``. The payload is what comes
    back to your bot inside ``SuccessfulPayment.invoice_payload`` so you can
    correlate the payment with whatever you are selling.
    """
    if stars < 1:
        raise ValueError("Telegram Stars amount must be a positive integer.")

    payload = payload or f"order_{secrets.token_urlsafe(12)}"
    bot = Bot(token=bot_token)

    async with bot:
        url = await bot.create_invoice_link(
            title=title,
            description=description,
            payload=payload,
            provider_token="",  # MUST be empty for Stars
            currency="XTR",     # XTR == Telegram Stars
            prices=[LabeledPrice(label=label or title, amount=stars)],
        )
    return url, payload


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Create a Telegram Stars invoice link.")
    parser.add_argument("--title", required=True, help="Product title shown on the invoice.")
    parser.add_argument("--description", required=True, help="Product description.")
    parser.add_argument("--stars", type=int, required=True, help="Price in Stars (integer).")
    parser.add_argument("--payload", default=None, help="Your internal order id (auto-generated if omitted).")
    parser.add_argument("--label", default=None, help="Line-item label (defaults to --title).")
    args = parser.parse_args()

    token = os.environ.get("BOT_TOKEN")
    if not token:
        sys.exit("ERROR: BOT_TOKEN is not set. Copy .env.example to .env and fill in your bot token.")

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

    print(f"Invoice URL : {url}")
    print(f"Payload     : {payload}")
    print("\nShare the URL with your buyer. When they pay, your bot will receive")
    print("a `successful_payment` update whose `invoice_payload` equals the payload above.")


if __name__ == "__main__":
    main()
