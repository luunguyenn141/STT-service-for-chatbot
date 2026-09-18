"""OpenAI-powered post-processor that refines raw Vietnamese STT output.

The refiner is *opt-in*: when ``OPENAI_API_KEY`` is absent the function
returns the original text unchanged so the rest of the pipeline is
unaffected.

Design goals
------------
* Correct homophones, missing words, and context errors that are common in
  noisy banking voice recordings (e.g. "chuyển 5 trăm" → "chuyển 500k").
* Keep banking domain terminology intact using the same keyterm list already
  configured for the STT provider.
* Never log the transcript content (privacy/audit requirement inherited from
  the rest of the service).
* Add minimal latency: the prompt is small, ``gpt-4o-mini`` is used by
  default, and the call is async.
* Fail open: any network / API error returns the original text so the user
  always gets *something*.
"""
from __future__ import annotations

import logging
from typing import Final

import httpx

logger = logging.getLogger("stt_poc.refiner")

_SYSTEM_PROMPT: Final[str] = (
    "Bạn là trợ lý chỉnh sửa văn bản nhận dạng giọng nói tiếng Việt cho ứng dụng ngân hàng MSB. "
    "Nhiệm vụ của bạn là sửa lỗi nhận dạng giọng nói (từ bị thiếu, từ đồng âm sai nghĩa, "
    "lỗi ngữ cảnh môi trường ồn ào) mà KHÔNG thay đổi ý định của người dùng. "
    "Quy tắc:\n"
    "1. Chỉ trả về đoạn văn bản đã được chỉnh sửa, không giải thích.\n"
    "2. Giữ nguyên các thuật ngữ ngân hàng, số tiền, tên tài khoản.\n"
    "3. Nếu văn bản đã đúng, trả về nguyên văn không thay đổi.\n"
    "4. Không thêm thông tin hoặc ngữ cảnh không có trong câu gốc.\n"
    "5. Ưu tiên nghĩa trong lĩnh vực ngân hàng, tài chính cá nhân."
)

_OPENAI_CHAT_URL: Final[str] = "https://api.openai.com/v1/chat/completions"
_DEFAULT_MODEL: Final[str] = "gpt-4o-mini"
_REFINER_TIMEOUT: Final[float] = 8.0  # seconds — tight budget to stay responsive


async def refine_text(
    raw_text: str,
    *,
    openai_api_key: str,
    keyterms: list[str] | None = None,
    model: str = _DEFAULT_MODEL,
) -> str:
    """Return a refined version of *raw_text*, or *raw_text* itself on failure.

    Parameters
    ----------
    raw_text:
        The raw transcript from the STT provider.
    openai_api_key:
        OpenAI secret key.  Callers must ensure this is non-empty.
    keyterms:
        Banking domain terms already configured for the STT provider —
        injected into the user message to help the model prioritise them.
    model:
        OpenAI chat model to use.  Defaults to ``gpt-4o-mini`` for speed
        and cost.
    """
    if not raw_text.strip():
        return raw_text

    # Build a user message that includes domain keyterms when available so
    # the model gives them priority during correction.
    keyterm_hint = ""
    if keyterms:
        keyterm_hint = (
            f"\n\nCác thuật ngữ quan trọng cần ưu tiên: {', '.join(keyterms[:50])}."
        )

    user_message = f"Văn bản gốc cần chỉnh sửa:{keyterm_hint}\n\n{raw_text}"

    try:
        async with httpx.AsyncClient(timeout=_REFINER_TIMEOUT) as client:
            response = await client.post(
                _OPENAI_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                    "temperature": 0.0,  # deterministic corrections
                    "max_tokens": 512,
                },
            )
            response.raise_for_status()
            data = response.json()
            refined = data["choices"][0]["message"]["content"].strip()
            if refined:
                logger.info(
                    "audit_event=text_refine_success model=%s input_len=%d output_len=%d",
                    model,
                    len(raw_text),
                    len(refined),
                )
                return refined
    except httpx.TimeoutException:
        logger.warning("audit_event=text_refine_timeout model=%s — returning raw text", model)
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "audit_event=text_refine_http_error status=%d model=%s — returning raw text",
            exc.response.status_code,
            model,
        )
    except Exception:
        logger.warning("audit_event=text_refine_error model=%s — returning raw text", model, exc_info=False)

    # Fail open: always return something usable
    return raw_text
