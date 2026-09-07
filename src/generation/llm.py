"""로컬 양자화 LLM 연결 — Qwen3-8B (Q4_K_M) on Ollama.

실제 생성은 Ollama native `/api/chat` 엔드포인트를 사용한다.

기존에는 `langchain-openai` 의 ChatOpenAI 로 Ollama `/v1` 호환 API를 사용했지만,
Qwen3 의 `think=false` 가 실제 호출에서 적용되지 않아 내부 사고 과정이 출력 토큰을
소진하고 최종 `content` 가 빈 문자열로 끝나는 문제가 확인되었다.

native `/api/chat` 에서는 `think=false` 가 정상 적용되는 것을 확인했으므로,
LangChain prompt/chain 구조는 그대로 유지하면서 실제 HTTP 호출만 native API로 바꾼다.
별도 `langchain-ollama` 의존성은 추가하지 않는다.

`strip_reasoning()` 은 `think=false` 가 적용되더라도 모델·버전 차이에 대비한 최종
후처리 안전장치로 유지한다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

# ── 설정값 (환경 변수로 덮어쓸 수 있다) ──────────────────────────

logger = logging.getLogger(__name__)


def _env_number(name: str, default: str, cast):
    """숫자 환경 변수를 읽는다. 비어 있거나 숫자가 아니면 기본값으로 돌아간다."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        raw = default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        logger.warning("%s 값이 숫자가 아니어서 기본값 %s 를 씁니다: %r", name, default, raw)
        return cast(default)


