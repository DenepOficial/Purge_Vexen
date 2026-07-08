import os
import asyncpg


DATABASE_URL = os.getenv("DATABASE_URL")


if not DATABASE_URL:
    raise Exception("DATABASE_URL no configurada en Railway")


async def create_pool():

    pool = await asyncpg.create_pool(
        DATABASE_URL
    )

    return pool
