from __future__ import annotations

import json

import pytest

from app.services import transcript_refiner


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, response, captured, **_):
        self.response = response
        self.captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def post(self, url, **kwargs):
        self.captured.update({"url": url, **kwargs})
        return self.response


def _mock_openai(monkeypatch, *, text: str, finish_reason: str = "stop", flags: bool = True):
    captured = {}
    content = json.dumps({
        "text": text,
        "meaning_preserved": flags,
        "amounts_preserved": flags,
        "names_preserved": flags,
        "negations_preserved": flags,
    })
    response = FakeResponse({
        "choices": [{"finish_reason": finish_reason, "message": {"content": content}}],
    })
    monkeypatch.setattr(
        transcript_refiner.httpx,
        "AsyncClient",
        lambda **kwargs: FakeClient(response, captured, **kwargs),
    )
    return captured


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Chuyển năm trăm nghìn cho An", "Chuyển 500.000 VND cho An"),
        ("Chuyển một triệu hai trăm nghìn đồng", "Chuyển 1.200.000 VND"),
        ("Gửi hai triệu rưỡi", "Gửi 2.500.000 VND"),
        ("Chuyển 500k", "Chuyển 500.000 VND"),
        ("Chuyển năm trăm cho An", "Chuyển năm trăm cho An"),
    ],
)
def test_normalize_vnd_only_formats_explicit_money(source, expected):
    assert transcript_refiner.normalize_vnd(source) == expected


@pytest.mark.asyncio
async def test_refinement_uses_strict_output_then_formats_money_and_names(monkeypatch):
    captured = _mock_openai(
        monkeypatch,
        text="Chuyển năm trăm nghìn cho nguyễn văn an.",
    )

    result = await transcript_refiner.refine_transcript(
        "chuyển năm trăm nghìn cho nguyễn văn an",
        openai_api_key="secret",
        keyterms=["chuyển khoản"],
        names=["nguyễn văn an"],
    )

    assert result.text == "Chuyển 500.000 VND cho Nguyễn Văn An."
    assert result.status == "refined"
    assert captured["json"]["response_format"]["type"] == "json_schema"
    assert captured["json"]["response_format"]["json_schema"]["strict"] is True
    assert "chuyển khoản" in captured["json"]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_changed_amount_is_rejected_and_safe_formatting_still_runs(monkeypatch):
    _mock_openai(monkeypatch, text="Chuyển bảy trăm nghìn cho An.")

    result = await transcript_refiner.refine_transcript(
        "Chuyển năm trăm nghìn cho An",
        openai_api_key="secret",
    )

    assert result.text == "Chuyển 500.000 VND cho An"
    assert result.status == "fallback"


@pytest.mark.asyncio
async def test_truncated_output_falls_back_to_configured_aliases(monkeypatch):
    _mock_openai(monkeypatch, text="MSB", finish_reason="length")

    result = await transcript_refiner.refine_transcript(
        "em ét bê",
        openai_api_key="secret",
        term_aliases={"em ét bê": "MSB"},
    )

    assert result.text == "MSB"
    assert result.status == "fallback"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "chuyển 500 nghìn từ hữu chi tiêu sang hữu ăn uống",
            "chuyển 500 nghìn từ hũ chi tiêu sang hũ ăn uống",
        ),
        (
            "chuyển 500 nghìn từ hủ thiết íu sang hũ tiết kiệm hôm nay",
            "chuyển 500 nghìn từ hũ thiết yếu sang hũ tiết kiệm hôm nay",
        ),
        (
            "chuyển 500 nghìn cho Nguyễn Văn Hữu",
            "chuyển 500 nghìn cho Nguyễn Văn Hữu",
        ),
    ],
)
def test_canonicalize_jar_homophones_only_in_entity_context(source, expected):
    text, _ = transcript_refiner.canonicalize_domain_entities(
        source,
        ["hũ chi tiêu", "hũ ăn uống", "hũ thiết yếu", "hũ tiết kiệm"],
    )
    assert text == expected


@pytest.mark.asyncio
async def test_hallucinated_jar_names_are_rejected_with_deterministic_fallback(monkeypatch):
    _mock_openai(
        monkeypatch,
        text="Chuyển 500 nghìn từ Hữu Chí Tiêu sang Hữu Văn Hoàng.",
    )

    result = await transcript_refiner.refine_transcript(
        "chuyển 500 nghìn từ hữu chi tiêu sang hữu ăn uống",
        openai_api_key="secret",
        entities=["hũ chi tiêu", "hũ ăn uống"],
    )

    assert result.text == "chuyển 500.000 VND từ hũ chi tiêu sang hũ ăn uống"
    assert result.status == "fallback"


@pytest.mark.asyncio
async def test_valid_jar_entities_are_kept_and_included_in_prompt(monkeypatch):
    captured = _mock_openai(
        monkeypatch,
        text="Chuyển 500 nghìn từ hũ chi tiêu sang hũ ăn uống.",
    )

    result = await transcript_refiner.refine_transcript(
        "chuyển 500 nghìn từ hữu chi tiêu sang hữu ăn uống",
        openai_api_key="secret",
        entities=["hũ chi tiêu", "hũ ăn uống"],
    )

    assert result.text == "Chuyển 500.000 VND từ hũ chi tiêu sang hũ ăn uống."
    assert result.status == "refined"
    prompt = captured["json"]["messages"][1]["content"]
    assert "Tên hũ hợp lệ" in prompt
    assert "hũ chi tiêu" in prompt
    assert "hũ ăn uống" in prompt
