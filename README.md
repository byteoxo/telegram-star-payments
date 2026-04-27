# telegram-star-payments

Create Telegram invoice links and listen for the `successful_payment` event
from your bot — works for both:

- **Telegram Stars** (`XTR`) — the in-app currency, no external provider
  required. Produces links like `https://t.me/$8T-Xwi0VYFeVDAAABzfGCSlot30`.
- **Real currencies** (`USD`, `EUR`, `JPY`, ...) via an external payment
  provider (Stripe / Smart Glocal / Tranzzo / YooKassa / ...) connected
  through BotFather.

The bot, the invoice link generation, the `pre_checkout_query` and the
`successful_payment` handlers are exactly the same for both — the only
differences are `currency`, the `provider_token`, and how amounts are
encoded.

## How it works

| Step | What happens                                                                                                  | Telegram API                                                                                  |
| ---- | ------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| 1    | Your backend asks the Bot API for an invoice link.                                                            | [`createInvoiceLink`](https://core.telegram.org/bots/api#createinvoicelink)                   |
| 2    | You share the returned `https://t.me/$...` link. Tapping it opens Telegram's native payment sheet.            | —                                                                                             |
| 3    | Right before charging, Telegram sends your bot a `pre_checkout_query`. **Answer within 10 seconds.**           | [`answerPreCheckoutQuery`](https://core.telegram.org/bots/api#answerprecheckoutquery)         |
| 4    | After charging, your bot receives a message whose `successful_payment` field is set. That's "payment complete". | [`SuccessfulPayment`](https://core.telegram.org/bots/api#successfulpayment)                   |
| 5    | Optional refund (Stars: via Bot API; fiat: via the provider's dashboard).                                     | [`refundStarPayment`](https://core.telegram.org/bots/api#refundstarpayment) (Stars only)      |

The `invoice_payload` field round-trips through every step, so set it to your
internal order id and use it to reconcile the payment with whatever you sold.

### Stars vs fiat

|                       | Stars (`XTR`)                              | Fiat (`USD`, `EUR`, ...)                                                                |
| --------------------- | ------------------------------------------ | --------------------------------------------------------------------------------------- |
| `currency`            | `"XTR"`                                    | ISO 4217, e.g. `"USD"`                                                                  |
| `provider_token`      | `""` (must be empty)                       | required, from BotFather                                                                |
| Amount encoding       | integer star count (e.g. `1400`)           | integer minor units (`$9.99` → `999`; `¥1000` → `1000`)                                 |
| Pay sheet             | "⭐ Pay X Stars" — instant in Telegram     | "Pay $9.99" — collects card via your provider (Stripe / Smart Glocal / ...)             |
| Refunds               | `bot.refund_star_payment(...)`             | provider dashboard (e.g. Stripe / Smart Glocal merchant console)                        |
| Where it works        | Anywhere Telegram works                    | Wherever your provider supports cards                                                   |

## Prerequisites

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. **For fiat only:** in BotFather, `My Bots → <bot> → Payments`, pick a
   provider, and copy the **Test** token for development. Stars don't need
   this step.

   The list of providers BotFather offers depends on your region. Common ones:

   - **Stripe** — global, USD/EUR/etc. (not always offered, e.g. not visible
     to many CN/RU accounts).
   - **Smart Glocal** — supports USD/EUR/RUB and is widely available.
   - **YooKassa**, **PayMaster**, **Tranzzo**, **LiqPay**, etc.

   You only need **one**. The bot code in this repo is provider-agnostic —
   any token works the same way.
3. Python 3.12 and [uv](https://docs.astral.sh/uv/).

## Setup

```bash
uv sync
cp .env.example .env
# edit .env: set BOT_TOKEN and (for fiat) PROVIDER_TOKEN
```

## Usage

### A. Run the bot (recommended)

```bash
uv run python main.py
```

In Telegram:

```
/buy_stars 1400 Plan - Basic
/buy_fiat USD 9.99 Plan - Basic
/buy_fiat EUR 4.50 Coffee
/buy_fiat JPY 1000 Onigiri          # zero-decimal currency, handled automatically
/status <payload>
/refund <payload>                    # Stars only
```

The bot replies with the invoice link plus a payment button. Once the user
pays:

- The bot replies `✅ Payment received!` with the charge id.
- A `PaymentRecord` is stored in `PAID_ORDERS` (in memory).
- If `ADMIN_CHAT_ID` is set, the admin is DM'd.

### B. Just generate a link from the CLI

```bash
# Stars
uv run python create_invoice.py stars \
    --title "Plan - Basic" --description "Monthly plan" --stars 1400

# USD via your configured provider
uv run python create_invoice.py fiat \
    --title "Plan - Basic" --description "Monthly plan" \
    --currency USD --amount 9.99
```

Output:

```
Invoice URL : https://t.me/$8T-Xwi0VYFeVDAAABzfGCSlot30
Payload     : order_AbCdEf123...
```

You still need the bot from option A (or your own webhook) running so the
`successful_payment` callback is received — Telegram sends it to the bot
that issued the invoice.

### Testing fiat

Use the **Test** token from your provider in BotFather. Each provider has
its own test card numbers — most accept the dummy Visa cards below.

| Provider     | Card                  | Exp           | CVC   | 3-DS code  |
| ------------ | --------------------- | ------------- | ----- | ---------- |
| Stripe       | `4242 4242 4242 4242` | any future    | any 3 | n/a        |
| Smart Glocal | `4111 1111 1111 1111` | any future    | `123` | `12345678` |
| YooKassa     | `5555 5555 5555 4444` | any future    | any 3 | `12345678` |

If a charge is declined, double-check that you used the **Test** token (live
tokens reject test cards). Switch to the live token only after end-to-end
testing.

## Going to production

The `main.py` shipped here uses long polling and an in-memory ledger, which
is fine for development. For production you should:

- **Persist orders** (`PAID_ORDERS` / `PENDING_ORDERS`) to a real database.
- **Use webhooks** instead of polling: `Application.run_webhook(...)` or your
  own HTTP framework calling `bot.process_update(...)`.
- **Be idempotent** in `on_successful_payment` — Telegram may redeliver if
  your webhook returns non-2xx.
- **Verify** that the `pre_checkout_query`'s `currency` and `total_amount`
  match the order you created (already done here).
- **Always answer** `pre_checkout_query` within 10 seconds; otherwise the
  charge is reverted.
- For fiat, **handle disputes/refunds in your provider's dashboard**, not via
  Bot API. Store `provider_payment_charge_id` so you can find the charge
  there.

## Files

- `main.py` — polling bot with `pre_checkout` and `successful_payment` handlers
  for both Stars and fiat.
- `create_invoice.py` — standalone CLI to mint invoice URLs.
- `.env.example` — required environment variables.
