"""
Валидация изображений перед публикацией события.
Проверяет: HTTP 200, Content-Type, размер (≥400×300), аспект (0.5..2.0).
Не пропускает SVG, GIF, ICO.
"""
import logging
from io import BytesIO

import httpx

logger = logging.getLogger(__name__)

_MIN_WIDTH = 400
_MIN_HEIGHT = 300
_MIN_ASPECT = 0.5
_MAX_ASPECT = 2.0
_EXCLUDED_TYPES = frozenset({
    "image/svg+xml",
    "image/gif",
    "image/x-icon",
    "image/vnd.microsoft.icon",
})
_TIMEOUT = 6.0


async def validate_image_url(url: str) -> tuple[bool, str]:
    """
    Проверяет URL изображения.
    Возвращает (True, "ok") или (False, причина_отказа).
    """
    if not url or not url.startswith(("http://", "https://")):
        return False, "not_http"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            # HEAD — быстрая проверка Content-Type
            ct = ""
            try:
                head = await client.head(url)
                ct = head.headers.get("content-type", "").split(";")[0].strip().lower()
                if ct and not ct.startswith("image/"):
                    return False, f"bad_content_type:{ct}"
                if ct in _EXCLUDED_TYPES:
                    return False, f"excluded_type:{ct}"
            except Exception:
                pass  # HEAD недоступен — пробуем GET

            # GET — скачиваем для Pillow
            resp = await client.get(url)
            resp.raise_for_status()

            ct_get = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if ct_get in _EXCLUDED_TYPES:
                return False, f"excluded_type:{ct_get}"
            if ct_get and not ct_get.startswith("image/"):
                return False, f"bad_content_type:{ct_get}"

            # Проверка размеров через Pillow
            try:
                from PIL import Image
                img = Image.open(BytesIO(resp.content))
                w, h = img.size
            except Exception as exc:
                return False, f"pillow_error:{exc}"

            if w < _MIN_WIDTH or h < _MIN_HEIGHT:
                return False, f"too_small:{w}x{h}"

            if h == 0:
                return False, "zero_height"

            aspect = w / h
            if aspect < _MIN_ASPECT or aspect > _MAX_ASPECT:
                return False, f"bad_aspect:{aspect:.2f}"

    except httpx.HTTPStatusError as exc:
        return False, f"http_error:{exc.response.status_code}"
    except Exception as exc:
        return False, f"network_error:{exc}"

    return True, "ok"
