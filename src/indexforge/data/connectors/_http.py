"""JSON over HTTPS for the connectors that call a provider's web API."""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from indexforge.data.connectors.base import DataSourceError

TIMEOUT_SECONDS = 30


def get_json(provider: str, url: str, params: dict[str, Any]) -> Any:
    """
    Fetch a URL and parse the JSON answer.

    Errors never include the URL or the parameters, because the API key is one
    of them and error messages end up in logs and on screens.

    Raises:
        DataSourceError: If the provider refuses, limits or cannot be reached.
    """
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": "indexforge"})  # noqa: S310 - https URLs built by the connectors
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise DataSourceError(_refusal(provider, e.code)) from None
    except (urllib.error.URLError, TimeoutError) as e:
        reason = getattr(e, "reason", e)
        raise DataSourceError(f"{provider} could not be reached: {reason}") from None
    except json.JSONDecodeError:
        raise DataSourceError(f"{provider} sent an answer that is not JSON") from None


def _refusal(provider: str, status: int) -> str:
    if status == 401:
        return f"{provider} rejected the API key. Check that it was entered correctly."
    if status in (402, 403):
        return (
            f"{provider} refused the request: this data is not included in the plan "
            "the API key belongs to."
        )
    if status == 429:
        return (
            f"{provider} is limiting requests for this API key. Its free plan allows a "
            "fixed number; wait for the allowance to reset, or use fewer companies."
        )
    return f"{provider} answered with an error (HTTP {status})."
