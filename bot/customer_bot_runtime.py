import asyncio
import json
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

from bot.database import Store
from bot.keyboards import (
    customer_bot_open_keyboard,
    reset_button_emoji_overrides,
    set_button_emoji_overrides,
)

logger = logging.getLogger(__name__)


class CustomerBotFleet:
    def __init__(
        self,
        dispatcher: Dispatcher,
        database_root: str | Path,
        encryption_key: str,
        notification_bot: Bot | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.database_root = Path(database_root) / "customer_bots"
        self.encryption_key = encryption_key
        self.notification_bot = notification_bot
        self.master_admin_username = "ROVISTARCAMBO"
        self._tasks: dict[int, asyncio.Task] = {}
        self._bots: dict[int, Bot] = {}

    async def reconcile(self, subscriptions: list[dict]) -> None:
        active = {int(subscription["id"]): subscription for subscription in subscriptions}
        for bot_id in list(self._tasks):
            if bot_id not in active:
                await self.stop(bot_id)
        for bot_id, subscription in active.items():
            task = self._tasks.get(bot_id)
            if task is None or task.done():
                self._tasks[bot_id] = asyncio.create_task(
                    self._run(bot_id, subscription),
                    name=f"customer-bot-{bot_id}",
                )

    async def _run(self, bot_id: int, subscription: dict) -> None:
        bot = None
        try:
            bot = Bot(
                token=subscription["bot_token"],
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
            self._bots[bot_id] = bot
            me = await bot.get_me()
            expected_username = subscription.get("bot_username", "")
            if expected_username and me.username != expected_username:
                logger.error("Customer bot identity changed for subscription %s", bot_id)
                return
            logger.info("Customer bot %s connected as @%s", bot_id, me.username)
            database_path = self.database_root / f"{bot_id}.sqlite3"
            store = Store(database_path, self.encryption_key)
            await store.initialize()
            if self.notification_bot is not None and me.username:
                notified = await store.get_setting("customer_bot_open_link_sent", "false")
                if notified != "true":
                    try:
                        await self.notification_bot.send_message(
                            int(subscription["owner_user_id"]),
                            f"Bot របស់អ្នក @{me.username} តភ្ជាប់រួចរាល់។ ចុចប៊ូតុងខាងក្រោមដើម្បីបើក៖",
                            reply_markup=customer_bot_open_keyboard(me.username),
                        )
                        await store.set_setting("customer_bot_open_link_sent", "true")
                    except TelegramAPIError as error:
                        logger.warning("Could not send customer bot open link for subscription %s: %s", bot_id, error)
            profile_id = await store.get_setting("khqr_profile_id", subscription["khqr_profile_id"])
            secret_key = await store.get_secret_setting("khqr_secret_key", subscription["khqr_secret_key"])
            payment_config = {
                "profile_id": profile_id,
                "secret_key": secret_key,
                "gateway_url": "https://khqr.cc/api/payment/requestv2",
                "success_url": "https://t.me/rovistarshopBot",
                "verify_url": (
                    f"https://khqr.cc/api/{profile_id}/"
                    "payment-gateway/v1/payments/check-transv2-khqrcc"
                ),
                "verify_url_overridden": "false",
                "enabled": "true",
            }
            await bot.delete_webhook(drop_pending_updates=False)
            update_types = self.dispatcher.resolve_used_update_types()
            offset = 0
            while True:
                updates = await bot.get_updates(
                    offset=offset,
                    timeout=30,
                    allowed_updates=update_types,
                )
                for update in updates:
                    offset = update.update_id + 1
                    await store.set_setting("master_admin_username", self.master_admin_username)
                    try:
                        emoji_overrides = json.loads(await store.get_setting("button_emoji_overrides", "{}"))
                    except (TypeError, ValueError):
                        emoji_overrides = {}
                    if not isinstance(emoji_overrides, dict):
                        emoji_overrides = {}
                    legacy_product_emoji = await store.get_setting("product_button_emoji_id", "")
                    if legacy_product_emoji:
                        emoji_overrides.setdefault("product", legacy_product_emoji)
                    emoji_overrides["__hide_defaults__"] = "true"
                    emoji_context = set_button_emoji_overrides(emoji_overrides)
                    try:
                        await self.dispatcher.feed_update(
                            bot,
                            update,
                            store=store,
                            admin_ids=frozenset({int(subscription["admin_user_id"])}),
                            admin_contact_user_id=None,
                            payment_config=payment_config,
                            customer_bot_mode=True,
                        )
                    finally:
                        reset_button_emoji_overrides(emoji_context)
        except asyncio.CancelledError:
            raise
        except TelegramAPIError as error:
            logger.error("Customer bot %s stopped: %s", bot_id, error)
        except Exception:
            logger.exception("Customer bot %s runtime failed", bot_id)
        finally:
            self._bots.pop(bot_id, None)
            if bot is not None:
                await bot.session.close()

    async def stop(self, bot_id: int) -> None:
        task = self._tasks.pop(bot_id, None)
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def shutdown(self) -> None:
        bot_ids = list(self._tasks)
        await asyncio.gather(*(self.stop(bot_id) for bot_id in bot_ids), return_exceptions=True)
