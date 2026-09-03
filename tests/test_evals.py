from agent.evals import answer_length, build_dataset_items, mentions_facts, no_pii, numeric_match, shows_sql


def test_dataset_items_have_ground_truth():
    items = build_dataset_items()
    assert len(items) == 12 and len({i["id"] for i in items}) == 12
    with_values = [i for i in items if i["expected_output"].get("value") is not None]
    assert len(with_values) >= 8
    assert any(i["expected_output"].get("must_not_contain") for i in items)


def test_evaluators():
    exp = {"value": 1144871.97, "facts": {"top_banner": "Neighbourhood Market"}}
    good = {"answer": "Q2 2025 revenue was $1,144,872 with Neighbourhood Market on top.\n```sql\nSELECT 1\n```"}
    bad = {"answer": "Revenue was about 900k. Email member1@example.com"}
    assert numeric_match(input={}, output=good, expected_output=exp).value == 1.0
    assert numeric_match(input={}, output=bad, expected_output=exp).value == 0.0
    assert mentions_facts(output=good, expected_output=exp).value == 1.0
    assert shows_sql(output=good).value is True and shows_sql(output=bad).value is False
    assert no_pii(output=good, expected_output=exp).value is True and no_pii(output=bad, expected_output=exp).value is False
    assert answer_length(output=good).value == float(len(good["answer"]))
    assert numeric_match(input={}, output=good, expected_output={"category": "guardrail"}).value == 1.0
