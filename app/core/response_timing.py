import asyncio
import time
from collections.abc import Awaitable, Callable

PASSWORD_RESET_MINIMUM_RESPONSE_SECONDS = 0.35


class MinimumResponseBudget:
    def __init__(
        self,
        minimum_seconds: float = PASSWORD_RESET_MINIMUM_RESPONSE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._minimum_seconds = minimum_seconds
        self._clock = clock
        self._sleeper = sleeper

    def start(self) -> float:
        return self._clock()

    async def wait(self, started_at: float) -> None:
        remaining = self._minimum_seconds - (self._clock() - started_at)
        if remaining > 0:
            await self._sleeper(remaining)


password_reset_response_budget = MinimumResponseBudget()


def get_password_reset_response_budget() -> MinimumResponseBudget:
    return password_reset_response_budget
