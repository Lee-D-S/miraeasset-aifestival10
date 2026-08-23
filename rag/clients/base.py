import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from rag.config import settings


class ClovaApiClient:
    """Shared HTTP transport and authentication for CLOVA Studio APIs."""

    def post(self, path: str, payload: dict) -> dict:
        if not settings.clova_api_key:
            raise RuntimeError("CLOVA_API_KEY is not configured.")

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {settings.clova_api_key}",
        }
        if settings.clova_request_id:
            headers["X-NCP-CLOVASTUDIO-REQUEST-ID"] = settings.clova_request_id

        request = Request(
            f"https://{settings.clova_api_host}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"CLOVA API request failed: HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise RuntimeError(f"CLOVA API request failed: {error}") from error

    @staticmethod
    def result_or_raise(response: dict, operation: str) -> dict:
        status = response.get("status", {})
        if status.get("code") != "20000":
            raise RuntimeError(f"CLOVA {operation} failed: {status}")
        return response.get("result", {})
