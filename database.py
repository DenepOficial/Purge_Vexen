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
            max_size=5,
            server_settings={
                "application_name": BOT_NAME
            }
        )

        print(f"✅ PostgreSQL conectado para {BOT_NAME}")

    return _pool


async def close_pool():

    global _pool

    if _pool:
        await _pool.close()
        _pool = None
