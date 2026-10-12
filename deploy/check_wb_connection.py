"""Check migrated WB credentials without printing the token."""

import asyncio

from app.db import Session
from app.integrations import wb_token
from app.security import guard


async def main() -> None:
    with Session() as db:
        token = wb_token(db, 'owner')
    await guard.request('wb', 'ping', token=token)
    print('Wildberries API connection: ok')


if __name__ == '__main__':
    asyncio.run(main())
