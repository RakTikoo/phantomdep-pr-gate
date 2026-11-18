from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from requests import Response


def status(response: Response) -> int:
    return response.status_code
