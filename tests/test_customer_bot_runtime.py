import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet

from bot.customer_bot_runtime import CustomerBotFleet
from bot.database import Store
from bot.keyboards import CUSTOM_EMOJI_IDS, button as make_button


class CustomerBotRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_unresolved_username_sends_owner_an_open_bot_button(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        encryption_key = Fernet.generate_key().decode("ascii")
        session = SimpleNamespace(closed=False, close=AsyncMock())
        fake_bot = SimpleNamespace(
            session=session,
            get_me=AsyncMock(return_value=SimpleNamespace(username="resolved_shop_bot")),
            delete_webhook=AsyncMock(),
            get_updates=AsyncMock(
                side_effect=[[SimpleNamespace(update_id=1)], asyncio.CancelledError]
            ),
        )
        notification_bot = SimpleNamespace(send_message=AsyncMock())
        rendered_icon_ids = []

        async def capture_tenant_button(*args, **kwargs):
            rendered_icon_ids.append(make_button("Cart", "test", icon="cart").icon_custom_emoji_id)

        dispatcher = SimpleNamespace(
            resolve_used_update_types=lambda: [],
            feed_update=AsyncMock(side_effect=capture_tenant_button),
        )
        fleet = CustomerBotFleet(
            dispatcher,
            Path(temp_dir.name),
            encryption_key,
            notification_bot=notification_bot,
        )
        fleet.master_admin_username = "MASTER_HELP"
        subscription = {
            "id": 1,
            "owner_user_id": 200,
            "bot_token": "format-valid-token",
            "bot_username": "",
            "khqr_profile_id": "",
            "khqr_secret_key": "",
            "admin_user_id": 200,
        }
        tenant_store = Store(Path(temp_dir.name) / "customer_bots" / "1.sqlite3", encryption_key)
        await tenant_store.initialize()
        await tenant_store.set_setting("button_emoji_overrides", '{"cart":"1234567890123456789"}')

        with patch("bot.customer_bot_runtime.Bot", return_value=fake_bot):
            with self.assertRaises(asyncio.CancelledError):
                await fleet._run(1, subscription)

        notification_bot.send_message.assert_awaited_once()
        notification = notification_bot.send_message.await_args
        self.assertEqual(notification.args[0], 200)
        button = notification.kwargs["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(button.url, "https://t.me/resolved_shop_bot")
        fake_bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)
        self.assertEqual(fake_bot.get_updates.await_count, 2)
        dispatcher.feed_update.assert_awaited_once()
        self.assertTrue(dispatcher.feed_update.await_args.kwargs["customer_bot_mode"])
        self.assertEqual(rendered_icon_ids, ["1234567890123456789"])
        self.assertEqual(make_button("Cart", "test", icon="cart").icon_custom_emoji_id, CUSTOM_EMOJI_IDS["cart"])
        session.close.assert_awaited_once()

        self.assertEqual(await tenant_store.get_setting("customer_bot_open_link_sent"), "true")
        self.assertEqual(await tenant_store.get_setting("master_admin_username"), "MASTER_HELP")


if __name__ == "__main__":
    unittest.main()
