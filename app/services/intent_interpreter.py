"""Validated, extensible intent extraction for finalized transcripts.

OpenAI may repair wording before this module runs, but business slots are
derived and validated locally. New voice actions are added as independent
handlers in ``_INTERPRETERS`` rather than by expanding one global prompt.
"""
from __future__ import annotations

from collections.abc import Callable
import re
import unicodedata

from app.models import IntentInterpretation, IntentSlot

TRANSFER_BETWEEN_JARS = "transfer_between_jars"
SUPPORTED_INTENTS = frozenset({TRANSFER_BETWEEN_JARS})

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_AMOUNT_RE = re.compile(r"(?<!\w)(\d{1,3}(?:[., ]\d{3})+|\d+)\s*VND(?!\w)", re.IGNORECASE)
_SOURCE_RE = re.compile(
    r"(?<!\w)từ(?!\w)\s+(.*?)(?=(?<!\w)(?:sang|vào|tới|đến|qua)(?!\w)|$)",
    re.IGNORECASE,
)
_DESTINATION_RE = re.compile(
    r"(?<!\w)(?:sang|vào|tới|đến|qua)(?!\w)\s+(.+)$",
    re.IGNORECASE,
)
_NEGATIONS = frozenset({"khong", "chua", "chang", "cha", "dung", "huy", "bo", "thoi"})


def _fold(value: str) -> str:
    value = value.casefold().replace("đ", "d")
    return "".join(char for char in unicodedata.normalize("NFD", value) if unicodedata.category(char) != "Mn")


def _words(value: str) -> list[str]:
    return [_fold(match.group(0)) for match in _WORD_RE.finditer(value)]


def _clean_entities(values: list[dict] | None) -> list[dict]:
    cleaned: list[dict] = []
    seen: set[str] = set()
    for value in values or []:
        if not isinstance(value, dict):
            continue
        entity_id = value.get("id")
        entity_type = value.get("type")
        label = value.get("label")
        aliases = value.get("aliases", [])
        if not all(isinstance(item, str) and item.strip() for item in (entity_id, entity_type, label)):
            continue
        if entity_id in seen or not isinstance(aliases, list):
            continue
        seen.add(entity_id)
        cleaned.append({
            "id": entity_id,
            "type": entity_type,
            "label": " ".join(label.split()),
            "aliases": [" ".join(alias.split()) for alias in aliases if isinstance(alias, str) and alias.strip()],
        })
    return cleaned


def _entity_phrases(entity: dict) -> list[str]:
    label = entity["label"]
    values = [label, f"hũ {label}", *entity["aliases"]]
    unique: dict[str, str] = {}
    for value in values:
        clean = " ".join(value.split())
        if clean:
            unique.setdefault(_fold(clean), clean)
    return sorted(unique.values(), key=len, reverse=True)


def _match_entity(segment: str, entities: list[dict]) -> tuple[IntentSlot | None, bool]:
    matches: dict[str, tuple[dict, str]] = {}
    folded_segment = _fold(segment)
    for entity in entities:
        for phrase in _entity_phrases(entity):
            folded_phrase = _fold(phrase)
            if re.search(rf"(?<!\w){re.escape(folded_phrase)}(?!\w)", folded_segment):
                matches[entity["id"]] = (entity, phrase)
                break
    if not matches:
        return None, False
    longest = max(len(_words(phrase)) for _, phrase in matches.values())
    winners = [match for match in matches.values() if len(_words(match[1])) == longest]
    if len(winners) != 1:
        return None, True
    entity, phrase = winners[0]
    return IntentSlot(
        name="",
        value=entity["label"],
        entity_id=entity["id"],
        source_text=phrase,
        confidence=1.0,
    ), False


def _amount_slot(text: str) -> IntentSlot | None:
    match = _AMOUNT_RE.search(text)
    if not match:
        return None
    digits = re.sub(r"[., ]", "", match.group(1))
    try:
        amount = int(digits)
    except ValueError:
        return None
    if amount <= 0:
        return None
    return IntentSlot(
        name="amount",
        value=amount,
        source_text=match.group(0),
        confidence=1.0,
    )