def _env_text(name: str, default: str) -> str:
    """문자열 환경 변수를 읽고 공백 값이면 안전한 기본값을 사용한다."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


# 로컬 Ollama가 기본값이고, RunPod HTTP 프록시나 SSH 터널 주소로 덮어쓸 수 있다.
LLM_BASE_URL = _env_text("JEONSEON_LLM_BASE_URL", "http://localhost:11434/v1")
LLM_MODEL = os.getenv("JEONSEON_LLM_MODEL", "qwen3:8b-q4_K_M")

# 법령의 조건·시점 표현이 매 실행마다 달라지지 않도록 기본 생성은 결정적으로 한다.
LLM_TEMPERATURE = _env_number("JEONSEON_LLM_TEMPERATURE", "0.0", float)
LLM_TIMEOUT = _env_number("JEONSEON_LLM_TIMEOUT", "180", float)
LLM_MAX_TOKENS = _env_number("JEONSEON_LLM_MAX_TOKENS", "256", int)
# 현재 RAG 프롬프트를 담으면서 KV 캐시 적재를 최소화한다.
LLM_NUM_CTX = _env_number("JEONSEON_LLM_NUM_CTX", "4096", int)
LLM_KEEP_ALIVE = os.getenv("JEONSEON_LLM_KEEP_ALIVE", "30m").strip() or "30m"
OLLAMA_USER_AGENT = "PATCH32-Streamlit/1.0"
LOCAL_OLLAMA_BASE_URL = "http://localhost:11434"
# RunPod가 꺼져 있을 때 180초 생성 timeout까지 기다리지 않고 로컬로 전환한다.
OLLAMA_FAILOVER_PROBE_TIMEOUT = 3.0

# 같은 Streamlit 프로세스에서 매 LLM 호출마다 RunPod 상태를 다시 조회하지 않는다.
# 실제 생성 호출이 실패하면 캐시를 버리고 로컬로 한 번 더 시도한다.
_ROUTE_CACHE: dict[tuple[str, str], str] = {}


def _env_flag(name: str, default: str = "1") -> bool:
    """환경 변수를 참/거짓으로 읽는다. 대소문자와 흔한 표기를 모두 받는다."""
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off", "")


THINK_OFF = _env_flag("JEONSEON_LLM_NO_THINK")


# ── 사고 과정 제거 ────────────────────────────────────────────

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    """Qwen3 의 `<think>...</think>` 구간을 잘라낸다."""
    if not text:
        return ""
    cleaned = _THINK_BLOCK.sub("", text)
    cleaned = _THINK_OPEN.sub("", cleaned)
    return cleaned.strip()


_SENTENCE_END = (".", "!", "?", "\u201d", '"', "]", ")")
_HANGUL = re.compile(r"[가-힣]")
_CUT_ENDS = (".", "!", "?", "\u201d")


def _ends_completely(text: str) -> bool:
    """문장이 제대로 끝났는가. 곧은 따옴표는 짝이 맞을 때만 끝으로 본다."""
    if not text.endswith(_SENTENCE_END):
        return False
    if text.endswith('"') and text.count('"') % 2 == 1:
        return False
    return True


_MARKDOWN_NOISE = re.compile(r"[#*>\-\s\d.)(]+")
_HEADING_MARKER = re.compile(r"^\s*(?:#{1,6}|\*\*|\d+[.)])")


def _is_stub_line(line: str) -> bool:
    """제목만 쓰고 내용이 시작되기 전에 끊긴 줄인가."""
    stripped = line.strip()
    if not stripped:
        return True
    if not _HEADING_MARKER.match(stripped):
        return False
    if len(_HANGUL.findall(stripped)) >= 2 and stripped.endswith(_SENTENCE_END):
        return False
    return len(_MARKDOWN_NOISE.sub("", stripped)) < 8


_MARKDOWN_LINK = re.compile(r"\[([^\]\n]+)\]\([^)\n]*\)")


def unlink(text: str) -> str:
    """마크다운 링크에서 주소를 벗겨내고 글자만 남긴다."""
    return _MARKDOWN_LINK.sub(r"\1", text)


def _drop_unclosed_bracket(text: str) -> str:
    """닫히지 않은 괄호부터 뒤를 버린다. 단, 잘라낼 양이 절반을 넘으면 두고 본다."""
    for opener, closer in (("(", ")"), ("[", "]")):
        depth = 0
        unclosed = -1
        for i, ch in enumerate(text):
            if ch == opener:
                if depth == 0:
                    unclosed = i
                depth += 1
            elif ch == closer and depth:
                depth -= 1
        if depth and unclosed > len(text) * 0.5:
            text = text[:unclosed].rstrip()
    return text


def trim_to_last_sentence(text: str) -> str:
    """토큰 상한에 걸려 중간에서 끊긴 답변을 마지막 완성 지점까지 되돌린다."""
    if not text:
        return ""
    text = text.rstrip()

    lines = text.split("\n")
    while len(lines) > 1 and _is_stub_line(lines[-1]):
        lines.pop()
    text = "\n".join(lines).rstrip()

    text = _drop_unclosed_bracket(text)

    if _ends_completely(text):
        return text

    cut = max(text.rfind(end) for end in _CUT_ENDS)
    if cut > len(text) * 0.5:
        return text[: cut + 1]
    return text


def clean_output(text: str) -> str:
    """모델 출력을 화면에 내보낼 수 있는 상태로 만든다."""
    return trim_to_last_sentence(unlink(strip_reasoning(text)))



def _extra_body() -> dict:
    """서버에 전달할 생성 제어값을 만든다.

    기존 테스트/호출부가 이 함수를 사용하므로 인터페이스를 유지한다.
    native API에서는 max_tokens를 options.num_predict로, think를 top-level think로 변환한다.
    """
    body: dict = {"max_tokens": LLM_MAX_TOKENS, "num_ctx": LLM_NUM_CTX}
    if THINK_OFF:
        body["think"] = False
    return body



def _duration_seconds(value) -> float | None:
    """Ollama가 nanosecond로 주는 duration을 초 단위로 바꾼다."""
    if value is None:
        return None
    try:
        return float(value) / 1_000_000_000
    except (TypeError, ValueError):
        return None


# ── Ollama native 호출 ────────────────────────────────────────

def _normalize_native_base_url(base_url: str) -> str:
    """OpenAI 호환 `/v1` 주소를 Ollama native base URL로 정규화한다."""
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base.rstrip("/")


def _native_base_url() -> str:
    """환경변수로 선택한 우선 Ollama의 native base URL을 만든다."""
    return _normalize_native_base_url(LLM_BASE_URL)


def _candidate_base_urls() -> tuple[str, ...]:
    """설정된 RunPod를 우선하고 로컬 Ollama를 마지막 후보로 둔다."""
    primary = _native_base_url()
    local = _normalize_native_base_url(LOCAL_OLLAMA_BASE_URL)
    if primary == local:
        return (local,)
    return (primary, local)


def _native_chat_url(base_url: str | None = None) -> str:
    return f"{base_url or _native_base_url()}/api/chat"


def _native_tags_url(base_url: str) -> str:
    return f"{base_url}/api/tags"


def _model_names(base_url: str, timeout: float) -> list[str]:
    request = urllib.request.Request(
        _native_tags_url(base_url),
        headers={"User-Agent": OLLAMA_USER_AGENT},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return [model.get("name", "") for model in payload.get("models", [])]


def _has_model(names: list[str], model: str) -> bool:
    return any(name == model or name.startswith(model) for name in names)


def _select_ollama_base(
    model: str,
    timeout: float = OLLAMA_FAILOVER_PROBE_TIMEOUT,
    *,
    force_check: bool = False,
) -> str:
    """사용 가능한 우선 Ollama를 고르고, 실패하면 로컬을 선택한다."""
    candidates = _candidate_base_urls()
    cache_key = (candidates[0], model)
    if not force_check and cache_key in _ROUTE_CACHE:
        return _ROUTE_CACHE[cache_key]

    failures: list[str] = []
    probe_timeout = max(0.1, min(float(timeout), OLLAMA_FAILOVER_PROBE_TIMEOUT))
    for index, base_url in enumerate(candidates):
        try:
            names = _model_names(base_url, probe_timeout)
        except (OSError, ValueError) as error:
            failures.append(f"{base_url}: 연결 실패({error})")
            continue

        if not _has_model(names, model):
            failures.append(
                f"{base_url}: 모델 없음(현재: {', '.join(names) or '없음'})"
            )
            continue

        _ROUTE_CACHE[cache_key] = base_url
        if index > 0:
            logger.warning(
                "우선 Ollama를 사용할 수 없어 로컬로 전환합니다: primary=%s fallback=%s",
                candidates[0],
                base_url,
            )
        return base_url

    raise RuntimeError(
        f"모델 '{model}'을 사용할 수 있는 Ollama가 없습니다. " + "; ".join(failures)
    )


def _to_ollama_messages(value) -> list[dict[str, str]]:
    """LangChain 입력을 Ollama native messages 형식으로 변환한다."""
    if hasattr(value, "to_messages"):
        messages = value.to_messages()
    elif isinstance(value, (list, tuple)):
        messages = value
    else:
        return [{"role": "user", "content": str(value)}]

    converted: list[dict[str, str]] = []
    for message in messages:
        message_type = getattr(message, "type", "")

        if message_type == "system":
            role = "system"
        elif message_type in ("human", "user"):
            role = "user"
        elif message_type in ("ai", "assistant"):
            role = "assistant"
        else:
            role = "user"

        content = getattr(message, "content", "")
        if not isinstance(content, str):
            content = str(content)

        converted.append({"role": role, "content": content})

    return converted


def _build_native_ollama(**overrides):
    """LangChain Runnable 형태의 Ollama native chat client를 만든다.

    기존 ChatOpenAI 기반 코드가 참조하던 ``temperature``, ``extra_body``,
    ``openai_api_base`` 속성은 호환성을 위해 Runnable 객체에 그대로 노출한다.
    실제 요청은 Ollama native `/api/chat`으로 보낸다.
    """
    timeout = float(overrides.pop("timeout", LLM_TIMEOUT))
    temperature = float(overrides.pop("temperature", LLM_TEMPERATURE))
    model = overrides.pop("model", LLM_MODEL)

    # 기존 호출부가 넘기던 값. native API에서는 재시도를 하지 않으므로 소비만 한다.
    overrides.pop("max_retries", None)

    body = _extra_body()

    # 호출자가 직접 준 max_tokens가 기본값보다 우선한다.
    if "max_tokens" in overrides:
        body["max_tokens"] = int(overrides.pop("max_tokens"))

    # 기존 동작과 동일하게 extra_body는 통째로 교체하지 않고 merge한다.
    if "extra_body" in overrides:
        body.update(overrides.pop("extra_body") or {})

    if overrides:
        raise TypeError(
            "Ollama native client가 지원하지 않는 override입니다: "
            + ", ".join(sorted(overrides))
        )

    max_tokens = int(body.get("max_tokens", LLM_MAX_TOKENS))

    def invoke(value):
        options = {
            "temperature": temperature,
            "num_predict": max_tokens,
        }

        # Ollama options로 전달 가능한 대표 설정은 그대로 보존한다.
        # 현재 테스트에서 사용하는 seed도 실제 서버까지 전달한다.
        for key in (
            "seed",
            "top_k",
            "top_p",
            "min_p",
            "repeat_penalty",
            "repeat_last_n",
            "num_ctx",
        ):
            if key in body:
                options[key] = body[key]

        messages = _to_ollama_messages(value)
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": LLM_KEEP_ALIVE,
            "options": options,
        }

        # 핵심 수정: Qwen3 thinking을 native API top-level 필드로 직접 제어한다.
        if "think" in body:
            payload["think"] = bool(body["think"])
        elif THINK_OFF:
            payload["think"] = False

        try:
            selected_base = _select_ollama_base(model, timeout)
        except RuntimeError as error:
            raise RuntimeError(f"Ollama 호출 준비 실패: {error}") from error

        candidates = _candidate_base_urls()
        attempt_bases = [selected_base]
        local_base = _normalize_native_base_url(LOCAL_OLLAMA_BASE_URL)
        if selected_base != local_base and local_base in candidates:
            attempt_bases.append(local_base)

        failures: list[str] = []
        result = None
        active_base = selected_base
        started = time.perf_counter()

        for attempt_index, base_url in enumerate(attempt_bases):
            request = urllib.request.Request(
                _native_chat_url(base_url),
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    # RunPod HTTP 프록시의 Cloudflare가 Python urllib 기본
                    # User-Agent 요청을 403으로 거부하므로 명시적으로 지정한다.
                    "User-Agent": OLLAMA_USER_AGENT,
                },
                method="POST",
            )

            attempt_started = time.perf_counter()
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    result = json.loads(response.read().decode("utf-8"))
            except (OSError, ValueError) as error:
                failures.append(f"{base_url}: {error}")
                _ROUTE_CACHE.pop((candidates[0], model), None)
                logger.warning(
                    "Ollama 호출 실패: endpoint=%s elapsed=%.3fs error=%s",
                    base_url,
                    time.perf_counter() - attempt_started,
                    error,
                )
                if attempt_index + 1 < len(attempt_bases):
                    logger.warning("로컬 Ollama로 호출을 재시도합니다: %s", local_base)
                continue

            active_base = base_url
            _ROUTE_CACHE[(candidates[0], model)] = base_url
            break

        if result is None:
            raise RuntimeError("Ollama 호출 실패: " + "; ".join(failures))

        message = result.get("message") or {}
        content = message.get("content") or ""

        load_seconds = _duration_seconds(result.get("load_duration"))
        prompt_seconds = _duration_seconds(result.get("prompt_eval_duration"))
        eval_seconds = _duration_seconds(result.get("eval_duration"))

        logger.warning(
            "Ollama 성능 진단: status=done endpoint=%s elapsed=%.3fs load=%.3fs "
            "prompt_eval=%.3fs eval=%.3fs prompt_tokens=%s output_tokens=%s "
            "input_chars=%d max_tokens=%d keep_alive=%s done_reason=%s",
            active_base,
            time.perf_counter() - started,
            load_seconds or 0.0,
            prompt_seconds or 0.0,
            eval_seconds or 0.0,
            result.get("prompt_eval_count"),
            result.get("eval_count"),
            sum(len(item.get("content", "")) for item in messages),
            max_tokens,
            LLM_KEEP_ALIVE,
            result.get("done_reason"),
        )

        return AIMessage(
            content=content,
            response_metadata={
                "model": result.get("model", model),
                "done": result.get("done"),
                "done_reason": result.get("done_reason"),
                "load_duration": result.get("load_duration"),
                "prompt_eval_count": result.get("prompt_eval_count"),
                "prompt_eval_duration": result.get("prompt_eval_duration"),
                "eval_count": result.get("eval_count"),
                "eval_duration": result.get("eval_duration"),
                "endpoint": active_base,
            },
        )

    runnable = RunnableLambda(invoke)

    # 기존 테스트/호출부와의 호환성. 실제 통신 경로는 native /api/chat이다.
    # RunnableLambda는 일반 Python 속성 설정을 허용한다.
    runnable.temperature = temperature
    runnable.extra_body = body
    runnable.openai_api_base = _native_base_url()
    runnable.model_name = model
    runnable.timeout = timeout

    return runnable


# ── LLM 만들기 ────────────────────────────────────────────────

def get_llm(fake_responses: list[str] | None = None, **overrides):
    """LLM 하나를 만들어 돌려준다.

    fake_responses를 주면 Ollama를 호출하지 않는 FakeListChatModel을 반환한다.
    실제 모델은 Ollama native `/api/chat`을 사용하며, Qwen3 thinking을 서버에서
    직접 비활성화한다.
    """
    if fake_responses is not None:
        if not fake_responses:
            raise ValueError("fake_responses 가 비어 있습니다. 응답을 하나 이상 주세요.")
        return FakeListChatModel(responses=fake_responses)

    return _build_native_ollama(**overrides)


def probe(timeout: float = 5.0) -> tuple[bool, str]:
    """RunPod를 우선 확인하고 실패하면 로컬 Ollama 상태를 확인한다."""
    try:
        active_base = _select_ollama_base(LLM_MODEL, timeout, force_check=True)
    except RuntimeError as error:
        return False, (
            "RunPod와 로컬 Ollama 모두 사용할 수 없습니다. "
            f"`ollama serve`와 `ollama pull {LLM_MODEL}` 상태를 확인하세요. 원인: {error}"
        )

    primary = _native_base_url()
    if active_base != primary:
        return True, f"준비됨 — {LLM_MODEL} @ {active_base} (RunPod 대신 로컬 사용 중)"
    return True, f"준비됨 — {LLM_MODEL} @ {active_base}"
