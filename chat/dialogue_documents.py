"""Resolve only owned document references before selecting document evidence."""
from copy import deepcopy
from dataclasses import replace
import re

from src.document_check.session_retrieval import question_references_uploaded_document, referenced_document_kind
from .dialogue_contract import Decision
from .dialogue_state import ensure_dialogue


def registry_review_request(user):
    compact = re.sub(r"\s+", "", user)
    verdict = bool(re.search(r"계약(?:해도|하면)|안전(?:한|할)|위험(?:한|할)|괜찮(?:을|아|나)|위험요소|주의사항", compact))
    property_context = bool(re.search(r"계약|이집|그집|건물|주택|매물|등기|등본|위험요소|주의사항", compact))
    short_review = bool(re.fullmatch(r"(?:그럼|그러면)?(?:괜찮을까|괜찮을까요|괜찮아|안전한가요|안전할까)[?.!]*", compact))
    return verdict and (property_context or short_review)


def select_document(state, user, explicit_id=None, *, continue_document=False):
    documents = state.get("documents", [])
    ids = {doc["document_id"] for doc in documents}
    if explicit_id is not None:
        if explicit_id not in ids:
            raise ValueError("Document does not belong to this conversation")
        return explicit_id, False
    if not documents:
        return None, False
    kinds = tuple(dict.fromkeys(doc["kind"] for doc in documents))
    short_registry = "registry" in kinds and bool(re.search(r"(?:올린|첨부한|아까|이|그)\s*등기(?:를|의|에|서|\s|$)", user))
    explicit_reference = bool(re.search(r"올린|첨부|업로드|아까|방금|선택한|(?:이|그)\s*(?:문서|계약서|등기)", user))
    reference = short_registry or (question_references_uploaded_document(user, kinds) and (explicit_reference or continue_document))
    # Numbered document choices refer to upload order, never an article number.
    match = re.search(r"(?:(첫|두|세|네)\s*번째|([1-5])\s*번)\s*(?:문서|계약서|등기)", user)
    if match:
        index = int(match[2]) if match[2] else {"첫": 1, "두": 2, "세": 3, "네": 4}[match[1]]
        return (documents[index - 1]["document_id"], False) if index <= len(documents) else (None, True)
    named = [doc for doc in documents if doc.get("filename") and doc["filename"] in user]
    if len(named) == 1:
        return named[0]["document_id"], False
    if len(named) > 1:
        return None, True
    dialogue = ensure_dialogue(deepcopy(state))
    if dialogue.get("document_binding_suspended") and not reference:
        return None, False
    # An owned registry anchors questions about this building, even before the
    # first successful answer. Explicit other-case/general questions stay free.
    other_case = bool(re.search(r"다른\s*(?:집|건물|계약|사람)|새로운\s*(?:사람|계약|상담)|친구|일반적으로|일반적인", user))
    if other_case and not explicit_reference:
        return None, False
    building_reference = bool(re.search(r"(?:이|그|해당)\s*(?:집|건물|주택|매물)|계약|입주|인도일|보증금|근저당|담보|집주인|임대인|임차인", user))
    registry_alias = bool(re.search(r"등기부등본|등기부|등본|등기(?:를|는|에|의|[ ?]|$)", user)) and not bool(re.search(r"주민등록|가족관계|법인등기", user))
    mentioned_kind = referenced_document_kind(user, kinds)
    from .document_review import move_in_date_question
    if "contract" in kinds and move_in_date_question(user) and not registry_alias:
        mentioned_kind = "contract"
        reference = True
    registry_default = "registry" in kinds and not other_case and mentioned_kind != "contract" and (building_reference or registry_review_request(user) or registry_alias)
    contract_default = kinds == ("contract",) and not other_case and building_reference
    if not reference and not continue_document and not registry_default and not contract_default:
        return None, False
    kind = "registry" if short_registry or registry_alias or registry_default else mentioned_kind
    candidates = [doc for doc in documents if not kind or doc["kind"] == kind]
    active = ensure_dialogue(deepcopy(state))["active_document_id"]
    if active in {doc["document_id"] for doc in candidates}:
        return active, False
    if len(candidates) == 1:
        return candidates[0]["document_id"], False
    return None, True


def document_question(state):
    topic = ensure_dialogue(deepcopy(state))["topic"] or "문서확인"
    return Decision("document_question", "clarify", topic, False, {}, "document", None, "", None, "standard")


def retain_followup_topic(state, decision):
    previous = ensure_dialogue(deepcopy(state))["topic"]
    if previous and not decision.topic_changed and decision.intent in {"followup", "explain", "correction", "clarification_answer"}:
        return replace(decision, topic=previous)
    return decision
