"""Coordinate worker and API maintenance across processes sharing the data volume."""
import asyncio
import os
from .config import DATA


class ProcessLock:
    def __init__(self, name):
        self.local = asyncio.Lock()
        self.name = name
        self.file = None

    async def __aenter__(self):
        await self.local.acquire()
        try:
            if os.name == 'posix':
                import fcntl
                self.file = (DATA / ('lock-' + self.name)).open('a')
                while True:
                    try:
                        fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        await asyncio.sleep(.2)
            return self
        except BaseException:
            if self.file:
                self.file.close()
                self.file = None
            self.local.release()
            raise

    async def __aexit__(self, *args):
        if self.file:
            self.file.close()
            self.file = None
        self.local.release()
