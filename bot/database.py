import json
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from cryptography.fernet import Fernet

ORDER_RESERVATION_MINUTES = 3
CUSTOMER_BOT_PLANS = {
    "monthly": (250, 30),
    "two_months": (350, 60),
    "yearly": (1500, 365),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def resolve_database_path(path: str | Path, data_dir: str | Path = "/data") -> Path:
    database_path = Path(path).expanduser()
    persistent_dir = Path(data_dir)
    if database_path.is_absolute() or not persistent_dir.is_dir():
        return database_path
    return persistent_dir / database_path


class Store:
    def __init__(self, path: str | Path, encryption_key: str) -> None:
        self.path = str(path)
        self._cipher = Fernet(encryption_key.encode("ascii"))

    async def initialize(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA foreign_keys=ON")
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    first_name TEXT NOT NULL DEFAULT '',
                    username TEXT,
                    active_discount_code TEXT,
                    language TEXT NOT NULL DEFAULT 'km' CHECK(language IN ('km', 'en')),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discount_codes (
                    code TEXT PRIMARY KEY,
                    percent INTEGER NOT NULL CHECK(percent BETWEEN 1 AND 90),
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bot_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    name_html TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS levels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL REFERENCES games(id),
                    name TEXT NOT NULL COLLATE NOCASE,
                    name_html TEXT NOT NULL DEFAULT '',
                    UNIQUE(game_id, name)
                );
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL REFERENCES games(id),
                    level_id INTEGER NOT NULL REFERENCES levels(id),
                    name TEXT NOT NULL,
                    name_html TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    description_html TEXT NOT NULL DEFAULT '',
                    price_cents INTEGER NOT NULL CHECK(price_cents > 0),
                    photo_file_ids TEXT NOT NULL,
                    account_email_encrypted TEXT NOT NULL,
                    account_password_encrypted TEXT NOT NULL,
                    delivery_file_id TEXT NOT NULL DEFAULT '',
                    delivery_url TEXT NOT NULL DEFAULT '',
                    service_kind TEXT NOT NULL DEFAULT '',
                    identity_fields TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'available'
                        CHECK(status IN ('available', 'reserved', 'sold')),
                    reserved_order_id INTEGER,
                    is_listed INTEGER NOT NULL DEFAULT 1,
                    created_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS products_by_level_status
                    ON products(level_id, status);
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER NOT NULL REFERENCES products(id),
                    user_id INTEGER NOT NULL REFERENCES users(user_id),
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'paid', 'cancelled', 'expired')),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    payment_tx_id TEXT UNIQUE,
                    amount_cents INTEGER NOT NULL DEFAULT 0,
                    discount_code TEXT,
                    telegram_username TEXT NOT NULL DEFAULT '',
                    identity_values TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS wallet_accounts (
                    user_id INTEGER PRIMARY KEY REFERENCES users(user_id),
                    balance_cents INTEGER NOT NULL DEFAULT 0 CHECK(balance_cents >= 0)
                );
                CREATE TABLE IF NOT EXISTS wallet_topups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(user_id),
                    amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
                    credit_cents INTEGER NOT NULL CHECK(credit_cents >= amount_cents),
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'paid', 'expired', 'cancelled')),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    payment_tx_id TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS customer_bots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id INTEGER NOT NULL REFERENCES users(user_id),
                    bot_token_encrypted TEXT NOT NULL,
                    bot_token_hash TEXT NOT NULL,
                    bot_username TEXT NOT NULL,
                    khqr_profile_id TEXT NOT NULL,
                    khqr_secret_encrypted TEXT NOT NULL,
                    admin_user_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'active', 'expired', 'cancelled')),
                    payment_tx_id TEXT UNIQUE,
                    amount_cents INTEGER NOT NULL DEFAULT 250,
                    payment_expires_at TEXT NOT NULL,
                    subscription_expires_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS customer_bot_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id INTEGER NOT NULL REFERENCES users(user_id),
                    plan_key TEXT NOT NULL CHECK(plan_key IN ('monthly', 'two_months', 'yearly')),
                    amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
                    duration_days INTEGER NOT NULL CHECK(duration_days > 0),
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'paid', 'setup', 'activated', 'expired', 'cancelled')),
                    payment_tx_id TEXT UNIQUE,
                    payment_expires_at TEXT NOT NULL,
                    customer_bot_id INTEGER REFERENCES customer_bots(id),
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS orders_by_status_expiration
                    ON orders(status, expires_at);
                CREATE INDEX IF NOT EXISTS wallet_topups_by_status_expiration
                    ON wallet_topups(status, expires_at);
                CREATE INDEX IF NOT EXISTS customer_bots_by_status_expiration
                    ON customer_bots(status, subscription_expires_at);
                CREATE INDEX IF NOT EXISTS customer_bot_orders_by_status_expiration
                    ON customer_bot_orders(status, payment_expires_at);
                """
            )
            product_columns = {
                row[1] for row in await (await db.execute("PRAGMA table_info(products)")).fetchall()
            }
            if "name_html" not in product_columns:
                await db.execute("ALTER TABLE products ADD COLUMN name_html TEXT NOT NULL DEFAULT ''")
            if "description" not in product_columns:
                await db.execute("ALTER TABLE products ADD COLUMN description TEXT NOT NULL DEFAULT ''")
            if "description_html" not in product_columns:
                await db.execute("ALTER TABLE products ADD COLUMN description_html TEXT NOT NULL DEFAULT ''")
            if "is_listed" not in product_columns:
                await db.execute("ALTER TABLE products ADD COLUMN is_listed INTEGER NOT NULL DEFAULT 1")
            if "delivery_file_id" not in product_columns:
                await db.execute("ALTER TABLE products ADD COLUMN delivery_file_id TEXT NOT NULL DEFAULT ''")
            if "delivery_url" not in product_columns:
                await db.execute("ALTER TABLE products ADD COLUMN delivery_url TEXT NOT NULL DEFAULT ''")
            if "service_kind" not in product_columns:
                await db.execute("ALTER TABLE products ADD COLUMN service_kind TEXT NOT NULL DEFAULT ''")
            if "identity_fields" not in product_columns:
                await db.execute("ALTER TABLE products ADD COLUMN identity_fields TEXT NOT NULL DEFAULT '[]'")
            game_columns = {
                row[1] for row in await (await db.execute("PRAGMA table_info(games)")).fetchall()
            }
            if "name_html" not in game_columns:
                await db.execute("ALTER TABLE games ADD COLUMN name_html TEXT NOT NULL DEFAULT ''")
            level_columns = {
                row[1] for row in await (await db.execute("PRAGMA table_info(levels)")).fetchall()
            }
            if "name_html" not in level_columns:
                await db.execute("ALTER TABLE levels ADD COLUMN name_html TEXT NOT NULL DEFAULT ''")
            order_columns = {
                row[1] for row in await (await db.execute("PRAGMA table_info(orders)")).fetchall()
            }
            if "payment_tx_id" not in order_columns:
                await db.execute("ALTER TABLE orders ADD COLUMN payment_tx_id TEXT")
            if "amount_cents" not in order_columns:
                await db.execute("ALTER TABLE orders ADD COLUMN amount_cents INTEGER NOT NULL DEFAULT 0")
            if "discount_code" not in order_columns:
                await db.execute("ALTER TABLE orders ADD COLUMN discount_code TEXT")
            if "telegram_username" not in order_columns:
                await db.execute("ALTER TABLE orders ADD COLUMN telegram_username TEXT NOT NULL DEFAULT ''")
            if "identity_values" not in order_columns:
                await db.execute("ALTER TABLE orders ADD COLUMN identity_values TEXT NOT NULL DEFAULT '{}'")
            user_columns = {
                row[1] for row in await (await db.execute("PRAGMA table_info(users)")).fetchall()
            }
            if "active_discount_code" not in user_columns:
                await db.execute("ALTER TABLE users ADD COLUMN active_discount_code TEXT")
            if "language" not in user_columns:
                await db.execute("ALTER TABLE users ADD COLUMN language TEXT NOT NULL DEFAULT 'km'")
            await db.execute("UPDATE orders SET amount_cents = (SELECT price_cents FROM products WHERE products.id = orders.product_id) WHERE amount_cents = 0")
            await db.commit()

    async def register_user(self, user_id: int, first_name: str, username: str | None) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO users(user_id, first_name, username, created_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       first_name=excluded.first_name,
                       username=excluded.username""",
                (user_id, first_name, username, _timestamp(_utc_now())),
            )
            await db.execute(
                "INSERT INTO wallet_accounts(user_id) VALUES (?) ON CONFLICT(user_id) DO NOTHING",
                (user_id,),
            )
            await db.commit()

    async def user_ids(self) -> list[int]:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT user_id FROM users ORDER BY user_id")
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def user_language(self, user_id: int) -> str:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT language FROM users WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            return row[0] if row and row[0] in {"km", "en"} else "km"

    async def set_user_language(self, user_id: int, language: str) -> bool:
        if language not in {"km", "en"}:
            return False
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "UPDATE users SET language = ? WHERE user_id = ?",
                (language, user_id),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def get_setting(self, key: str, default: str = "") -> str:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT setting_value FROM bot_settings WHERE setting_key = ?",
                (key,),
            )
            row = await cursor.fetchone()
            return str(row[0]) if row else default

    async def set_setting(self, key: str, value: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO bot_settings(setting_key, setting_value) VALUES (?, ?) "
                "ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value",
                (key, value),
            )
            await db.commit()

    async def get_secret_setting(self, key: str, default: str = "") -> str:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT setting_value FROM bot_settings WHERE setting_key = ?",
                (f"secret:{key}",),
            )
            row = await cursor.fetchone()
        if row is None:
            return default
        return self._cipher.decrypt(str(row[0]).encode("ascii")).decode("utf-8")

    async def set_secret_setting(self, key: str, value: str) -> None:
        encrypted = self._cipher.encrypt(value.encode("utf-8")).decode("ascii")
        await self.set_setting(f"secret:{key}", encrypted)

    async def create_customer_bot_order(
        self,
        owner_user_id: int,
        plan_key: str,
        payment_tx_id: str,
    ) -> dict[str, Any]:
        plan = CUSTOMER_BOT_PLANS.get(plan_key)
        if plan is None:
            raise ValueError("Unsupported customer bot plan.")
        amount_cents, duration_days = plan
        now = _utc_now()
        expires_at = now + timedelta(minutes=ORDER_RESERVATION_MINUTES)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """INSERT INTO customer_bot_orders(
                       owner_user_id, plan_key, amount_cents, duration_days,
                       payment_tx_id, payment_expires_at, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    owner_user_id,
                    plan_key,
                    amount_cents,
                    duration_days,
                    payment_tx_id,
                    _timestamp(expires_at),
                    _timestamp(now),
                ),
            )
            order_id = int(cursor.lastrowid)
            await db.commit()
            return {
                "id": order_id,
                "owner_user_id": owner_user_id,
                "plan_key": plan_key,
                "amount_cents": amount_cents,
                "duration_days": duration_days,
                "payment_tx_id": payment_tx_id,
                "payment_expires_at": _timestamp(expires_at),
            }

    async def pending_customer_bot_orders(self) -> list[dict[str, Any]]:
        now = _timestamp(_utc_now())
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT id, owner_user_id, plan_key, amount_cents, duration_days, payment_tx_id,
                          payment_expires_at
                   FROM customer_bot_orders
                   WHERE status = 'pending' AND payment_expires_at > ?""",
                (now,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def customer_bot_setup_orders(self, owner_user_id: int) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT id, plan_key, amount_cents, duration_days, status
                   FROM customer_bot_orders
                   WHERE owner_user_id = ? AND status IN ('paid', 'setup')
                   ORDER BY id DESC""",
                (owner_user_id,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def mark_customer_bot_order_paid(
        self,
        order_id: int,
        verified_amount_cents: int,
    ) -> dict[str, Any] | None:
        now = _timestamp(_utc_now())
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """SELECT * FROM customer_bot_orders
                   WHERE id = ? AND status = 'pending' AND payment_expires_at > ?""",
                (order_id, now),
            )
            order = _row_to_dict(await cursor.fetchone())
            if order is None or int(order["amount_cents"]) != verified_amount_cents:
                await db.rollback()
                return None
            await db.execute(
                "UPDATE customer_bot_orders SET status = 'paid' WHERE id = ?",
                (order_id,),
            )
            await db.commit()
            return order

    async def prepare_customer_bot_setup(self, order_id: int, owner_user_id: int) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """SELECT * FROM customer_bot_orders
                   WHERE id = ? AND owner_user_id = ? AND status IN ('paid', 'setup')""",
                (order_id, owner_user_id),
            )
            order = _row_to_dict(await cursor.fetchone())
            if order is None:
                await db.rollback()
                return None
            await db.execute(
                "UPDATE customer_bot_orders SET status = 'setup' WHERE id = ? AND status = 'paid'",
                (order_id,),
            )
            await db.commit()
            order["status"] = "setup"
            return order

    async def activate_paid_customer_bot_order(
        self,
        order_id: int,
        owner_user_id: int,
        *,
        bot_token: str,
        bot_username: str,
        khqr_profile_id: str,
        khqr_secret_key: str,
        admin_user_id: int,
    ) -> dict[str, Any] | None:
        token_hash = hashlib.sha256(bot_token.encode("utf-8")).hexdigest()
        token_encrypted = self._cipher.encrypt(bot_token.encode("utf-8")).decode("ascii")
        secret_encrypted = self._cipher.encrypt(khqr_secret_key.encode("utf-8")).decode("ascii")
        now = _utc_now()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """SELECT * FROM customer_bot_orders
                   WHERE id = ? AND owner_user_id = ? AND status = 'setup'""",
                (order_id, owner_user_id),
            )
            order = _row_to_dict(await cursor.fetchone())
            if order is None:
                await db.rollback()
                return None
            cursor = await db.execute(
                "SELECT id, status FROM customer_bots WHERE bot_token_hash = ?",
                (token_hash,),
            )
            existing = await cursor.fetchone()
            if existing and existing["status"] in {"pending", "active"}:
                await db.rollback()
                raise ValueError("This Telegram bot already has a pending or active subscription.")
            expires_at = now + timedelta(days=int(order["duration_days"]))
            if existing:
                bot_id = int(existing["id"])
                await db.execute(
                    """UPDATE customer_bots SET
                           owner_user_id = ?, bot_token_encrypted = ?, bot_username = ?,
                           khqr_profile_id = ?, khqr_secret_encrypted = ?, admin_user_id = ?,
                           status = 'active', payment_tx_id = NULL, amount_cents = ?,
                           payment_expires_at = ?, subscription_expires_at = ?, created_at = ?
                       WHERE id = ?""",
                    (
                        owner_user_id,
                        token_encrypted,
                        bot_username,
                        khqr_profile_id,
                        secret_encrypted,
                        admin_user_id,
                        order["amount_cents"],
                        _timestamp(now),
                        _timestamp(expires_at),
                        _timestamp(now),
                        bot_id,
                    ),
                )
            else:
                cursor = await db.execute(
                    """INSERT INTO customer_bots(
                           owner_user_id, bot_token_encrypted, bot_token_hash, bot_username,
                           khqr_profile_id, khqr_secret_encrypted, admin_user_id, status,
                           payment_tx_id, amount_cents, payment_expires_at,
                           subscription_expires_at, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', NULL, ?, ?, ?, ?)""",
                    (
                        owner_user_id,
                        token_encrypted,
                        token_hash,
                        bot_username,
                        khqr_profile_id,
                        secret_encrypted,
                        admin_user_id,
                        order["amount_cents"],
                        _timestamp(now),
                        _timestamp(expires_at),
                        _timestamp(now),
                    ),
                )
                bot_id = int(cursor.lastrowid)
            await db.execute(
                "UPDATE customer_bot_orders SET status = 'activated', customer_bot_id = ? WHERE id = ?",
                (bot_id, order_id),
            )
            await db.commit()
        return {
            "id": bot_id,
            "owner_user_id": owner_user_id,
            "bot_username": bot_username,
            "plan_key": order["plan_key"],
            "amount_cents": int(order["amount_cents"]),
            "duration_days": int(order["duration_days"]),
            "subscription_expires_at": _timestamp(expires_at),
        }

    async def expire_pending_customer_bot_orders(self) -> list[dict[str, int]]:
        now = _timestamp(_utc_now())
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT id, owner_user_id FROM customer_bot_orders
                   WHERE status = 'pending' AND payment_expires_at <= ?""",
                (now,),
            )
            expired = [dict(row) for row in await cursor.fetchall()]
            await db.execute(
                "UPDATE customer_bot_orders SET status = 'expired' WHERE status = 'pending' AND payment_expires_at <= ?",
                (now,),
            )
            await db.commit()
            return expired

    async def cancel_customer_bot_order(self, order_id: int, owner_user_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """UPDATE customer_bot_orders SET status = 'cancelled'
                   WHERE id = ? AND owner_user_id = ? AND status = 'pending'""",
                (order_id, owner_user_id),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def create_customer_bot_subscription(
        self,
        *,
        owner_user_id: int,
        bot_token: str,
        bot_username: str,
        khqr_profile_id: str,
        khqr_secret_key: str,
        admin_user_id: int,
        payment_tx_id: str,
    ) -> int:
        token_hash = hashlib.sha256(bot_token.encode("utf-8")).hexdigest()
        token_encrypted = self._cipher.encrypt(bot_token.encode("utf-8")).decode("ascii")
        secret_encrypted = self._cipher.encrypt(khqr_secret_key.encode("utf-8")).decode("ascii")
        now = _utc_now()
        payment_expires_at = now + timedelta(minutes=ORDER_RESERVATION_MINUTES)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT id, status FROM customer_bots WHERE bot_token_hash = ?",
                (token_hash,),
            )
            existing = await cursor.fetchone()
            if existing and existing["status"] in {"pending", "active"}:
                await db.rollback()
                raise ValueError("This Telegram bot already has a pending or active subscription.")
            if existing:
                bot_id = int(existing["id"])
                await db.execute(
                    """UPDATE customer_bots SET
                           owner_user_id = ?, bot_token_encrypted = ?, bot_username = ?,
                           khqr_profile_id = ?, khqr_secret_encrypted = ?, admin_user_id = ?,
                           status = 'pending', payment_tx_id = ?, amount_cents = 250,
                           payment_expires_at = ?, subscription_expires_at = NULL, created_at = ?
                       WHERE id = ?""",
                    (
                        owner_user_id,
                        token_encrypted,
                        bot_username,
                        khqr_profile_id,
                        secret_encrypted,
                        admin_user_id,
                        payment_tx_id,
                        _timestamp(payment_expires_at),
                        _timestamp(now),
                        bot_id,
                    ),
                )
                await db.commit()
                return bot_id
            cursor = await db.execute(
                """INSERT INTO customer_bots(
                       owner_user_id, bot_token_encrypted, bot_token_hash, bot_username,
                       khqr_profile_id, khqr_secret_encrypted, admin_user_id,
                       payment_tx_id, payment_expires_at, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    owner_user_id,
                    token_encrypted,
                    token_hash,
                    bot_username,
                    khqr_profile_id,
                    secret_encrypted,
                    admin_user_id,
                    payment_tx_id,
                    _timestamp(payment_expires_at),
                    _timestamp(now),
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def customer_bot_trial_available(self, owner_user_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM customer_bots WHERE owner_user_id = ? LIMIT 1",
                (owner_user_id,),
            )
            return await cursor.fetchone() is None

    async def create_first_customer_bot_trial(
        self,
        *,
        owner_user_id: int,
        bot_token: str,
        bot_username: str,
        khqr_profile_id: str,
        khqr_secret_key: str,
        admin_user_id: int,
    ) -> dict[str, Any] | None:
        token_hash = hashlib.sha256(bot_token.encode("utf-8")).hexdigest()
        token_encrypted = self._cipher.encrypt(bot_token.encode("utf-8")).decode("ascii")
        secret_encrypted = self._cipher.encrypt(khqr_secret_key.encode("utf-8")).decode("ascii")
        now = _utc_now()
        expires_at = now + timedelta(days=7)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT 1 FROM customer_bots WHERE owner_user_id = ? LIMIT 1",
                (owner_user_id,),
            )
            if await cursor.fetchone() is not None:
                await db.rollback()
                return None
            cursor = await db.execute(
                "SELECT id, status FROM customer_bots WHERE bot_token_hash = ?",
                (token_hash,),
            )
            existing = await cursor.fetchone()
            if existing and existing["status"] in {"pending", "active"}:
                await db.rollback()
                raise ValueError("This Telegram bot already has a pending or active subscription.")
            if existing:
                bot_id = int(existing["id"])
                await db.execute(
                    """UPDATE customer_bots SET
                           owner_user_id = ?, bot_token_encrypted = ?, bot_username = ?,
                           khqr_profile_id = ?, khqr_secret_encrypted = ?, admin_user_id = ?,
                           status = 'active', payment_tx_id = NULL, amount_cents = 250,
                           payment_expires_at = ?, subscription_expires_at = ?, created_at = ?
                       WHERE id = ?""",
                    (
                        owner_user_id,
                        token_encrypted,
                        bot_username,
                        khqr_profile_id,
                        secret_encrypted,
                        admin_user_id,
                        _timestamp(now),
                        _timestamp(expires_at),
                        _timestamp(now),
                        bot_id,
                    ),
                )
            else:
                cursor = await db.execute(
                    """INSERT INTO customer_bots(
                           owner_user_id, bot_token_encrypted, bot_token_hash, bot_username,
                           khqr_profile_id, khqr_secret_encrypted, admin_user_id, status,
                           amount_cents, payment_expires_at, subscription_expires_at, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 250, ?, ?, ?)""",
                    (
                        owner_user_id,
                        token_encrypted,
                        token_hash,
                        bot_username,
                        khqr_profile_id,
                        secret_encrypted,
                        admin_user_id,
                        _timestamp(now),
                        _timestamp(expires_at),
                        _timestamp(now),
                    ),
                )
                bot_id = int(cursor.lastrowid)
            await db.commit()
        return {
            "id": bot_id,
            "owner_user_id": owner_user_id,
            "bot_username": bot_username,
            "subscription_expires_at": _timestamp(expires_at),
        }

    async def pending_customer_bots(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT id, owner_user_id, payment_tx_id, amount_cents, payment_expires_at
                   FROM customer_bots WHERE status = 'pending' AND payment_expires_at > ?""",
                (_timestamp(_utc_now()),),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def expire_pending_customer_bots(self) -> list[dict[str, int]]:
        now = _timestamp(_utc_now())
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT id, owner_user_id FROM customer_bots
                   WHERE status = 'pending' AND payment_expires_at <= ?""",
                (now,),
            )
            expired = [dict(row) for row in await cursor.fetchall()]
            await db.execute(
                """UPDATE customer_bots SET status = 'cancelled',
                       bot_token_encrypted = '', khqr_secret_encrypted = ''
                   WHERE status = 'pending' AND payment_expires_at <= ?""",
                (now,),
            )
            await db.commit()
            return expired

    async def cancel_customer_bot(self, bot_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """UPDATE customer_bots SET status = 'cancelled',
                       bot_token_encrypted = '', khqr_secret_encrypted = ''
                   WHERE id = ? AND status = 'pending'""",
                (bot_id,),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def activate_customer_bot(
        self,
        bot_id: int,
        verified_amount_cents: int,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """SELECT * FROM customer_bots
                   WHERE id = ? AND status = 'pending' AND payment_expires_at > ?""",
                (bot_id, _timestamp(now)),
            )
            record = _row_to_dict(await cursor.fetchone())
            if record is None or int(record["amount_cents"]) != verified_amount_cents:
                await db.rollback()
                return None
            expires_at = now + timedelta(days=30)
            await db.execute(
                """UPDATE customer_bots SET status = 'active', subscription_expires_at = ?
                   WHERE id = ?""",
                (_timestamp(expires_at), bot_id),
            )
            await db.commit()
        assert record is not None
        record["bot_token"] = self._cipher.decrypt(record.pop("bot_token_encrypted").encode("ascii")).decode("utf-8")
        record["khqr_secret_key"] = self._cipher.decrypt(
            record.pop("khqr_secret_encrypted").encode("ascii")
        ).decode("utf-8")
        record["subscription_expires_at"] = _timestamp(expires_at)
        record["status"] = "active"
        return record

    async def active_customer_bots(self) -> list[dict[str, Any]]:
        now = _timestamp(_utc_now())
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM customer_bots
                   WHERE status = 'active' AND subscription_expires_at > ?""",
                (now,),
            )
            records = [dict(row) for row in await cursor.fetchall()]
        for record in records:
            record["bot_token"] = self._cipher.decrypt(record.pop("bot_token_encrypted").encode("ascii")).decode("utf-8")
            record["khqr_secret_key"] = self._cipher.decrypt(
                record.pop("khqr_secret_encrypted").encode("ascii")
            ).decode("utf-8")
        return records

    async def expire_customer_bots(self) -> list[dict[str, int]]:
        now = _timestamp(_utc_now())
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT id, owner_user_id FROM customer_bots
                   WHERE status = 'active' AND subscription_expires_at <= ?""",
                (now,),
            )
            expired_bots = [dict(row) for row in await cursor.fetchall()]
            cursor = await db.execute(
                """UPDATE customer_bots SET status = 'expired',
                       bot_token_encrypted = '', khqr_secret_encrypted = ''
                   WHERE status = 'active' AND subscription_expires_at <= ?""",
                (now,),
            )
            await db.commit()
            return expired_bots

    async def create_discount_code(self, code: str, percent: int, created_by: int) -> bool:
        try:
            async with aiosqlite.connect(self.path) as db:
                await db.execute(
                    "INSERT INTO discount_codes(code, percent, created_by, created_at) VALUES (?, ?, ?, ?)",
                    (code.upper(), percent, created_by, _timestamp(_utc_now())),
                )
                await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def apply_discount_code(self, user_id: int, code: str) -> int | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT percent FROM discount_codes WHERE code = ? AND is_active = 1",
                (code.upper(),),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            await db.execute(
                "UPDATE users SET active_discount_code = ? WHERE user_id = ?",
                (code.upper(), user_id),
            )
            await db.commit()
            return int(row[0])

    async def create_product(
        self,
        *,
        game: str,
        level: str,
        name: str,
        price_cents: int,
        photo_file_ids: list[str],
        account_email: str,
        account_password: str,
        created_by: int,
        game_html: str = "",
        level_html: str = "",
        name_html: str = "",
        description: str = "",
        description_html: str = "",
        delivery_file_id: str = "",
        delivery_url: str = "",
        service_kind: str = "",
        identity_fields: list[str] | None = None,
    ) -> int:
        if service_kind not in {"", "telegram_premium", "telegram_stars", "game_service"}:
            raise ValueError("Unsupported service kind.")
        identity_fields = identity_fields or []
        if len(identity_fields) > 2 or any(not field.strip() or len(field) > 40 for field in identity_fields):
            raise ValueError("Service identity fields must contain up to two short labels.")
        if not photo_file_ids and not service_kind:
            raise ValueError("A product must have at least one photo.")
        if price_cents <= 0:
            raise ValueError("Price must be greater than zero.")

        email_encrypted = self._cipher.encrypt(account_email.encode("utf-8")).decode("ascii")
        password_encrypted = self._cipher.encrypt(account_password.encode("utf-8")).decode("ascii")
        now = _timestamp(_utc_now())

        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """INSERT INTO games(name, name_html) VALUES (?, ?)
                   ON CONFLICT(name) DO UPDATE SET name_html =
                       CASE WHEN excluded.name_html != '' THEN excluded.name_html ELSE games.name_html END""",
                (game.strip(), game_html),
            )
            cursor = await db.execute("SELECT id FROM games WHERE name = ? COLLATE NOCASE", (game.strip(),))
            game_row = await cursor.fetchone()
            assert game_row is not None
            game_id = int(game_row[0])
            await db.execute(
                """INSERT INTO levels(game_id, name, name_html) VALUES (?, ?, ?)
                   ON CONFLICT(game_id, name) DO UPDATE SET name_html =
                       CASE WHEN excluded.name_html != '' THEN excluded.name_html ELSE levels.name_html END""",
                (game_id, level.strip(), level_html),
            )
            cursor = await db.execute(
                "SELECT id FROM levels WHERE game_id = ? AND name = ? COLLATE NOCASE",
                (game_id, level.strip()),
            )
            level_row = await cursor.fetchone()
            assert level_row is not None
            cursor = await db.execute(
                """INSERT INTO products(
                       game_id, level_id, name, name_html, description, description_html,
                       price_cents, photo_file_ids,
                       account_email_encrypted, account_password_encrypted,
                       delivery_file_id, delivery_url, service_kind, identity_fields, created_by, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    game_id,
                    int(level_row[0]),
                    name.strip(),
                    name_html,
                    description.strip(),
                    description_html,
                    price_cents,
                    json.dumps(photo_file_ids),
                    email_encrypted,
                    password_encrypted,
                    delivery_file_id.strip(),
                    delivery_url.strip(),
                    service_kind,
                    json.dumps(identity_fields, ensure_ascii=False),
                    created_by,
                    now,
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def games_with_stock(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                     """SELECT g.id, g.name, g.name_html, COUNT(p.id) AS product_count,
                                  SUM(CASE WHEN p.status = 'available' THEN 1 ELSE 0 END) AS available_count
                   FROM games AS g
                         JOIN products AS p ON p.game_id = g.id AND p.is_listed = 1 AND p.service_kind = ''
                   GROUP BY g.id, g.name ORDER BY g.name COLLATE NOCASE"""
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def service_games_with_stock(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT g.id, g.name, g.name_html, COUNT(p.id) AS product_count,
                          SUM(CASE WHEN p.status = 'available' THEN 1 ELSE 0 END) AS available_count
                   FROM games AS g
                   JOIN products AS p ON p.game_id = g.id AND p.is_listed = 1 AND p.service_kind != ''
                   GROUP BY g.id, g.name ORDER BY g.name COLLATE NOCASE"""
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def levels_with_stock(self, game_id: int) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                     """SELECT l.id, l.name, l.name_html, COUNT(p.id) AS product_count,
                                  SUM(CASE WHEN p.status = 'available' THEN 1 ELSE 0 END) AS available_count
                   FROM levels AS l
                         JOIN products AS p ON p.level_id = l.id AND p.is_listed = 1
                   WHERE l.game_id = ?
                   GROUP BY l.id, l.name ORDER BY l.name COLLATE NOCASE""",
                (game_id,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def products_for_game(self, game_id: int) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT p.id, p.game_id, p.level_id, p.name, p.name_html,
                          p.description, p.description_html, p.price_cents,
                          p.photo_file_ids, p.status, p.service_kind, p.identity_fields,
                          g.name AS game_name, g.name_html AS game_name_html,
                          l.name AS level_name, l.name_html AS level_name_html
                   FROM products AS p
                   JOIN games AS g ON g.id = p.game_id
                   JOIN levels AS l ON l.id = p.level_id
                   WHERE p.game_id = ? AND p.is_listed = 1
                   ORDER BY l.name COLLATE NOCASE, p.id DESC""",
                (game_id,),
            )
            products = [dict(row) for row in await cursor.fetchall()]
            for product in products:
                product["photo_file_ids"] = json.loads(product["photo_file_ids"])
                product["identity_fields"] = json.loads(product["identity_fields"])
            return products

    async def products_for_level(self, level_id: int) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT p.id, p.game_id, p.level_id, p.name, p.name_html, p.description, p.description_html,
                          p.price_cents, p.photo_file_ids, p.status, p.service_kind, p.identity_fields,
                          g.name AS game_name, g.name_html AS game_name_html,
                          l.name AS level_name, l.name_html AS level_name_html
                   FROM products AS p
                   JOIN games AS g ON g.id = p.game_id
                   JOIN levels AS l ON l.id = p.level_id
                   WHERE p.level_id = ? AND p.is_listed = 1
                   ORDER BY p.id DESC""",
                (level_id,),
            )
            products = [dict(row) for row in await cursor.fetchall()]
            for product in products:
                product["photo_file_ids"] = json.loads(product["photo_file_ids"])
                product["identity_fields"] = json.loads(product["identity_fields"])
            return products

    async def available_product(self, product_id: int) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT p.id, p.level_id, p.name, p.name_html, p.description, p.description_html,
                          p.price_cents, p.photo_file_ids, p.status, p.service_kind, p.identity_fields,
                          g.name AS game_name, g.name_html AS game_name_html,
                          l.name AS level_name, l.name_html AS level_name_html
                   FROM products AS p
                   JOIN games AS g ON g.id = p.game_id
                   JOIN levels AS l ON l.id = p.level_id
                   WHERE p.id = ? AND p.is_listed = 1""",
                (product_id,),
            )
            product = _row_to_dict(await cursor.fetchone())
            if product:
                product["photo_file_ids"] = json.loads(product["photo_file_ids"])
                product["identity_fields"] = json.loads(product["identity_fields"])
            return product

    async def admin_products(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT p.id, p.name, p.name_html, p.price_cents, p.status, p.is_listed, p.service_kind,
                          g.name AS game_name, g.name_html AS game_name_html,
                          l.name AS level_name, l.name_html AS level_name_html
                   FROM products AS p
                   JOIN games AS g ON g.id = p.game_id
                   JOIN levels AS l ON l.id = p.level_id
                   ORDER BY p.id DESC"""
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def product_email_preview(self, product_id: int) -> str | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT account_email_encrypted FROM products WHERE id = ?",
                (product_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        email = self._cipher.decrypt(row[0].encode("ascii")).decode("utf-8")
        local, separator, domain = email.partition("@")
        if not separator or not domain:
            return "***"
        return f"{local[:2]}***@{domain}"

    async def admin_product_credentials(self, product_id: int) -> dict[str, str] | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """SELECT account_email_encrypted, account_password_encrypted
                   FROM products
                   WHERE id = ? AND status = 'available' AND reserved_order_id IS NULL""",
                (product_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "email": self._cipher.decrypt(row[0].encode("ascii")).decode("utf-8"),
            "password": self._cipher.decrypt(row[1].encode("ascii")).decode("utf-8"),
        }

    async def update_product_email(self, product_id: int, email: str) -> bool:
        encrypted = self._cipher.encrypt(email.encode("utf-8")).decode("ascii")
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """UPDATE products SET account_email_encrypted = ?
                   WHERE id = ? AND status = 'available' AND reserved_order_id IS NULL""",
                (encrypted, product_id),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def update_product_password(self, product_id: int, password: str) -> bool:
        encrypted = self._cipher.encrypt(password.encode("utf-8")).decode("ascii")
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """UPDATE products SET account_password_encrypted = ?
                   WHERE id = ? AND status = 'available' AND reserved_order_id IS NULL""",
                (encrypted, product_id),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def hide_product(self, product_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """UPDATE products SET is_listed = 0
                   WHERE id = ? AND is_listed = 1 AND status != 'reserved'
                     AND (service_kind = '' OR NOT EXISTS (
                         SELECT 1 FROM orders
                         WHERE orders.product_id = products.id AND orders.status = 'pending'
                           AND orders.expires_at > ?
                     ))""",
                (product_id, _timestamp(_utc_now())),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def set_payment_transaction(self, order_id: int, transaction_id: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """UPDATE orders SET payment_tx_id = ?
                   WHERE id = ? AND status = 'pending' AND expires_at > ?""",
                (transaction_id, order_id, _timestamp(_utc_now())),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def wallet_balance_cents(self, user_id: int) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT balance_cents FROM wallet_accounts WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def create_wallet_topup(
        self,
        user_id: int,
        amount_cents: int,
        credit_cents: int,
    ) -> dict[str, Any]:
        if amount_cents <= 0 or credit_cents < amount_cents:
            raise ValueError("Invalid wallet top-up amount")
        now = _utc_now()
        expires_at = now + timedelta(minutes=ORDER_RESERVATION_MINUTES)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """INSERT INTO wallet_topups(user_id, amount_cents, credit_cents, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (user_id, amount_cents, credit_cents, _timestamp(now), _timestamp(expires_at)),
            )
            topup_id = int(cursor.lastrowid)
            await db.commit()
            return {
                "id": topup_id,
                "user_id": user_id,
                "amount_cents": amount_cents,
                "credit_cents": credit_cents,
                "expires_at": _timestamp(expires_at),
            }

    async def set_wallet_topup_transaction(self, topup_id: int, transaction_id: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """UPDATE wallet_topups SET payment_tx_id = ?
                   WHERE id = ? AND status = 'pending' AND expires_at > ?""",
                (transaction_id, topup_id, _timestamp(_utc_now())),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def pending_wallet_topups(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                  """SELECT t.id, t.user_id, t.amount_cents, t.credit_cents, t.payment_tx_id, t.expires_at,
                         u.first_name, u.username
                     FROM wallet_topups AS t
                     JOIN users AS u ON u.user_id = t.user_id
                     WHERE t.status = 'pending' AND t.payment_tx_id IS NOT NULL AND t.expires_at > ?""",
                (_timestamp(_utc_now()),),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def complete_wallet_topup(
        self,
        topup_id: int,
        verified_amount_cents: int,
    ) -> dict[str, Any] | None:
        now = _timestamp(_utc_now())
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                  """SELECT t.id, t.user_id, t.amount_cents, t.credit_cents,
                         u.first_name, u.username
                     FROM wallet_topups AS t
                     JOIN users AS u ON u.user_id = t.user_id
                     WHERE t.id = ? AND t.status = 'pending' AND t.expires_at > ?""",
                (topup_id, now),
            )
            topup = _row_to_dict(await cursor.fetchone())
            if topup is None or int(topup["amount_cents"]) != verified_amount_cents:
                await db.rollback()
                return None
            await db.execute(
                """INSERT INTO wallet_accounts(user_id, balance_cents) VALUES (?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET balance_cents = balance_cents + excluded.balance_cents""",
                (topup["user_id"], topup["credit_cents"]),
            )
            await db.execute("UPDATE wallet_topups SET status = 'paid' WHERE id = ?", (topup_id,))
            cursor = await db.execute(
                "SELECT balance_cents FROM wallet_accounts WHERE user_id = ?",
                (topup["user_id"],),
            )
            balance_row = await cursor.fetchone()
            await db.commit()
            topup["balance_cents"] = int(balance_row[0])
            return topup

    async def pending_payment_orders(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT o.id, o.user_id, o.payment_tx_id, o.created_at, o.expires_at,
                          o.telegram_username,
                          COALESCE(NULLIF(o.amount_cents, 0), p.price_cents) AS price_cents, p.name
                   FROM orders AS o
                   JOIN products AS p ON p.id = o.product_id
                   WHERE o.status = 'pending' AND o.payment_tx_id IS NOT NULL
                     AND o.expires_at > ?""",
                (_timestamp(_utc_now()),),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def create_order(
        self,
        product_id: int,
        user_id: int,
        telegram_username: str = "",
        identity_values: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        now = _utc_now()
        expires_at = now + timedelta(minutes=ORDER_RESERVATION_MINUTES)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("BEGIN IMMEDIATE")
            discount_cursor = await db.execute(
                """SELECT u.active_discount_code, c.percent
                   FROM users AS u LEFT JOIN discount_codes AS c
                     ON c.code = u.active_discount_code AND c.is_active = 1
                   WHERE u.user_id = ?""",
                (user_id,),
            )
            discount = await discount_cursor.fetchone()
            cursor = await db.execute(
                "SELECT price_cents, service_kind FROM products "
                "WHERE id = ? AND status = 'available' AND is_listed = 1",
                (product_id,),
            )
            product = await cursor.fetchone()
            if product is None:
                await db.rollback()
                return None
            is_service = bool(product[1])
            if not is_service:
                cursor = await db.execute(
                    """UPDATE products SET status = 'reserved'
                       WHERE id = ? AND status = 'available' AND is_listed = 1""",
                    (product_id,),
                )
                if cursor.rowcount != 1:
                    await db.rollback()
                    return None
            cursor = await db.execute(
                """INSERT INTO orders(
                       product_id, user_id, created_at, expires_at, telegram_username, identity_values
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    product_id,
                    user_id,
                    _timestamp(now),
                    _timestamp(expires_at),
                    telegram_username,
                    json.dumps(identity_values or {}, ensure_ascii=False),
                ),
            )
            order_id = int(cursor.lastrowid)
            original_price_cents = int(product[0])
            discount_code = discount[0] if discount and discount[1] is not None else None
            discount_percent = int(discount[1]) if discount_code else 0
            amount_cents = max(1, (original_price_cents * (100 - discount_percent) + 50) // 100)
            await db.execute(
                "UPDATE orders SET amount_cents = ?, discount_code = ? WHERE id = ?",
                (amount_cents, discount_code, order_id),
            )
            if discount and discount[0]:
                await db.execute("UPDATE users SET active_discount_code = NULL WHERE user_id = ?", (user_id,))
            if not is_service:
                await db.execute(
                    "UPDATE products SET reserved_order_id = ? WHERE id = ?",
                    (order_id, product_id),
                )
            await db.commit()
            order_cursor = await db.execute(
                """SELECT o.id, o.user_id, o.expires_at, o.telegram_username, o.identity_values,
                          p.name, p.name_html,
                          COALESCE(NULLIF(o.amount_cents, 0), p.price_cents) AS price_cents,
                          p.price_cents AS original_price_cents, o.discount_code, p.service_kind,
                          g.name AS game_name, g.name_html AS game_name_html,
                          l.name AS level_name, l.name_html AS level_name_html
                   FROM orders AS o
                   JOIN products AS p ON p.id = o.product_id
                   JOIN games AS g ON g.id = p.game_id
                   JOIN levels AS l ON l.id = p.level_id
                   WHERE o.id = ?""",
                (order_id,),
            )
            order = _row_to_dict(await order_cursor.fetchone())
            if order is not None:
                order["identity_values"] = json.loads(order["identity_values"] or "{}")
            return order

    async def pay_product_with_wallet(
        self,
        product_id: int,
        user_id: int,
        telegram_username: str = "",
        identity_values: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        timestamp = _timestamp(now)
        expires_at = _timestamp(now + timedelta(minutes=ORDER_RESERVATION_MINUTES))
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("BEGIN IMMEDIATE")
            discount_cursor = await db.execute(
                """SELECT u.active_discount_code, c.percent
                   FROM users AS u LEFT JOIN discount_codes AS c
                     ON c.code = u.active_discount_code AND c.is_active = 1
                   WHERE u.user_id = ?""",
                (user_id,),
            )
            discount = await discount_cursor.fetchone()
            cursor = await db.execute(
                """SELECT p.id, p.price_cents, p.name, p.name_html, p.service_kind,
                          g.name AS game_name, g.name_html AS game_name_html,
                          l.name AS level_name, l.name_html AS level_name_html
                   FROM products AS p
                   JOIN games AS g ON g.id = p.game_id
                   JOIN levels AS l ON l.id = p.level_id
                   WHERE p.id = ? AND p.status = 'available' AND p.is_listed = 1""",
                (product_id,),
            )
            product = _row_to_dict(await cursor.fetchone())
            if product is None:
                await db.rollback()
                return {"status": "unavailable"}
            cursor = await db.execute(
                "SELECT balance_cents FROM wallet_accounts WHERE user_id = ?",
                (user_id,),
            )
            wallet = await cursor.fetchone()
            balance_cents = int(wallet[0]) if wallet else 0
            discount_code = discount[0] if discount and discount[1] is not None else None
            discount_percent = int(discount[1]) if discount_code else 0
            original_price_cents = int(product["price_cents"])
            price_cents = max(1, (original_price_cents * (100 - discount_percent) + 50) // 100)
            if balance_cents < price_cents:
                await db.rollback()
                return {
                    "status": "insufficient",
                    "balance_cents": balance_cents,
                    "price_cents": price_cents,
                }
            is_service = bool(product["service_kind"])
            if not is_service:
                cursor = await db.execute(
                    """UPDATE products SET status = 'reserved'
                       WHERE id = ? AND status = 'available' AND is_listed = 1""",
                    (product_id,),
                )
                if cursor.rowcount != 1:
                    await db.rollback()
                    return {"status": "unavailable"}
            cursor = await db.execute(
                """INSERT INTO orders(product_id, user_id, status, created_at, expires_at,
                                      amount_cents, discount_code, telegram_username, identity_values)
                   VALUES (?, ?, 'paid', ?, ?, ?, ?, ?, ?)""",
                (
                    product_id,
                    user_id,
                    timestamp,
                    expires_at,
                    price_cents,
                    discount_code,
                    telegram_username,
                    json.dumps(identity_values or {}, ensure_ascii=False),
                ),
            )
            order_id = int(cursor.lastrowid)
            if not is_service:
                await db.execute(
                    """UPDATE products SET status = 'sold', reserved_order_id = NULL
                       WHERE id = ?""",
                    (product_id,),
                )
            await db.execute(
                "UPDATE wallet_accounts SET balance_cents = balance_cents - ? WHERE user_id = ?",
                (price_cents, user_id),
            )
            await db.execute("UPDATE users SET active_discount_code = NULL WHERE user_id = ?", (user_id,))
            await db.commit()
            cursor = await db.execute(
                """SELECT o.id, o.user_id, o.product_id, o.telegram_username, o.identity_values,
                          p.name, p.name_html, p.service_kind,
                          o.amount_cents AS price_cents, p.price_cents AS original_price_cents,
                          o.discount_code,
                          p.account_email_encrypted, p.account_password_encrypted,
                          p.delivery_file_id, p.delivery_url,
                          g.name AS game_name, g.name_html AS game_name_html,
                          l.name AS level_name, l.name_html AS level_name_html
                   FROM orders AS o
                   JOIN products AS p ON p.id = o.product_id
                   JOIN games AS g ON g.id = p.game_id
                   JOIN levels AS l ON l.id = p.level_id
                   WHERE o.id = ?""",
                (order_id,),
            )
            order = _row_to_dict(await cursor.fetchone())
            cursor = await db.execute(
                "SELECT balance_cents FROM wallet_accounts WHERE user_id = ?",
                (user_id,),
            )
            balance = await cursor.fetchone()
        assert order is not None and balance is not None
        order["account_email"] = self._cipher.decrypt(
            order.pop("account_email_encrypted").encode("ascii")
        ).decode("utf-8")
        order["account_password"] = self._cipher.decrypt(
            order.pop("account_password_encrypted").encode("ascii")
        ).decode("utf-8")
        order["status"] = "paid"
        order["identity_values"] = json.loads(order["identity_values"] or "{}")
        order["balance_cents"] = int(balance[0])
        return order

    async def pending_orders(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT o.id, o.user_id, o.expires_at, o.telegram_username, o.identity_values,
                          p.name, p.name_html, p.service_kind,
                          COALESCE(NULLIF(o.amount_cents, 0), p.price_cents) AS price_cents,
                          g.name AS game_name, g.name_html AS game_name_html,
                          l.name AS level_name, l.name_html AS level_name_html
                   FROM orders AS o
                   JOIN products AS p ON p.id = o.product_id
                   JOIN games AS g ON g.id = p.game_id
                   JOIN levels AS l ON l.id = p.level_id
                   WHERE o.status = 'pending' AND o.expires_at > ?
                   ORDER BY o.id""",
                (_timestamp(_utc_now()),),
            )
            orders = [dict(row) for row in await cursor.fetchall()]
            for order in orders:
                order["identity_values"] = json.loads(order["identity_values"] or "{}")
            return orders

    async def complete_paid_order(
        self,
        order_id: int,
        verified_amount_cents: int,
    ) -> dict[str, Any] | None:
        now = _timestamp(_utc_now())
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """SELECT o.id, o.user_id, o.product_id, o.telegram_username, o.identity_values,
                          p.name, p.name_html, p.service_kind,
                          COALESCE(NULLIF(o.amount_cents, 0), p.price_cents) AS price_cents,
                          p.price_cents AS original_price_cents, o.discount_code,
                          p.account_email_encrypted, p.account_password_encrypted,
                          p.delivery_file_id, p.delivery_url,
                          g.name AS game_name, g.name_html AS game_name_html,
                          l.name AS level_name, l.name_html AS level_name_html
                   FROM orders AS o
                   JOIN products AS p ON p.id = o.product_id
                   JOIN games AS g ON g.id = p.game_id
                   JOIN levels AS l ON l.id = p.level_id
                   WHERE o.id = ? AND o.status = 'pending' AND o.expires_at > ?
                                         AND ((p.service_kind != '' AND p.status = 'available' AND p.is_listed = 1)
                                             OR (p.service_kind = '' AND p.status = 'reserved' AND p.reserved_order_id = o.id))""",
                (order_id, now),
            )
            order = _row_to_dict(await cursor.fetchone())
            if order is None or int(order["price_cents"]) != verified_amount_cents:
                await db.rollback()
                return None
            await db.execute("UPDATE orders SET status = 'paid' WHERE id = ?", (order_id,))
            if not order["service_kind"]:
                await db.execute(
                    """UPDATE products SET status = 'sold', reserved_order_id = NULL
                       WHERE id = ? AND reserved_order_id = ?""",
                    (order["product_id"], order_id),
                )
            await db.commit()

        order["account_email"] = self._cipher.decrypt(
            order.pop("account_email_encrypted").encode("ascii")
        ).decode("utf-8")
        order["account_password"] = self._cipher.decrypt(
            order.pop("account_password_encrypted").encode("ascii")
        ).decode("utf-8")
        order["identity_values"] = json.loads(order["identity_values"] or "{}")
        return order

    async def cancel_order(self, order_id: int, user_id: int | None = None) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """SELECT o.id, o.user_id, o.product_id, o.discount_code, p.name
                   FROM orders AS o JOIN products AS p ON p.id = o.product_id
                         WHERE o.id = ? AND o.status = 'pending'
                            AND (? IS NULL OR o.user_id = ?)""",
                     (order_id, user_id, user_id),
            )
            order = _row_to_dict(await cursor.fetchone())
            if order is None:
                await db.rollback()
                return None
            await db.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
            await db.execute(
                """UPDATE products SET status = 'available', reserved_order_id = NULL
                   WHERE id = ? AND reserved_order_id = ?""",
                (order["product_id"], order_id),
            )
            if order["discount_code"]:
                await db.execute(
                    """UPDATE users SET active_discount_code = COALESCE(active_discount_code, ?)
                       WHERE user_id = ? AND EXISTS (
                           SELECT 1 FROM discount_codes WHERE code = ? AND is_active = 1
                       )""",
                    (order["discount_code"], order["user_id"], order["discount_code"]),
                )
            await db.commit()
            return order

    async def expire_orders(self) -> list[dict[str, Any]]:
        now = _timestamp(_utc_now())
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """SELECT o.id, o.user_id, o.product_id, o.discount_code, p.name
                   FROM orders AS o JOIN products AS p ON p.id = o.product_id
                   WHERE o.status = 'pending' AND o.expires_at <= ?""",
                (now,),
            )
            expired = [dict(row) for row in await cursor.fetchall()]
            for order in expired:
                await db.execute("UPDATE orders SET status = 'expired' WHERE id = ?", (order["id"],))
                await db.execute(
                    """UPDATE products SET status = 'available', reserved_order_id = NULL
                       WHERE id = ? AND reserved_order_id = ?""",
                    (order["product_id"], order["id"]),
                )
                if order["discount_code"]:
                    await db.execute(
                        """UPDATE users SET active_discount_code = COALESCE(active_discount_code, ?)
                           WHERE user_id = ? AND EXISTS (
                               SELECT 1 FROM discount_codes WHERE code = ? AND is_active = 1
                           )""",
                        (order["discount_code"], order["user_id"], order["discount_code"]),
                    )
            await db.commit()
            return expired

    async def order_history(self, user_id: int) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT o.id, o.status, o.created_at, p.name, p.name_html,
                          COALESCE(NULLIF(o.amount_cents, 0), p.price_cents) AS price_cents,
                          g.name AS game_name, g.name_html AS game_name_html,
                          l.name AS level_name, l.name_html AS level_name_html
                   FROM orders AS o
                   JOIN products AS p ON p.id = o.product_id
                   JOIN games AS g ON g.id = p.game_id
                   JOIN levels AS l ON l.id = p.level_id
                   WHERE o.user_id = ? ORDER BY o.id DESC LIMIT 20""",
                (user_id,),
            )
            return [dict(row) for row in await cursor.fetchall()]
