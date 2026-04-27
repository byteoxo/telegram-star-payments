# telegram-star-payments

Create [Telegram Stars](https://core.telegram.org/bots/payments-stars) (XTR) invoice
links and listen for the `successful_payment` event from your bot — exactly the
flow that produces links such as:

```
https://t.me/$8T-Xwi0VYFeVDAAABzfGCSlot30
```

## How it works

| Step | What happens                                                                                          | Telegram API                                |
| ---- | ----------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| 1    | Your backend asks the Bot API for an invoice link with `currency = "XTR"` and an empty `provider_token`. | [`createInvoiceLink`](https://core.telegram.org/bots/api#createinvoicelink) |
| 2    | You share the returned `https://t.me/$...` link. Tapping it opens Telegram's native "Pay with Stars" sheet. | —                                           |
| 3    | Right before charging, Telegram sends your bot a `pre_checkout_query`. **You must answer within 10 seconds**, otherwise the payment is canceled. | [`answerPreCheckoutQuery`](https://core.telegram.org/bots/api#answerprecheckoutquery) |
| 4    | After charging, your bot receives a regular message whose `successful_payment` field is set. That is the "payment complete" signal. | [`SuccessfulPayment`](https://core.telegram.org/bots/api#successfulpayment) |
| 5    | Optional: you can refund within 21 days.                                                              | [`refundStarPayment`](https://core.telegram.org/bots/api#refundstarpayment) |

The `invoice_payload` field round-trips through every step, so set it to your
internal order id and use it to reconcile the payment with whatever you sold.

## Prerequisites

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. (One-time) In BotFather, open your bot → *Payments* — Stars are available
   without any external provider, so just keep the default settings.
3. Python 3.12 and [uv](https://docs.astral.sh/uv/).

## Setup

```bash
uv sync
cp .env.example .env
# edit .env and put your BOT_TOKEN in
```

## Usage

### A. Run the bot (recommended)

```bash
uv run python main.py
```

Then in Telegram:

```
/buy 1400 Plan - Basic
```

The bot replies with the invoice link **and** a "⭐ Pay 1400 Stars" button.
Once the user pays, the bot replies with a confirmation and (if `ADMIN_CHAT_ID`
is set) DMs the admin. Payment data is also kept in memory and queryable via
`/status <payload>`.

### B. Just generate a link from the CLI

If you only need the URL (e.g. to put into your own web page), use:

```bash
uv run python create_invoice.py \
    --title "Plan - Basic" \
    --description "Monthly access" \
    --stars 1400
```

Output:

```
Invoice URL : https://t.me/$8T-Xwi0VYFeVDAAABzfGCSlot30
Payload     : order_AbCdEf123...
```

You still need the bot from option A (or your own webhook) running somewhere to
catch the `successful_payment` callback when the user actually pays — Telegram
sends that update only to the bot that issued the invoice.

## Going to production

The `main.py` shipped here uses long polling and an in-memory ledger, which is
fine for development. For production you should:

- **Persist orders** (`PAID_ORDERS` / `PENDING_ORDERS`) to a real database.
- **Use webhooks** instead of polling: `Application.run_webhook(...)` or your
  own HTTP framework calling `bot.process_update(...)`.
- **Be idempotent** in `on_successful_payment` — Telegram may redeliver if your
  webhook returns non-2xx.
- **Verify** the `invoice_payload` against what you stored when the link was
  created, to avoid replay/forging by malformed clients.
- **Always answer** `pre_checkout_query` within 10 seconds; otherwise the
  charge is reverted.

## Files

- `main.py` — the polling bot, including pre-checkout and successful-payment handlers.
- `create_invoice.py` — standalone CLI that just creates the invoice URL.
- `.env.example` — required environment variables.
