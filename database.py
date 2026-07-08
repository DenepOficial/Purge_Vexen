import os
import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL")
BOT_NAME = os.getenv("BOT_NAME")


if not DATABASE_URL:
    raise Exception("DATABASE_URL no existe")


if not BOT_NAME:
    raise Exception("BOT_NAME no configurado")


async def create_pool():

    return await asyncpg.create_pool(
        DATABASE_URL
    )
