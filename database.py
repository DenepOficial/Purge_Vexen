import os
import asyncpg


DATABASE_URL = os.getenv("DATABASE_URL")
BOT_NAME = os.getenv("BOT_NAME")


if not DATABASE_URL:
    raise Exception("DATABASE_URL no existe")


if not BOT_NAME:
    raise Exception("BOT_NAME no configurado")


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

    schema = BOT_NAME.lower()


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
            last_clean TIMESTAMP NOT NULL,

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
