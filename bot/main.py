import asyncio
import json
import logging
import os
import aiohttp
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import BotCommand
from dotenv import load_dotenv

from bot.database import Store, resolve_database_path
from bot.customer_bot_runtime import CustomerBotFleet
from bot.handlers import (
    ADMIN_CONTACT_USERNAME,
    admin_router,
    notify_expired_orders,
    poll_khqr_payments,
    user_router,
)
from bot.keyboards import (
    BUTTON_EMOJI_TARGET_LABELS,
    customer_bot_setup_keyboard,
    reset_button_emoji_overrides,
    set_button_emoji_overrides,
)
from bot.payments import DEFAULT_GATEWAY_URL, DEFAULT_PROFILE_ID
from bot.payments import verify_transaction

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ACCOUNT_DATA_KEY = os.getenv("ACCOUNT_DATA_KEY", "").strip()
DATABASE_PATH = str(
    resolve_database_path(os.getenv("DATABASE_PATH", "bot_data.sqlite3").strip() or "bot_data.sqlite3")
)
KHQRCC_PROFILE_ID = os.getenv("KHQRCC_PROFILE_ID", DEFAULT_PROFILE_ID).strip()
KHQRCC_SECRET_KEY = os.getenv("KHQRCC_SECRET_KEY", "").strip()
PAYMENTS_ENABLED = os.getenv("PAYMENTS_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
_ADMIN_CONTACT_USER_ID = os.getenv("ADMIN_CONTACT_USER_ID", "").strip()
try:
    ADMIN_CONTACT_USER_ID = int(_ADMIN_CONTACT_USER_ID) if _ADMIN_CONTACT_USER_ID else None
except ValueError as error:
    raise RuntimeError("ADMIN_CONTACT_USER_ID must be a numeric Telegram user ID.") from error
if ADMIN_CONTACT_USER_ID is not None and ADMIN_CONTACT_USER_ID <= 0:
    raise RuntimeError("ADMIN_CONTACT_USER_ID must be a positive Telegram user ID.")
KHQRCC_GATEWAY_URL = os.getenv("KHQRCC_GATEWAY_URL", DEFAULT_GATEWAY_URL).strip()
KHQRCC_SUCCESS_URL = os.getenv(
    "KHQRCC_SUCCESS_URL",
    "https://t.me/rovistarshopBot",
).strip()
KHQRCC_VERIFY_URL = os.getenv(
    "KHQRCC_VERIFY_URL",
    f"https://khqr.cc/api/{KHQRCC_PROFILE_ID}/payment-gateway/v1/payments/check-transv2-khqrcc",
).strip()
KHQRCC_VERIFY_URL_OVERRIDDEN = bool(os.getenv("KHQRCC_VERIFY_URL", "").strip())


def _load_admin_ids() -> frozenset[int]:
    values = os.getenv("ADMIN_USER_IDS", "")
    try:
        admin_ids = frozenset(int(value.strip()) for value in values.split(",") if value.strip())
    except ValueError as error:
        raise RuntimeError("ADMIN_USER_IDS must be comma-separated numeric Telegram IDs.") from error
    if not admin_ids:
        raise RuntimeError("Set at least one numeric Telegram user ID in ADMIN_USER_IDS.")
    return admin_ids


async def _button_emoji_overrides_middleware(handler, event, data):
    store = data.get("store")
    if not isinstance(store, Store):
        return await handler(event, data)
    try:
        stored = json.loads(await store.get_setting("button_emoji_overrides", "{}"))
    except (TypeError, ValueError):
        stored = {}
    valid_overrides = {
        key: value
        for key, value in stored.items()
        if isinstance(stored, dict)
        and key in BUTTON_EMOJI_TARGET_LABELS
        and isinstance(value, str)
        and value.isdecimal()
    } if isinstance(stored, dict) else {}
    token = set_button_emoji_overrides(valid_overrides)
    try:
        return await handler(event, data)
    finally:
        reset_button_emoji_overrides(token)


async def _order_expiry_loop(bot: Bot, store: Store) -> None:
    while True:
        await asyncio.sleep(15)
        try:
            await notify_expired_orders(bot, store)
        except Exception:
            logging.exception("Order expiration check failed")


async def _khqr_poll_loop(
    bot: Bot,
    store: Store,
    payment_config: dict[str, str],
    admin_ids: frozenset[int],
) -> None:
    while True:
        try:
            await poll_khqr_payments(bot, store, payment_config, admin_ids)
        except Exception:
            logging.exception("KHQR payment verification failed")
        await asyncio.sleep(1)


async def _customer_bot_subscription_loop(
    bot: Bot,
    store: Store,
    fleet: CustomerBotFleet,
    payment_config: dict[str, str],
) -> None:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        while True:
            try:
                fleet.master_admin_username = await store.get_setting(
                    "support_username",
                    ADMIN_CONTACT_USERNAME,
                )
                if payment_config.get("enabled") == "true" and payment_config.get("secret_key"):
                    for pending_order in await store.pending_customer_bot_orders():
                        check = await verify_transaction(
                            transaction_id=pending_order["payment_tx_id"],
                            secret_key=payment_config["secret_key"],
                            verify_url=payment_config["verify_url"],
                            session=session,
                        )
                        if check.status != "success" or check.amount_cents != int(pending_order["amount_cents"]):
                            continue
                        paid_order = await store.mark_customer_bot_order_paid(
                            int(pending_order["id"]),
                            verified_amount_cents=check.amount_cents,
                        )
                        if paid_order:
                            plan_label = {
                                "monthly": "1 ខែ",
                                "two_months": "2 ខែ",
                                "yearly": "1 ឆ្នាំ",
                            }.get(paid_order["plan_key"], "subscription")
                            await bot.send_message(
                                int(paid_order["owner_user_id"]),
                                f"ការបង់ប្រាក់ package {plan_label} · ${int(paid_order['amount_cents']) / 100:.2f} "
                                "បាន Verify ជោគជ័យ។ ឥឡូវនេះចុចខាងក្រោមដើម្បីចាប់ផ្តើមបង្កើត bot៖",
                                reply_markup=customer_bot_setup_keyboard(int(paid_order["id"])),
                            )
                for expired_order in await store.expire_pending_customer_bot_orders():
                    await bot.send_message(
                        int(expired_order["owner_user_id"]),
                        f"Invoice បង់ថ្លៃ bot #{expired_order['id']} ផុតកំណត់ហើយ។ "
                        "សូមចុចបង្កើត bot ម្ដងទៀតដើម្បីជ្រើស package ថ្មី។",
                    )
                if payment_config.get("enabled") == "true" and payment_config.get("secret_key"):
                    for pending in await store.pending_customer_bots():
                        check = await verify_transaction(
                            transaction_id=pending["payment_tx_id"],
                            secret_key=payment_config["secret_key"],
                            verify_url=payment_config["verify_url"],
                            session=session,
                        )
                        if check.status != "success" or check.amount_cents != int(pending["amount_cents"]):
                            continue
                        activated = await store.activate_customer_bot(
                            int(pending["id"]),
                            verified_amount_cents=check.amount_cents,
                        )
                        if activated:
                            await bot.send_message(
                                int(activated["owner_user_id"]),
                                f"ការបង់ប្រាក់ bot @{activated['bot_username']} បាន Verify ជោគជ័យ។ "
                                "Bot នឹងចាប់ផ្តើមក្នុងពេលឆាប់ៗនេះ និងដំណើរការ 30 ថ្ងៃ។",
                            )
                for expired in await store.expire_pending_customer_bots():
                    await bot.send_message(
                        int(expired["owner_user_id"]),
                        f"Invoice បង្កើត bot #{expired['id']} ផុតកំណត់ហើយ។ សូមចាប់ផ្តើមបង្កើតម្ដងទៀត។",
                    )
                expired_active = await store.expire_customer_bots()
                for expired in expired_active:
                    await bot.send_message(
                        int(expired["owner_user_id"]),
                        f"Bot subscription #{expired['id']} បានផុតកំណត់ ហើយ bot ត្រូវបានបិទ។ "
                        "សូមបង្កើតការបញ្ជាទិញថ្មីដើម្បីបន្តសេវា 30 ថ្ងៃទៀត។",
                    )
                await fleet.reconcile(await store.active_customer_bots())
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Customer bot subscription manager failed")
            await asyncio.sleep(3)


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in .env before starting the bot.")
    if not ACCOUNT_DATA_KEY:
        raise RuntimeError(
            "Set ACCOUNT_DATA_KEY in .env. Generate one with "
            "Fernet.generate_key() as described in README.md."
        )

    admin_ids = _load_admin_ids()
    if not PAYMENTS_ENABLED:
        logging.warning("KHQR checkout is disabled because the Direct QR API is not reachable from this machine.")
    elif not KHQRCC_SECRET_KEY or KHQRCC_SECRET_KEY.lower().startswith(("your_", "replace_", "placeholder")):
        logging.warning("KHQRCC_SECRET_KEY is not configured; checkout buttons will not create payment orders.")
    store = Store(Path(DATABASE_PATH), ACCOUNT_DATA_KEY)
    await store.initialize()
    active_profile_id = await store.get_setting("khqr_profile_id", KHQRCC_PROFILE_ID)
    active_secret_key = await store.get_secret_setting("khqr_secret_key", KHQRCC_SECRET_KEY)
    active_verify_url = (
        KHQRCC_VERIFY_URL
        if KHQRCC_VERIFY_URL_OVERRIDDEN
        else f"https://khqr.cc/api/{active_profile_id}/payment-gateway/v1/payments/check-transv2-khqrcc"
    )

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="បង្ហាញម៉ឺនុយស្វាគមន៍"),
            BotCommand(command="menu", description="បើកម៉ឺនុយប៊ូតុង"),
        ]
    )
    dispatcher = Dispatcher(
        storage=MemoryStorage(),
        events_isolation=SimpleEventIsolation(),
    )
    dispatcher.update.outer_middleware(_button_emoji_overrides_middleware)
    dispatcher["store"] = store
    dispatcher["admin_ids"] = admin_ids
    dispatcher["admin_contact_user_id"] = ADMIN_CONTACT_USER_ID
    payment_config = {
        "profile_id": active_profile_id,
        "secret_key": active_secret_key,
        "gateway_url": KHQRCC_GATEWAY_URL,
        "success_url": KHQRCC_SUCCESS_URL,
        "verify_url": active_verify_url,
        "verify_url_overridden": "true" if KHQRCC_VERIFY_URL_OVERRIDDEN else "false",
        "enabled": "true" if PAYMENTS_ENABLED else "false",
    }
    dispatcher["payment_config"] = payment_config
    dispatcher.include_router(admin_router)
    dispatcher.include_router(user_router)

    expiry_task = asyncio.create_task(_order_expiry_loop(bot, store))
    payment_task = asyncio.create_task(_khqr_poll_loop(bot, store, payment_config, admin_ids))
    customer_bot_fleet = CustomerBotFleet(
        dispatcher,
        Path(DATABASE_PATH).parent,
        ACCOUNT_DATA_KEY,
        notification_bot=bot,
    )
    customer_bot_task = asyncio.create_task(
        _customer_bot_subscription_loop(bot, store, customer_bot_fleet, payment_config)
    )
    try:
        await dispatcher.start_polling(bot)
    finally:
        expiry_task.cancel()
        payment_task.cancel()
        customer_bot_task.cancel()
        await customer_bot_fleet.shutdown()
        await asyncio.gather(expiry_task, payment_task, customer_bot_task, return_exceptions=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