def _clarification(missing: list[str], ambiguous: list[str]) -> str:
    if ambiguous:
        labels = {"source": "hũ nguồn", "destination": "hũ đích"}
        readable = " và ".join(labels.get(slot, slot) for slot in ambiguous)
        return f"Mình nghe thấy nhiều lựa chọn cho {readable}. Bạn vui lòng nói rõ tên hũ nhé."
    if missing == ["amount"]:
        return "Bạn muốn chuyển bao nhiêu tiền?"
    if missing == ["source"]:
        return "Bạn muốn chuyển tiền từ hũ nào?"
    if missing == ["destination"]:
        return "Bạn muốn chuyển tiền tới hũ nào?"
    labels = {"amount": "số tiền", "source": "hũ nguồn", "destination": "hũ đích"}
    readable = ", ".join(labels[slot] for slot in missing[:-1])
    if len(missing) > 1:
        readable = f"{readable} và {labels[missing[-1]]}" if readable else labels[missing[-1]]
    return f"Mình cần thêm {readable}. Hãy nói lại đầy đủ để mình hiểu chính xác."


def _interpret_transfer_between_jars(text: str, context_entities: list[dict]) -> IntentInterpretation | None:
    words = _words(text)
    entities = [entity for entity in _clean_entities(context_entities) if entity["type"] == "budget_jar"]
    mentions_jar = "hu" in words or any(
        re.search(rf"(?<!\w){re.escape(_fold(entity['label']))}(?!\w)", _fold(text))
        for entity in entities
    )
    if "chuyen" not in words or not mentions_jar:
        return None

    slots: list[IntentSlot] = []
    amount = _amount_slot(text)
    if amount:
        slots.append(amount)

    ambiguous: list[str] = []
    source_match = _SOURCE_RE.search(text)
    if source_match:
        source, is_ambiguous = _match_entity(source_match.group(1), entities)
        if source:
            source.name = "source"
            slots.append(source)
        elif is_ambiguous:
            ambiguous.append("source")

    destination_match = _DESTINATION_RE.search(text)
    if destination_match:
        destination, is_ambiguous = _match_entity(destination_match.group(1), entities)
        if destination:
            destination.name = "destination"
            slots.append(destination)
        elif is_ambiguous:
            ambiguous.append("destination")

    by_name = {slot.name: slot for slot in slots}
    source = by_name.get("source")
    destination = by_name.get("destination")
    same_jar = bool(
        source
        and destination
        and source.entity_id
        and source.entity_id == destination.entity_id
    )
    if same_jar:
        ambiguous.append("destination")

    present = {slot.name for slot in slots}
    missing = [slot for slot in ("amount", "source", "destination") if slot not in present and slot not in ambiguous]
    status = "ambiguous" if ambiguous else "incomplete" if missing else "complete"
    negated = any(word in _NEGATIONS for word in words)
    actionable = status == "complete" and not negated
    confidence = 1.0 if status == "complete" else 0.85 if slots else 0.65
    if same_jar:
        clarification = "Hũ nguồn và hũ đích đang trùng nhau. Bạn muốn chuyển tiền tới hũ nào khác?"
    elif negated and status == "complete":
        clarification = "Mình hiểu là bạn không muốn thực hiện giao dịch này, nên mình chưa gửi đi."
    else:
        clarification = None if status == "complete" else _clarification(missing, ambiguous)
    return IntentInterpretation(
        intent=TRANSFER_BETWEEN_JARS,
        status=status,
        confidence=confidence,
        actionable=actionable,
        negated=negated,
        slots=slots,
        missing_slots=missing,
        ambiguous_slots=ambiguous,
        clarification=clarification,
    )


_INTERPRETERS: dict[str, Callable[[str, list[dict]], IntentInterpretation | None]] = {
    TRANSFER_BETWEEN_JARS: _interpret_transfer_between_jars,
}


def interpret_transcript(
    text: str,
    *,
    context_entities: list[dict] | None = None,
    enabled_intents: list[str] | None = None,
) -> IntentInterpretation | None:
    """Return the first locally validated interpretation for enabled intents."""
    for intent in enabled_intents or []:
        interpreter = _INTERPRETERS.get(intent)
        if interpreter:
            result = interpreter(text, context_entities or [])
            if result:
                return result
    return None
