import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Chat, Message, User
from cryptography.fernet import Fernet

from bot.database import Store, resolve_database_path
from bot.handlers import (
    _send_product_card,
    begin_checkout_discount_code,
    redeem_discount_code,
    return_to_main_menu,
    show_main_menu_command,
    show_levels,
)


class DatabasePathResolutionTests(unittest.TestCase):
    def test_relative_path_uses_persistent_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            self.assertEqual(
                resolve_database_path("bot_data.sqlite3", data_dir),
                data_dir / "bot_data.sqlite3",
            )

    def test_absolute_path_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "bot_data.sqlite3"
            self.assertEqual(resolve_database_path(database_path, temp_dir), database_path)


class CatalogGameSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp_dir.name) / "catalog.sqlite3", Fernet.generate_key().decode("ascii"))
        await self.store.initialize()
        await self.store.create_product(
            game="Free Fire",
            level="50",
            name="Single photo account",
            price_cents=3400,
            photo_file_ids=["single-photo"],
            account_email="one@example.com",
            account_password="one-password",
            created_by=1,
        )
        await self.store.register_user(123, "test", "test_user")
        await self.store.create_product(
            game="Free Fire",
            level="67",
            name="Album account",
            price_cents=4200,
            photo_file_ids=["album-photo-1", "album-photo-2"],
            account_email="two@example.com",
            account_password="two-password",
            created_by=1,
        )

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_selecting_game_sends_all_levels_and_purchase_actions(self) -> None:
        class BotDouble:
            def __init__(self) -> None:
                self.photos = []
                self.albums = []
                self.messages = []

            async def send_photo(self, **kwargs):
                self.photos.append(kwargs)

            async def send_media_group(self, **kwargs):
                self.albums.append(kwargs)

            async def send_message(self, *args, **kwargs):
                self.messages.append((args, kwargs))

        bot = BotDouble()
        message = Message(
            message_id=1,
            date=datetime.now(),
            chat=Chat(id=123, type=ChatType.PRIVATE),
        )
        query = SimpleNamespace(
            message=message,
            data="game:1",
            from_user=SimpleNamespace(id=123),
            bot=bot,
            answer=lambda *args, **kwargs: _completed(),
        )

        await show_levels(query, bot, self.store)

        self.assertEqual(len(bot.photos), 1)
        self.assertIn("គម្រោង:", bot.photos[0]["caption"])
        self.assertEqual(bot.photos[0]["reply_markup"].inline_keyboard[0][0].callback_data, "buy:1")
        self.assertEqual(len(bot.albums), 1)
        self.assertEqual(len(bot.albums[0]["media"]), 2)
        self.assertIn("គម្រោង:", bot.messages[0][0][1])
        self.assertEqual(bot.messages[0][1]["reply_markup"].inline_keyboard[0][0].callback_data, "buy:2")

    async def test_rejected_photo_falls_back_to_product_text_and_purchase_button(self) -> None:
        class BotDouble:
            def __init__(self) -> None:
                self.messages = []

            async def send_photo(self, **kwargs):
                raise TelegramBadRequest(method="sendPhoto", message="wrong file identifier specified")

            async def send_message(self, *args, **kwargs):
                self.messages.append((args, kwargs))

        bot = BotDouble()
        await _send_product_card(bot, 123, self.store, 1)

        self.assertEqual(len(bot.messages), 1)
        self.assertIn("Single photo account", bot.messages[0][0][1])
        self.assertEqual(
            bot.messages[0][1]["reply_markup"].inline_keyboard[0][0].callback_data,
            "buy:1",
        )

    async def test_back_to_menu_callback_restores_customer_reply_keyboard(self) -> None:
        message = Message(
            message_id=1,
            date=datetime.now(),
            chat=Chat(id=123, type=ChatType.PRIVATE),
        )
        query = SimpleNamespace(
            message=message,
            from_user=SimpleNamespace(id=123),
            answer=AsyncMock(),
        )

        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            await return_to_main_menu(query, self.store, frozenset())

        query.answer.assert_awaited_once_with()
        answer.assert_awaited_once()
        self.assertIn("reply_markup", answer.await_args.kwargs)

    async def test_menu_command_reopens_persistent_reply_keyboard(self) -> None:
        message = Message(
            message_id=1,
            date=datetime.now(),
            chat=Chat(id=123, type=ChatType.PRIVATE),
            from_user=User(id=123, is_bot=False, first_name="test"),
        )

        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            await show_main_menu_command(message, self.store, frozenset())

        keyboard = answer.await_args.kwargs["reply_markup"]
        self.assertFalse(keyboard.is_persistent)
        self.assertFalse(keyboard.one_time_keyboard)
        self.assertTrue(keyboard.resize_keyboard)

    async def test_checkout_discount_returns_to_same_payment_choices(self) -> None:
        class StateDouble:
            def __init__(self) -> None:
                self.data = {}
                self.current_state = None

            async def clear(self):
                self.data.clear()
                self.current_state = None

            async def set_state(self, state):
                self.current_state = state

            async def update_data(self, **kwargs):
                self.data.update(kwargs)

            async def get_data(self):
                return dict(self.data)

        await self.store.create_discount_code("SAVE10", 10, 1)
        state = StateDouble()
        callback_message = Message(
            message_id=2,
            date=datetime.now(),
            chat=Chat(id=123, type=ChatType.PRIVATE),
        )
        query = SimpleNamespace(
            message=callback_message,
            data="discount:1",
            from_user=SimpleNamespace(id=123),
            answer=AsyncMock(),
        )
        code_message = Message(
            message_id=3,
            date=datetime.now(),
            chat=Chat(id=123, type=ChatType.PRIVATE),
            from_user=User(id=123, is_bot=False, first_name="test"),
            text="SAVE10",
        )

        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            await begin_checkout_discount_code(query, state, self.store)
            await redeem_discount_code(code_message, state, self.store, frozenset())

        self.assertEqual(await self.store.user_language(123), "km")
        self.assertEqual(len(answer.await_args_list), 2)
        returned_keyboard = answer.await_args_list[-1].kwargs["reply_markup"].inline_keyboard
        self.assertEqual(returned_keyboard[0][0].callback_data, "pay:1:aba")
        self.assertEqual(returned_keyboard[0][1].callback_data, "pay:1:wallet")


async def _completed():
    return None


if __name__ == "__main__":
    unittest.main()