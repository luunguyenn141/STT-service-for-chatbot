"""Safe OpenAI post-processing for Vietnamese banking transcripts."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
import json
import logging
import re
import unicodedata
from typing import Final, Literal

import httpx

from app.models import IntentInterpretation
from app.services.intent_interpreter import interpret_transcript

logger = logging.getLogger("stt_poc.refiner")

RefinementStatus = Literal["refined", "unchanged", "fallback", "skipped"]


@dataclass(frozen=True)
class RefinementResult:
    text: str
    status: RefinementStatus
    interpretation: IntentInterpretation | None = None

    @property
    def changed(self) -> bool:
        return self.status == "refined"


_SYSTEM_PROMPT: Final[str] = (
    "Bạn chỉnh sửa bản chép lời tiếng Việt cho ứng dụng ngân hàng MSB. "
    "Chỉ sửa lỗi nhận dạng, dấu câu và cách viết; không suy diễn hoặc thêm thông tin.\n"
    "Bắt buộc giữ nguyên ý định, phủ định, tên người/tài khoản và mọi biểu thức số tiền. "
    "Không đổi chữ số thành chữ hoặc chữ thành số; dịch vụ sẽ định dạng tiền sau.\n"
    "Ưu tiên thuật ngữ ngân hàng và thực thể được cung cấp. Trong ngữ cảnh quản lý ngân sách, "
    "'hũ' là một phong bì ngân sách, không phải tên người; chỉ dùng đúng tên hũ trong danh sách. "
    "Ví dụ: 'từ hữu chi tiêu sang hữu ăn uống' có thể là 'từ hũ chi tiêu sang hũ ăn uống'. "
    "Nếu không chắc, giữ nguyên câu gốc."
)
_OPENAI_CHAT_URL: Final[str] = "https://api.openai.com/v1/chat/completions"
_DEFAULT_MODEL: Final[str] = "gpt-4o-mini"
_INTENT_HINTS: Final[dict[str, str]] = {
    "transfer_between_jars": (
        "Nếu câu nói về chuyển tiền giữa các hũ, giữ rõ cấu trúc số tiền, "
        "hũ nguồn sau 'từ' và hũ đích sau 'sang/vào/tới/đến'. "
        "Không tự điền thành phần người dùng chưa nói."
    ),
}

_OUTPUT_SCHEMA: Final[dict] = {
    "name": "stt_refinement",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "meaning_preserved": {"type": "boolean"},
            "amounts_preserved": {"type": "boolean"},
            "names_preserved": {"type": "boolean"},
            "negations_preserved": {"type": "boolean"},
        },
        "required": [
            "text",
            "meaning_preserved",
            "amounts_preserved",
            "names_preserved",
            "negations_preserved",
        ],
        "additionalProperties": False,
    },
}

_NUMBER_VALUES: Final[dict[str, int]] = {
    "không": 0, "một": 1, "mốt": 1, "hai": 2, "ba": 3, "bốn": 4,
    "tư": 4, "năm": 5, "lăm": 5, "sáu": 6, "bảy": 7, "tám": 8, "chín": 9,
}
_NUMBER_CANONICAL: Final[dict[str, str]] = {
    **{word: str(value) for word, value in _NUMBER_VALUES.items()},
    "mười": "10", "mươi": "x10", "trăm": "x100", "nghìn": "x1000",
    "ngàn": "x1000", "k": "x1000", "triệu": "x1000000",
    "tỷ": "x1000000000", "rưỡi": "+half",
}
_SCALES: Final[dict[str, Decimal]] = {
    "k": Decimal(1_000), "nghìn": Decimal(1_000), "ngàn": Decimal(1_000),
    "triệu": Decimal(1_000_000), "tỷ": Decimal(1_000_000_000),
}
_FILLER_WORDS: Final[set[str]] = {"linh", "lẻ"}
_CURRENCY_WORDS: Final[set[str]] = {"đồng", "vnd"}
_NEGATIONS: Final[set[str]] = {
    "không", "chưa", "chẳng", "chả", "đừng", "hủy", "huỷ", "bỏ", "thôi",
}
_WORD_RE: Final[re.Pattern[str]] = re.compile(r"\d+(?:[.,]\d+)*|[^\W\d_]+", re.UNICODE)
_JAR_PREFIXES: Final[set[str]] = {"hũ", "hữu", "hủ", "hu", "hú", "hụ"}
_ENTITY_MARKERS: Final[set[str]] = {"từ", "sang", "vào", "qua", "trong", "ở"}
_ENTITY_BOUNDARIES: Final[set[str]] = {"từ", "sang", "vào", "qua", "cho", "đến", "với"}


def _replace_configured(text: str, replacements: dict[str, str]) -> str:
    for source in sorted(replacements, key=len, reverse=True):
        if source.strip():
            text = re.sub(
                rf"(?<!\w){re.escape(source.strip())}(?!\w)", replacements[source], text,
                flags=re.IGNORECASE,
            )
    return text


def _apply_names(
    text: str,
    names: list[str],
    mode: Literal["title", "upper", "preserve"],
) -> str:
    def display(name: str) -> str:
        if mode == "upper":
            return name.upper()
        if mode == "title":
            return name.title()
        return name

    return _replace_configured(text, {name: display(name.strip()) for name in names if name.strip()})


def _decimal_token(token: str) -> Decimal | None:
    if not token or not token[0].isdigit():
        return None
    separators = [char for char in token if char in ".,"]
    try:
        if not separators:
            return Decimal(token)
        parts = re.split(r"[.,]", token)
        if all(len(part) == 3 for part in parts[1:]):
            return Decimal("".join(parts))
        if len(separators) == 1:
            return Decimal(token.replace(",", "."))
    except InvalidOperation:
        pass
    return None


def _parse_under_thousand(tokens: list[str]) -> Decimal | None:
    total = Decimal(0)
    pending: Decimal | None = None
    for token in tokens:
        if token in _FILLER_WORDS:
            continue
        numeric = _decimal_token(token)
        if numeric is None and token in _NUMBER_VALUES:
            numeric = Decimal(_NUMBER_VALUES[token])
        if numeric is not None:
            if pending is not None:
                return None
            pending = numeric
        elif token == "mười":
            if pending is not None:
                return None
            total += 10
        elif token == "mươi":
            total += (pending if pending is not None else Decimal(1)) * 10
            pending = None
        elif token == "trăm":
            total += (pending if pending is not None else Decimal(1)) * 100
            pending = None
        else:
            return None
    return total + (pending or Decimal(0))


def _parse_number(tokens: list[str]) -> Decimal | None:
    if not tokens:
        return Decimal(0)
    if len(tokens) >= 2 and tokens[-1] == "rưỡi" and tokens[-2] in _SCALES:
        base = _parse_number(tokens[:-1])
        return None if base is None else base + (_SCALES[tokens[-2]] / 2)
    for scale in ("tỷ", "triệu", "nghìn", "ngàn", "k"):
        if scale in tokens:
            index = tokens.index(scale)
            left = _parse_number(tokens[:index])
            right = _parse_number(tokens[index + 1 :])
            if left is None or right is None:
                return None
            return (left or Decimal(1)) * _SCALES[scale] + right
    return _parse_under_thousand(tokens)


def _format_vnd(amount: Decimal, separator: Literal[".", ",", " ", ""]) -> str | None:
    if amount < 0 or amount != amount.to_integral_value():
        return None
    grouped = f"{int(amount):,}"
    if separator != ",":
        grouped = grouped.replace(",", separator)
    return f"{grouped} VND"


def normalize_vnd(text: str, separator: Literal[".", ",", " ", ""] = ".") -> str:
    """Normalize explicit Vietnamese money expressions without guessing a unit."""
    matches = list(_WORD_RE.finditer(text))
    runs: list[list[re.Match[str]]] = []
    current: list[re.Match[str]] = []

    def is_amount_token(value: str) -> bool:
        return (
            value[0].isdigit() or value in _NUMBER_VALUES or value in _SCALES
            or value in _FILLER_WORDS or value in _CURRENCY_WORDS
            or value in {"mười", "mươi", "trăm", "rưỡi"}
        )

    for match in matches:
        token = match.group(0).casefold()
        gap = text[current[-1].end() : match.start()] if current else ""
        if current and (not is_amount_token(token) or not re.fullmatch(r"[\s-]*", gap)):
            runs.append(current)
            current = []
        if is_amount_token(token):
            current.append(match)
    if current:
        runs.append(current)

    replacements: list[tuple[int, int, str]] = []
    for run in runs:
        tokens = [match.group(0).casefold() for match in run]
        has_currency = any(token in _CURRENCY_WORDS for token in tokens)
        has_scale = any(token in _SCALES for token in tokens)
        has_number = any(token[0].isdigit() or token in _NUMBER_VALUES or token == "mười" for token in tokens)
        if not has_number or not (has_currency or has_scale):
            continue
        amount = _parse_number([token for token in tokens if token not in _CURRENCY_WORDS])
        formatted = _format_vnd(amount, separator) if amount is not None else None
        if formatted:
            replacements.append((run[0].start(), run[-1].end(), formatted))

    for start, end, replacement in reversed(replacements):
        text = text[:start] + replacement + text[end:]
    return text


def _protected_numbers(text: str) -> list[str]:
    protected: list[str] = []
    for match in _WORD_RE.finditer(text.casefold()):
        token = match.group(0)
        if token[0].isdigit():
            value = _decimal_token(token)
            protected.append(f"n:{value}" if value is not None else f"n:{token}")
        elif token in _NUMBER_CANONICAL:
            protected.append(_NUMBER_CANONICAL[token])
    return protected


def _negations(text: str) -> Counter[str]:
    return Counter(
        match.group(0).casefold() for match in _WORD_RE.finditer(text)
        if match.group(0).casefold() in _NEGATIONS
    )


def _contains_name(text: str, name: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(name.strip())}(?!\w)", text, re.IGNORECASE) is not None


def _fold(value: str) -> str:
    value = value.casefold().replace("đ", "d")
    return "".join(char for char in unicodedata.normalize("NFD", value) if unicodedata.category(char) != "Mn")


def _tokens(value: str) -> list[str]:
    return [_fold(match.group(0)) for match in _WORD_RE.finditer(value)]


def _jar_entities(values: list[str]) -> list[str]:
    """Return unique canonical `hũ <label>` entities supplied by the product."""
    entities: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = " ".join(value.strip().split())
        words = clean.split()
        if len(words) < 2 or _fold(words[0]) != "hu":
            continue
        canonical = f"hũ {' '.join(words[1:])}"
        key = _fold(canonical)
        if key not in seen:
            seen.add(key)
            entities.append(canonical)
    return entities


def _entity_spans(text: str) -> list[tuple[int, int]]:
    """Find short noun phrases after transfer/location markers."""
    words = list(_WORD_RE.finditer(text))
    spans: list[tuple[int, int]] = []
    for index, word in enumerate(words):
        if word.group(0).casefold() not in _ENTITY_MARKERS or index + 1 >= len(words):
            continue
        end_index = index + 1
        while end_index + 1 < len(words) and end_index - index < 5:
            next_word = words[end_index + 1].group(0).casefold()
            if next_word in _ENTITY_BOUNDARIES:
                break
            gap = text[words[end_index].end():words[end_index + 1].start()]
            if not re.fullmatch(r"[\s&-]*", gap):
                break
            end_index += 1
        spans.append((words[index + 1].start(), words[end_index].end()))
    return spans


def canonicalize_domain_entities(
    text: str,
    entities: list[str] | None,
    *,
    min_similarity: float = 0.76,
) -> tuple[str, list[str]]:
    """Correct contextual jar homophones against a closed product vocabulary."""
    canonical = _jar_entities(entities or [])
    if not canonical:
        return text, []

    replacements: list[tuple[int, int, str]] = []
    for start, end in _entity_spans(text):
        phrase = text[start:end]
        phrase_matches = list(_WORD_RE.finditer(phrase))
        phrase_words = [_fold(match.group(0)) for match in phrase_matches]
        if not phrase_words or phrase_words[0] not in {_fold(value) for value in _JAR_PREFIXES}:
            continue
        ranked: list[tuple[float, str, int]] = []
        for entity in canonical:
            entity_word_count = len(_tokens(entity))
            if len(phrase_matches) < entity_word_count:
                continue
            candidate_end = phrase_matches[entity_word_count - 1].end()
            candidate_phrase = phrase[:candidate_end]
            ranked.append((SequenceMatcher(None, _fold(candidate_phrase), _fold(entity)).ratio(), entity, candidate_end))
        if not ranked:
            continue
        ranked.sort(reverse=True)
        best_score, best_entity, best_end = ranked[0]
        runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
        # A close runner-up means the audio is ambiguous; preserve the source.
        if best_score >= min_similarity and (best_score - runner_up >= 0.08 or best_score >= 0.95):
            replacements.append((start, start + best_end, best_entity))

    for start, end, replacement in reversed(replacements):
        text = text[:start] + replacement + text[end:]
    return text, canonical


def _entity_counts(text: str, entities: list[str]) -> Counter[str]:
    casefolded = text.casefold()
    return Counter({
        entity.casefold(): len(re.findall(rf"(?<!\w){re.escape(entity.casefold())}(?!\w)", casefolded))
        for entity in entities
        if re.search(rf"(?<!\w){re.escape(entity.casefold())}(?!\w)", casefolded)
    })


def _locally_safe(
    source: str,
    candidate: str,
    names: list[str],
    entities: list[str],
    min_similarity: float,
) -> bool:
    if not candidate.strip() or len(candidate) < max(1, len(source) // 2) or len(candidate) > max(80, len(source) * 2):
        return False
    if _protected_numbers(source) != _protected_numbers(candidate):
        return False
    if _negations(source) != _negations(candidate):
        return False
    if _entity_counts(source, entities) != _entity_counts(candidate, entities):
        return False
    if SequenceMatcher(None, _tokens(source), _tokens(candidate)).ratio() < min_similarity:
        return False
    return all(
        not name.strip() or not _contains_name(source, name) or _contains_name(candidate, name)
        for name in names
    )


def _finalize(
    text: str,
    *,
    normalize_money: bool,
    money_separator: Literal[".", ",", " ", ""],
    names: list[str],
    name_case: Literal["title", "upper", "preserve"],
) -> str:
    if normalize_money:
        text = normalize_vnd(text, money_separator)
    return _apply_names(text.strip(), names, name_case)


def _intent_signature(value: IntentInterpretation | None) -> tuple | None:
    """Keep the model from changing the roles or completeness of an action."""
    if value is None:
        return None
    slots = tuple(sorted(
        (slot.name, slot.entity_id, str(slot.value))
        for slot in value.slots
    ))
    return (
        value.intent,
        value.status,
        value.negated,
        slots,
        tuple(value.missing_slots),
        tuple(value.ambiguous_slots),
    )


async def refine_transcript(
    raw_text: str,
    *,
    openai_api_key: str,
    keyterms: list[str] | None = None,
    model: str = _DEFAULT_MODEL,
    timeout_seconds: float = 8.0,
    max_chars: int = 6000,
    normalize_money: bool = True,
    money_separator: Literal[".", ",", " ", ""] = ".",
    name_case: Literal["title", "upper", "preserve"] = "title",
    names: list[str] | None = None,
    term_aliases: dict[str, str] | None = None,
    entities: list[str] | None = None,
    context_entities: list[dict] | None = None,
    enabled_intents: list[str] | None = None,
    min_similarity: float = 0.72,
) -> RefinementResult:
    """Refine a transcript and fall back to deterministic, locally safe formatting."""
    if not raw_text.strip():
        return RefinementResult(raw_text, "unchanged")

    configured_names = names or []
    dynamic_entity_terms: list[str] = []
    for entity in context_entities or []:
        if not isinstance(entity, dict) or entity.get("type") != "budget_jar":
            continue
        label = entity.get("label")
        if isinstance(label, str) and label.strip():
            dynamic_entity_terms.append(f"hũ {' '.join(label.split())}")
        aliases = entity.get("aliases", [])
        if isinstance(aliases, list):
            dynamic_entity_terms.extend(alias for alias in aliases if isinstance(alias, str) and alias.strip())
    source = _replace_configured(raw_text.strip(), term_aliases or {})
    source, canonical_entities = canonicalize_domain_entities(
        source,
        (entities or []) + (keyterms or []) + dynamic_entity_terms,
    )
    finalize_options = {
        "normalize_money": normalize_money,
        "money_separator": money_separator,
        "names": configured_names,
        "name_case": name_case,
    }
    fallback = _finalize(source, **finalize_options)
    def interpretation_for(value: str) -> IntentInterpretation | None:
        return interpret_transcript(
            value,
            context_entities=context_entities,
            enabled_intents=enabled_intents,
        )
    source_interpretation = interpretation_for(fallback)
    if len(source) > max_chars:
        logger.info("audit_event=text_refine_skipped reason=input_too_long input_len=%d", len(source))
        return RefinementResult(fallback, "skipped", interpretation_for(fallback))

    keyterm_hint = f"\nThuật ngữ ưu tiên: {', '.join((keyterms or [])[:50])}." if keyterms else ""
    entity_hint = (
        f"\nTên hũ hợp lệ (không được đổi sang tên người hoặc tên khác): {', '.join(canonical_entities[:30])}."
        if canonical_entities else ""
    )
    intent_hints = [
        _INTENT_HINTS[intent] for intent in (enabled_intents or []) if intent in _INTENT_HINTS
    ]
    intent_hint = f"\nNgữ cảnh tác vụ: {' '.join(intent_hints)}" if intent_hints else ""
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                _OPENAI_CHAT_URL,
                headers={"Authorization": f"Bearer {openai_api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": f"Chỉnh sửa bản chép lời sau.{keyterm_hint}{entity_hint}{intent_hint}\n\n{source}"},
                    ],
                    "temperature": 0.0,
                    "max_tokens": min(4096, max(256, len(source) * 2)),
                    "response_format": {"type": "json_schema", "json_schema": _OUTPUT_SCHEMA},
                },
            )
            response.raise_for_status()
            choice = response.json()["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ValueError("incomplete model output")
            payload = json.loads(choice["message"]["content"])
            flags = [
                payload.get("meaning_preserved"), payload.get("amounts_preserved"),
                payload.get("names_preserved"), payload.get("negations_preserved"),
            ]
            candidate = payload.get("text", "")
            if not isinstance(candidate, str) or not all(flag is True for flag in flags):
                raise ValueError("semantic preservation was not guaranteed")
            if not _locally_safe(source, candidate, configured_names, canonical_entities, min_similarity):
                raise ValueError("local semantic guard rejected output")
            final = _finalize(candidate, **finalize_options)
            candidate_interpretation = interpretation_for(final)
            if _intent_signature(source_interpretation) != _intent_signature(candidate_interpretation):
                raise ValueError("intent roles or completeness changed")
            status: RefinementStatus = "refined" if final != raw_text.strip() else "unchanged"
            logger.info(
                "audit_event=text_refine_success model=%s status=%s input_len=%d output_len=%d",
                model, status, len(raw_text), len(final),
            )
            return RefinementResult(final, status, candidate_interpretation)
    except httpx.TimeoutException:
        reason = "timeout"
    except httpx.HTTPStatusError as exc:
        reason = f"http_{exc.response.status_code}"
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        reason = "invalid_output"
    except Exception:
        reason = "error"

    logger.warning("audit_event=text_refine_fallback model=%s reason=%s", model, reason)
    return RefinementResult(fallback, "fallback", interpretation_for(fallback))


async def refine_text(raw_text: str, **kwargs) -> str:
    """Text-only wrapper retained for simple callers."""
    return (await refine_transcript(raw_text, **kwargs)).text
