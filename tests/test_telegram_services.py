import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.enums import ChatType
from aiogram.types import Chat, Message, User
from cryptography.fernet import Fernet

from bot.database import Store
from bot.handlers import (
    AdminTelegramServiceForm,
    ServiceUsernameForm,
    add_ten_telegram_service_slots,
    begin_telegram_services,
    begin_telegram_service_option,
    begin_customer_bot_builder,
    buy_product,
    choose_telegram_service_kind,
    confirm_service_username,
    publish_telegram_service_options,
    receive_service_username,
    receive_service_identity_value,
    receive_telegram_service_name,
    receive_telegram_service_price,
    receive_customer_bot_token,
    show_topup_options,
)
from bot.handlers import CustomerBotBuilderForm
from bot.keyboards import admin_service_editor


class StateDouble:
    def __init__(self) -> None:
        self.data = {}
        self.current_state = None

    async def clear(self) -> None:
        self.data.clear()
        self.current_state = None

    async def set_state(self, state) -> None:
        self.current_state = state

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)

    async def get_data(self) -> dict:
        return dict(self.data)

    async def get_state(self):
        return self.current_state


def admin_message(text: str) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(),
        chat=Chat(id=100, type=ChatType.PRIVATE),
        from_user=User(id=100, is_bot=False, first_name="Admin"),
        text=text,
    )


class TelegramServiceFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp_dir.name) / "services.sqlite3", Fernet.generate_key().decode("ascii"))
        await self.store.initialize()
        await self.store.register_user(123, "Customer", "customer")

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_new_user_create_bot_starts_free_trial_setup(self) -> None:
        state = StateDouble()
        message = Message(
            message_id=5,
            date=datetime.now(),
            chat=Chat(id=123, type=ChatType.PRIVATE),
            from_user=User(id=123, is_bot=False, first_name="Customer"),
            text="បង្កើត bot ផ្ទាល់ខ្លួន",
        )
        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            await begin_customer_bot_builder(message, state, self.store)
        self.assertIn("Free trial", answer.await_args_list[0].args[0])
        self.assertIn("BotFather", answer.await_args_list[0].args[0])
        self.assertIn("/newbot", answer.await_args_list[0].args[0])
        button = answer.await_args_list[1].kwargs["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(button.url, "https://t.me/BotFather")
        self.assertEqual(state.current_state, "CustomerBotBuilderForm:token")
        self.assertTrue(state.data["customer_bot_free_trial"])

    async def test_customer_bot_cannot_start_nested_bot_creation(self) -> None:
        state = StateDouble()
        message = Message(
            message_id=9,
            date=datetime.now(),
            chat=Chat(id=123, type=ChatType.PRIVATE),
            from_user=User(id=123, is_bot=False, first_name="Customer"),
            text="បង្កើត bot ផ្ទាល់ខ្លួន",
        )
        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            await begin_customer_bot_builder(message, state, self.store, customer_bot_mode=True)

        self.assertIn("មិនអាចបង្កើត bot ផ្សេងទៀត", answer.await_args.args[0])
        self.assertIsNone(state.current_state)

    async def test_valid_format_token_immediately_advances_to_telegram_id_prompt(self) -> None:
        state = StateDouble()
        message = Message(
            message_id=6,
            date=datetime.now(),
            chat=Chat(id=123, type=ChatType.PRIVATE),
            from_user=User(id=123, is_bot=False, first_name="Customer"),
            text="123456789:abcdefghijklmnopqrstuvwxyz_ABCDEFGHIJKLMNOPQRST",
        )

        with patch.object(Message, "delete", new_callable=AsyncMock):
            with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
                with patch("bot.handlers.Bot") as customer_bot_factory:
                    await receive_customer_bot_token(message, state, self.store)

        customer_bot_factory.assert_not_called()
        self.assertEqual(state.data["customer_bot_username"], "")
        self.assertEqual(state.current_state, CustomerBotBuilderForm.admin_user_id)
        self.assertIn("Telegram User ID", answer.await_args.args[0])
        self.assertEqual(
            answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].url,
            "https://t.me/userinfo3bot",
        )

    async def test_malformed_token_is_not_deleted(self) -> None:
        message = Message(
            message_id=8,
            date=datetime.now(),
            chat=Chat(id=123, type=ChatType.PRIVATE),
            from_user=User(id=123, is_bot=False, first_name="Customer"),
            text="this is not a bot token",
        )
        with patch.object(Message, "delete", new_callable=AsyncMock) as delete:
            with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
                await receive_customer_bot_token(message, StateDouble(), self.store)

        delete.assert_not_awaited()
        self.assertIn("Token មិនត្រឹមត្រូវ", answer.await_args.args[0])

    async def test_admin_can_stage_options_in_numbered_slots_and_publish_them(self) -> None:
        state = StateDouble()
        admins = frozenset({100})
        editor_message = Message(
            message_id=10,
            date=datetime.now(),
            chat=Chat(id=100, type=ChatType.PRIVATE),
        )
        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            await begin_telegram_services(admin_message("services"), state, admins)
            await choose_telegram_service_kind(admin_message("Telegram Premium"), state, admins)
            self.assertEqual(len(answer.await_args.kwargs["reply_markup"].inline_keyboard), 13)

            first_slot = SimpleNamespace(
                from_user=User(id=100, is_bot=False, first_name="Admin"),
                data="admin:service:slot:0",
                message=editor_message,
                answer=AsyncMock(),
            )
            await begin_telegram_service_option(first_slot, state, admins)
            await receive_telegram_service_name(admin_message("3 ខែ"), state, admins)
            await receive_telegram_service_price(admin_message("5.00"), state, self.store, admins)

            second_slot = SimpleNamespace(
                from_user=User(id=100, is_bot=False, first_name="Admin"),
                data="admin:service:slot:1",
                message=editor_message,
                answer=AsyncMock(),
            )
            await begin_telegram_service_option(second_slot, state, admins)
            await receive_telegram_service_name(admin_message("12 ខែ"), state, admins)
            await receive_telegram_service_price(admin_message("18.00"), state, self.store, admins)

            add_ten_query = SimpleNamespace(
                from_user=User(id=100, is_bot=False, first_name="Admin"),
                message=editor_message,
                answer=AsyncMock(),
            )
            with patch.object(Message, "edit_reply_markup", new_callable=AsyncMock) as edit_markup:
                add_ten_query.data = "admin:service:add10"
                await add_ten_telegram_service_slots(add_ten_query, state, admins)
                expanded_grid = edit_markup.await_args.kwargs["reply_markup"]
                self.assertEqual(len(expanded_grid.inline_keyboard), 18)

            self.assertEqual(await self.store.service_games_with_stock(), [])
            publish_query = SimpleNamespace(
                from_user=User(id=100, is_bot=False, first_name="Admin"),
                message=editor_message,
                answer=AsyncMock(),
            )
            publish_query.data = "admin:service:publish"
            with patch.object(Message, "edit_reply_markup", new_callable=AsyncMock):
                await publish_telegram_service_options(publish_query, state, self.store, admins)

        categories = await self.store.service_games_with_stock()
        self.assertEqual(await self.store.games_with_stock(), [])
        premium = next(category for category in categories if category["name"] == "Telegram Premium")
        products = await self.store.products_for_game(premium["id"])
        self.assertEqual([product["name"] for product in products], ["12 ខែ", "3 ខែ"])
        self.assertEqual([product["price_cents"] for product in products], [1800, 500])
        self.assertTrue(all(product["service_kind"] == "telegram_premium" for product in products))
        self.assertIsNone(state.current_state)

    async def test_customer_topup_action_and_admin_publish_create_catalog_options(self) -> None:
        state = StateDouble()
        admins = frozenset({100})
        with patch.object(Message, "answer", new_callable=AsyncMock):
            await begin_telegram_services(admin_message("services"), state, admins)
            await choose_telegram_service_kind(admin_message("Telegram Premium"), state, admins)
            editor_message = Message(
                message_id=11,
                date=datetime.now(),
                chat=Chat(id=100, type=ChatType.PRIVATE),
            )
            query = SimpleNamespace(
                from_user=User(id=100, is_bot=False, first_name="Admin"),
                data="admin:service:slot:0",
                message=editor_message,
                answer=AsyncMock(),
            )
            await begin_telegram_service_option(query, state, admins)
            await receive_telegram_service_name(admin_message("12 ខែ"), state, admins)
            await receive_telegram_service_price(admin_message("32.50"), state, self.store, admins)

        self.assertEqual(await self.store.service_games_with_stock(), [])
        publish_query = SimpleNamespace(
            from_user=User(id=100, is_bot=False, first_name="Admin"),
            data="admin:service:publish",
            message=editor_message,
            answer=AsyncMock(),
        )
        with patch.object(Message, "edit_reply_markup", new_callable=AsyncMock):
            with patch.object(Message, "answer", new_callable=AsyncMock):
                await publish_telegram_service_options(publish_query, state, self.store, admins)

        categories = await self.store.service_games_with_stock()
        premium = next(category for category in categories if category["name"] == "Telegram Premium")
        products = await self.store.products_for_game(premium["id"])
        self.assertEqual([product["name"] for product in products], ["12 ខែ"])
        self.assertEqual([product["price_cents"] for product in products], [3250])
        self.assertTrue(all(product["service_kind"] == "telegram_premium" for product in products))
        self.assertIsNone(state.current_state)
        from bot.keyboards import product_actions

        action = product_actions(products[0]["id"], is_service=True).inline_keyboard[0][0]
        self.assertEqual(action.text, "បញ្ចូលឥឡូវនេះ")
        self.assertEqual(action.callback_data, f"buy:{products[0]['id']}")

    async def test_customer_must_confirm_username_before_payment_options(self) -> None:
        service_id = await self.store.create_product(
            game="Telegram Stars",
            level="សេវាកម្ម",
            name="100 Stars",
            price_cents=250,
            photo_file_ids=[],
            account_email="",
            account_password="",
            created_by=100,
            service_kind="telegram_stars",
        )
        state = StateDouble()
        user = User(id=123, is_bot=False, first_name="Customer")
        buy_query = SimpleNamespace(
            message=Message(
                message_id=2,
                date=datetime.now(),
                chat=Chat(id=123, type=ChatType.PRIVATE),
            ),
            data=f"buy:{service_id}",
            from_user=user,
            answer=AsyncMock(),
        )
        username_message = Message(
            message_id=3,
            date=datetime.now(),
            chat=Chat(id=123, type=ChatType.PRIVATE),
            from_user=user,
            text="@valid_user123",
        )
        confirm_query = SimpleNamespace(
            message=buy_query.message,
            data="service:confirm",
            from_user=user,
            answer=AsyncMock(),
        )

        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            await buy_product(buy_query, self.store, state)
            self.assertEqual(state.current_state, ServiceUsernameForm.username)
            self.assertFalse(answer.await_args.kwargs.get("reply_markup"))

            await receive_service_username(username_message, state, self.store)
            self.assertEqual(state.current_state, ServiceUsernameForm.confirm)
            confirmation = answer.await_args.kwargs["reply_markup"].inline_keyboard
            self.assertEqual(confirmation[0][0].callback_data, "service:confirm")
            self.assertEqual(confirmation[1][0].callback_data, "service:edit")

            await confirm_service_username(confirm_query, state, self.store)

        payment_buttons = answer.await_args.kwargs["reply_markup"].inline_keyboard
        self.assertEqual(payment_buttons[0][0].callback_data, f"pay:{service_id}:aba")
        self.assertEqual(payment_buttons[0][1].callback_data, f"pay:{service_id}:wallet")
        self.assertEqual(state.data["service_username"], "@valid_user123")
        self.assertIsNone(state.current_state)

        from bot.keyboards import product_actions

        self.assertEqual(product_actions(service_id, is_service=True).inline_keyboard[0][0].text, "បញ្ចូលឥឡូវនេះ")

    async def test_service_category_shows_package_price_buttons_not_product_cards(self) -> None:
        for name, price in (("Premium 6 ខែ", 1780), ("Premium 12 ខែ", 3250)):
            await self.store.create_product(
                game="Telegram Premium",
                level="សេវាកម្ម",
                name=name,
                price_cents=price,
                photo_file_ids=[],
                account_email="",
                account_password="",
                created_by=100,
                service_kind="telegram_premium",
            )
        category = (await self.store.service_games_with_stock())[0]
        user = User(id=123, is_bot=False, first_name="Customer")
        query = SimpleNamespace(
            message=Message(
                message_id=20,
                date=datetime.now(),
                chat=Chat(id=123, type=ChatType.PRIVATE),
            ),
            data=f"topup:game:{category['id']}",
            from_user=user,
            answer=AsyncMock(),
        )

        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            with patch("bot.handlers._send_product_card", new=AsyncMock()) as send_card:
                await show_topup_options(query, self.store)

        send_card.assert_not_awaited()
        answer.assert_awaited_once()
        keyboard = answer.await_args.kwargs["reply_markup"].inline_keyboard
        self.assertEqual(len(keyboard), 3)
        option_labels = [row[0].text for row in keyboard[:-1]]
        self.assertIn("$17.80", option_labels[0])
        self.assertIn("$32.50", option_labels[1])
        self.assertTrue(keyboard[0][0].callback_data.startswith("buy:"))
        self.assertEqual(keyboard[-1][0].callback_data, "topup")

    async def test_game_service_collects_two_ids_before_confirmation(self) -> None:
        service_id = await self.store.create_product(
            game="Mobile Legends",
            level="Top up",
            name="100 Diamonds",
            price_cents=500,
            photo_file_ids=["mobile-service-photo"],
            account_email="",
            account_password="",
            created_by=100,
            service_kind="game_service",
            identity_fields=["Game ID", "Zone/Server ID"],
        )
        state = StateDouble()
        await state.update_data(
            service_product_id=service_id,
            service_username="@valid_user123",
            service_identity_fields=["Game ID", "Zone/Server ID"],
            service_identity_values={},
            service_identity_index=0,
        )
        await state.set_state(ServiceUsernameForm.identity_value)
        user = User(id=123, is_bot=False, first_name="Customer")

        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            first = Message(
                message_id=30,
                date=datetime.now(),
                chat=Chat(id=123, type=ChatType.PRIVATE),
                from_user=user,
                text="987654321",
            )
            await receive_service_identity_value(first, state, self.store)
            self.assertEqual(state.current_state, ServiceUsernameForm.identity_value)
            self.assertIn("Zone/Server ID", answer.await_args.args[0])

            second = Message(
                message_id=31,
                date=datetime.now(),
                chat=Chat(id=123, type=ChatType.PRIVATE),
                from_user=user,
                text="1234",
            )
            await receive_service_identity_value(second, state, self.store)

        self.assertEqual(state.current_state, ServiceUsernameForm.confirm)
        self.assertIn("987654321", answer.await_args.args[0])
        self.assertIn("1234", answer.await_args.args[0])
        self.assertEqual(state.data["service_identity_values"], {"Game ID": "987654321", "Zone/Server ID": "1234"})

    async def test_discount_code_round_trip_keeps_service_identity_values(self) -> None:
        from bot.handlers import begin_checkout_discount_code, redeem_discount_code

        await self.store.create_discount_code("GAME10", 10, 100)
        state = StateDouble()
        await state.update_data(
            service_product_id=7,
            service_username="@valid_user123",
            service_identity_fields=["Game ID", "Zone/Server ID"],
            service_identity_values={"Game ID": "987654321", "Zone/Server ID": "1234"},
        )
        query = SimpleNamespace(
            message=Message(
                message_id=40,
                date=datetime.now(),
                chat=Chat(id=123, type=ChatType.PRIVATE),
            ),
            data="discount:7",
            from_user=User(id=123, is_bot=False, first_name="Customer"),
            answer=AsyncMock(),
        )
        message = Message(
            message_id=41,
            date=datetime.now(),
            chat=Chat(id=123, type=ChatType.PRIVATE),
            from_user=User(id=123, is_bot=False, first_name="Customer"),
            text="GAME10",
        )
        with patch.object(Message, "answer", new_callable=AsyncMock):
            await begin_checkout_discount_code(query, state, self.store)
            await redeem_discount_code(message, state, self.store, frozenset())
        self.assertEqual(state.data["service_username"], "@valid_user123")
        self.assertEqual(state.data["service_identity_values"], {"Game ID": "987654321", "Zone/Server ID": "1234"})
        self.assertEqual(state.data["service_identity_fields"], ["Game ID", "Zone/Server ID"])


if __name__ == "__main__":
    unittest.main()