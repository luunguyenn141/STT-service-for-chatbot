from app.services.intent_interpreter import interpret_transcript


ENTITIES = [
    {"id": "daily", "type": "budget_jar", "label": "Chi tiêu hằng ngày", "aliases": ["hũ Chi tiêu hằng ngày"]},
    {"id": "bali", "type": "budget_jar", "label": "Du lịch Bali", "aliases": ["hũ Du lịch Bali"]},
]


def interpret(text: str, entities=None):
    return interpret_transcript(
        text,
        context_entities=entities or ENTITIES,
        enabled_intents=["transfer_between_jars"],
    )


def test_custom_jars_are_resolved_to_dynamic_ids():
    result = interpret("Chuyển 500.000 VND từ hũ Chi tiêu hằng ngày sang hũ Du lịch Bali")
    assert result is not None
    assert result.status == "complete"
    assert result.actionable is True
    slots = {slot.name: slot for slot in result.slots}
    assert slots["amount"].value == 500_000
    assert slots["source"].entity_id == "daily"
    assert slots["destination"].entity_id == "bali"


def test_missing_slot_returns_a_specific_clarification():
    result = interpret("Chuyển 500.000 VND sang hũ Du lịch Bali")
    assert result is not None
    assert result.status == "incomplete"
    assert result.actionable is False
    assert result.missing_slots == ["source"]
    assert result.clarification == "Bạn muốn chuyển tiền từ hũ nào?"


def test_multiple_entity_matches_are_ambiguous_instead_of_guessed():
    entities = [
        {"id": "trip-1", "type": "budget_jar", "label": "Du lịch Bali", "aliases": []},
        {"id": "trip-2", "type": "budget_jar", "label": "Du lịch Bali", "aliases": []},
        ENTITIES[0],
    ]
    result = interpret("Chuyển 500.000 VND từ hũ Chi tiêu hằng ngày sang hũ Du lịch Bali", entities)
    assert result is not None
    assert result.status == "ambiguous"
    assert result.ambiguous_slots == ["destination"]


def test_person_name_huu_is_not_treated_as_a_jar_transfer():
    assert interpret("Chuyển 500.000 VND cho Nguyễn Văn Hữu") is None


def test_negated_complete_command_is_not_actionable():
    result = interpret("Đừng chuyển 500.000 VND từ hũ Chi tiêu hằng ngày sang hũ Du lịch Bali")
    assert result is not None
    assert result.status == "complete"
    assert result.negated is True
    assert result.actionable is False
    assert result.clarification == "Mình hiểu là bạn không muốn thực hiện giao dịch này, nên mình chưa gửi đi."


def test_same_source_and_destination_requires_a_different_destination():
    result = interpret(
        "Chuyển 500.000 VND từ hũ Chi tiêu hằng ngày sang hũ Chi tiêu hằng ngày",
    )
    assert result is not None
    assert result.status == "ambiguous"
    assert result.actionable is False
    assert result.ambiguous_slots == ["destination"]
    assert result.clarification == "Hũ nguồn và hũ đích đang trùng nhau. Bạn muốn chuyển tiền tới hũ nào khác?"
