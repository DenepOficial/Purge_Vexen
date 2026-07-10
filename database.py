import os
import re
import asyncpg


DATABASE_URL = os.getenv("DATABASE_URL")
BOT_NAME = os.getenv("BOT_NAME")

if not DATABASE_URL:
    raise Exception("DATABASE_URL no existe")

if not BOT_NAME:
    raise Exception("BOT_NAME no configurado")


def normalize_schema_name(name: str) -> str:
    schema = name.lower()
    schema = re.sub(r"[^a-z0-9_]", "_", schema)

    if not re.match(r"^[a-z_]", schema):
        schema = f"bot_{schema}"

    return schema


SCHEMA_NAME = normalize_schema_name(BOT_NAME)

_pool = None


async def create_pool():
    global _pool

    if _pool is None:
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=5
        )

        print(f"✅ PostgreSQL conectado para {BOT_NAME}")

        await create_tables(_pool)

    return _pool


async def create_tables(db):
    schema = SCHEMA_NAME

    await db.execute(
        f"""
        CREATE SCHEMA IF NOT EXISTS {schema};
        """
    )

    await db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.whitelist (
            id SERIAL PRIMARY KEY,
            guild_id BIGINT UNIQUE NOT NULL
        );
        """
    )

    await db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.allowed_roles (
            id SERIAL PRIMARY KEY,
            guild_id BIGINT NOT NULL,
            role_id BIGINT NOT NULL,

            UNIQUE(guild_id, role_id)
        );
        """
    )

    await db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.linked_channels (
            id SERIAL PRIMARY KEY,
            guild_id BIGINT NOT NULL,
            channel_id BIGINT NOT NULL,
            hours INTEGER NOT NULL,
            last_clean TIMESTAMPTZ NOT NULL,

            UNIQUE(guild_id, channel_id)
        );
        """
    )

    await db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.logs_channels (
            id SERIAL PRIMARY KEY,
            guild_id BIGINT UNIQUE NOT NULL,
            channel_id BIGINT NOT NULL
        );
        """
    )

    print(f"✅ Tablas creadas en schema {schema}")
