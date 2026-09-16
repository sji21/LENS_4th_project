from copy import deepcopy
import pytest
from src.evaluation.chatting_scenarios import load_suite, validate_suite


def test_suites_are_disjoint_and_have_multiturn_cases():
    dev, acceptance = load_suite("dev"), load_suite("acceptance")
    assert not {c["id"] for c in dev["cases"]} & {c["id"] for c in acceptance["cases"]}
    assert not {t["user"] for c in dev["cases"] for t in c["turns"]} & {t["user"] for c in acceptance["cases"] for t in c["turns"] if len(t["user"]) > 15}
    assert sum(len(c["turns"]) > 1 for c in dev["cases"]) >= 8
    assert len(acceptance["cases"]) >= 4


@pytest.mark.parametrize("mutation", [
    lambda x: x.update(schema_version=100),
    lambda x: x.update(cases=[]),
    lambda x: x["cases"].append(deepcopy(x["cases"][0])),
    lambda x: x["cases"][0]["turns"][0].update(user=""),
    lambda x: x["cases"][0]["turns"][0]["expect"].update(actions=["execute_arbitrary_tool"]),
    lambda x: x["cases"][0]["turns"][0]["expect"].update(facts={"amount": []}),
])
def test_malformed_suite_is_rejected(mutation):
    payload = load_suite()
    mutation(payload)
    with pytest.raises(ValueError):
        validate_suite(payload)


def test_cannot_load_arbitrary_path():
    with pytest.raises(ValueError):
        load_suite("../../.env")
