import tempfile
import json
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite
from aiogram.enums import ChatType
from aiogram.types import Chat, Message, User
from cryptography.fernet import Fernet

from bot.database import Store
from bot.keyboards import admin_panel_shortcut_keyboard, telegram_user_id_help_keyboard
from bot.handlers import (
    AdminKhqrSettingsForm,
    AdminButtonEmojiForm,
    _show_admin_khqr_settings,
    apply_button_emoji_target,
    begin_button_emoji_setup,
    receive_button_emoji_id,
    receive_customer_bot_admin_user_id,
    _payment_setup_error,
    save_khqr_profile,
    save_khqr_secret,
)


class StateDouble:
    def __init__(self) -> None:
        self.current_state = None
        self.data = {}

    async def clear(self) -> None:
        self.current_state = None

    async def set_state(self, state) -> None:
        self.current_state = state

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)

    async def get_data(self) -> dict:
        return dict(self.data)


class AdminKhqrSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp_dir.name) / "settings.sqlite3", Fernet.generate_key().decode("ascii"))
        await self.store.initialize()
        self.state = StateDouble()
        self.admin_ids = frozenset({100})
        self.config = {
            "profile_id": "old-profile",
            "secret_key": "old-secret-value",
            "verify_url": "https://khqr.cc/api/old-profile/payment-gateway/v1/payments/check-transv2-khqrcc",
            "verify_url_overridden": "false",
        }

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    def make_message(self, text: str) -> Message:
        return Message(
            message_id=1,
            date=datetime.now(),
            chat=Chat(id=100, type=ChatType.PRIVATE),
            from_user=User(id=100, is_bot=False, first_name="Admin"),
            text=text,
        )

    async def test_profile_change_updates_persisted_profile_and_verify_url(self) -> None:
        message = self.make_message("new-profile-123")
        with patch.object(Message, "answer", new_callable=AsyncMock):
            await save_khqr_profile(message, self.state, self.store, self.config, self.admin_ids)

        self.assertEqual(await self.store.get_setting("khqr_profile_id"), "new-profile-123")
        self.assertEqual(self.config["profile_id"], "new-profile-123")
        self.assertEqual(
            self.config["verify_url"],
            "https://khqr.cc/api/new-profile-123/payment-gateway/v1/payments/check-transv2-khqrcc",
        )
        self.assertIsNone(self.state.current_state)

    async def test_secret_change_deletes_chat_message_and_stores_only_ciphertext(self) -> None:
        secret = "new-merchant-secret-value"
        message = self.make_message(secret)
        with patch.object(Message, "delete", new_callable=AsyncMock) as delete_message:
            with patch.object(Message, "answer", new_callable=AsyncMock):
                await save_khqr_secret(message, self.state, self.store, self.config, self.admin_ids)

        delete_message.assert_awaited_once()
        self.assertEqual(self.config["secret_key"], secret)
        self.assertEqual(await self.store.get_secret_setting("khqr_secret_key"), secret)
        async with aiosqlite.connect(self.store.path) as db:
            row = await (await db.execute(
                "SELECT setting_value FROM bot_settings WHERE setting_key = 'secret:khqr_secret_key'"
            )).fetchone()
        self.assertNotEqual(row[0], secret)
        self.assertIsNone(self.state.current_state)

    async def test_customer_admin_id_prompt_has_userinfo_button_and_advances(self) -> None:
        help_button = telegram_user_id_help_keyboard().inline_keyboard[0][0]
        self.assertEqual(help_button.url, "https://t.me/userinfo3bot")
        self.assertEqual(help_button.icon_custom_emoji_id, "5980828026529649035")

        message = self.make_message("123456789")
        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            await receive_customer_bot_admin_user_id(message, self.state, self.store)
        self.assertIsNone(self.state.current_state)
        self.assertIn("មិនត្រូវនឹងគណនី", answer.await_args.args[0])

        self.state.data = {
            "customer_bot_token": "validated-token",
            "customer_bot_username": "customer_shop_bot",
            "customer_bot_free_trial": True,
        }
        await self.store.register_user(100, "Owner", "owner")
        self.store.create_first_customer_bot_trial = AsyncMock(
            return_value={"id": 1, "bot_username": "customer_shop_bot"}
        )
        message = self.make_message("100")
        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            await receive_customer_bot_admin_user_id(message, self.state, self.store)

        self.assertEqual(self.state.data["customer_bot_admin_user_id"], 100)
        self.assertIsNone(self.state.current_state)
        self.store.create_first_customer_bot_trial.assert_awaited_once_with(
            owner_user_id=100,
            bot_token="validated-token",
            bot_username="customer_shop_bot",
            khqr_profile_id="",
            khqr_secret_key="",
            admin_user_id=100,
        )
        self.assertIn("/admin", answer.await_args.args[0])

    async def test_customer_checkout_requires_own_khqr_configuration(self) -> None:
        self.assertIsNotNone(_payment_setup_error({"profile_id": "", "secret_key": "", "enabled": "true"}))
        self.assertIsNone(
            _payment_setup_error(
                {"profile_id": "private-profile", "secret_key": "private-secret-value", "enabled": "true"}
            )
        )

    async def test_khqr_settings_has_blue_admin_shortcut_with_requested_emoji(self) -> None:
        button = admin_panel_shortcut_keyboard("@MASTER_HELP").inline_keyboard[0][0]
        self.assertEqual(button.url, "https://t.me/MASTER_HELP")
        self.assertEqual(button.style, "primary")
        self.assertEqual(button.icon_custom_emoji_id, "5316992572680320646")
        await self.store.set_setting("master_admin_username", "MASTER_HELP")

        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            await _show_admin_khqr_settings(self.make_message("settings"), self.store, self.config)

        self.assertEqual(answer.await_count, 2)
        shortcut = answer.await_args_list[1].kwargs["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(shortcut.url, "https://t.me/MASTER_HELP")

    async def test_admin_can_set_and_reset_each_button_emoji(self) -> None:
        with patch.object(Message, "answer", new_callable=AsyncMock):
            await begin_button_emoji_setup(self.make_message("emoji"), self.state, self.admin_ids)
        self.assertEqual(self.state.current_state, AdminButtonEmojiForm.emoji_id)

        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            await receive_button_emoji_id(
                self.make_message("Emoji ID: 1234567890123456789"),
                self.state,
                self.store,
                self.admin_ids,
            )
        target_callbacks = [
            button.callback_data
            for row in answer.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
            if button.callback_data
        ]
        self.assertIn("admin:button_emoji:target:cart", target_callbacks)
        self.assertEqual(self.state.data["button_emoji_pending_id"], "1234567890123456789")

        query = SimpleNamespace(
            from_user=User(id=100, is_bot=False, first_name="Admin"),
            data="admin:button_emoji:target:cart",
            message=self.make_message("target"),
            answer=AsyncMock(),
        )
        with patch.object(Message, "edit_reply_markup", new_callable=AsyncMock):
            with patch.object(Message, "answer", new_callable=AsyncMock):
                await apply_button_emoji_target(query, self.state, self.store, self.admin_ids)
        overrides = json.loads(await self.store.get_setting("button_emoji_overrides"))
        self.assertEqual(overrides["cart"], "1234567890123456789")

        with patch.object(Message, "answer", new_callable=AsyncMock):
            await begin_button_emoji_setup(self.make_message("emoji"), self.state, self.admin_ids)
        with patch.object(Message, "answer", new_callable=AsyncMock):
            await receive_button_emoji_id(self.make_message("0"), self.state, self.store, self.admin_ids)
        query.data = "admin:button_emoji:target:cart"
        with patch.object(Message, "edit_reply_markup", new_callable=AsyncMock):
            with patch.object(Message, "answer", new_callable=AsyncMock):
                await apply_button_emoji_target(query, self.state, self.store, self.admin_ids)
        overrides = json.loads(await self.store.get_setting("button_emoji_overrides"))
        self.assertNotIn("cart", overrides)


if __name__ == "__main__":
    unittest.main()
