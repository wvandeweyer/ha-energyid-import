# Typed subset of pytest_homeassistant_custom_component.test_util.aiohttp.

from collections.abc import Awaitable, Callable, Mapping
import re

from yarl import URL

type _Url = str | URL | re.Pattern[str]
type _SideEffect = Callable[[str, URL, object], Awaitable[AiohttpClientMockResponse]]

class AiohttpClientMockResponse:
    def __init__(
        self,
        method: str,
        url: URL,
        status: int = ...,
        response: bytes | None = ...,
        json: object = ...,
        text: str | None = ...,
        cookies: Mapping[str, str] | None = ...,
        exc: BaseException | type[BaseException] | None = ...,
        headers: Mapping[str, str] | None = ...,
    ) -> None: ...

class AiohttpClientMocker:
    mock_calls: list[tuple[str, URL, object, Mapping[str, str] | None]]
    @property
    def call_count(self) -> int: ...
    def get(
        self,
        url: _Url,
        *,
        status: int = ...,
        text: str | None = ...,
        json: object = ...,
        params: Mapping[str, str] | None = ...,
        headers: Mapping[str, str] | None = ...,
        exc: BaseException | type[BaseException] | None = ...,
        side_effect: _SideEffect | None = ...,
        closing: bool | None = ...,
    ) -> None: ...
    def clear_requests(self) -> None: ...
