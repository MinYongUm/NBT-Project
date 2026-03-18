"""
NBT (Network Backup Tools) - Config Analyzer
- Ollama 로컬 LLM을 사용한 네트워크 장비 Config 분석 모듈
- 외부 API 전송 없음 — 모든 처리는 로컬 Ollama 컨테이너에서 수행

Update History:
- ver 5.0 (2026/03/17): 신규 작성 — Ollama /api/generate 연동
- ver 5.1 (2026/03/18): analyze_with_context() 추가 — RAG 파이프라인 연동
"""

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 환경변수
# ------------------------------------------------------------------
_OLLAMA_URL   = os.environ.get("NBT_OLLAMA_URL",   "http://nbt-ollama:11434")
_OLLAMA_MODEL = os.environ.get("NBT_OLLAMA_MODEL", "llama3.2:3b")

# config 텍스트 최대 길이 — 초과 시 앞부분만 사용 (fallback 전용)
_MAX_CHARS = 12_000

# Ollama 응답 대기 시간 (초) — LLM은 응답이 느림
_REQUEST_TIMEOUT = 300

# ------------------------------------------------------------------
# System prompt
# ------------------------------------------------------------------
_SYSTEM_PROMPT = """You are a senior network engineer assistant specializing in Cisco network devices.
Analyze the provided network device configuration and answer the user's question.

Rules:
- Always respond in Korean (한국어로 답변)
- Base your analysis strictly on the provided configuration
- If something is not present in the config, clearly state that
- Use technical network terminology appropriately
- Structure your answer clearly with findings and recommendations
- Be concise but thorough"""


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def analyze_config(file_path: str, question: str) -> dict:
    """백업 파일을 읽어 Ollama LLM으로 분석합니다. (fallback 전용)

    인덱싱이 되지 않은 파일에 대한 fallback 경로입니다.
    인덱싱된 파일은 analyze_with_context()를 사용합니다.

    Args:
        file_path: 백업 파일 절대 경로
        question:  사용자 질문 (한국어 가능)

    Returns:
        dict: {
            "answer":    str,
            "model":     str,
            "file":      str,
            "truncated": bool,
            "rag_used":  bool,  # 항상 False (fallback)
            "chunks":    int,   # 항상 0 (fallback)
        }

    Raises:
        FileNotFoundError: 백업 파일이 존재하지 않는 경우
        ValueError:        질문이 비어있는 경우
        RuntimeError:      Ollama API 호출 실패
    """
    if not question or not question.strip():
        raise ValueError("질문을 입력해 주세요.")

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"백업 파일을 찾을 수 없습니다: {file_path}")

    config_text = path.read_text(encoding="utf-8")
    truncated = len(config_text) > _MAX_CHARS
    if truncated:
        config_text = config_text[:_MAX_CHARS]
        logger.warning(
            f"Config 파일이 {_MAX_CHARS}자를 초과하여 앞부분만 사용합니다: {path.name}"
        )

    logger.info(
        f"Config 분석 시작 (fallback) | 파일: {path.name} | 모델: {_OLLAMA_MODEL}"
    )

    prompt = _build_prompt_fallback(config_text, question)
    answer = _call_ollama(prompt)

    logger.info(f"Config 분석 완료 (fallback) | 파일: {path.name}")

    return {
        "answer":    answer,
        "model":     _OLLAMA_MODEL,
        "file":      path.name,
        "truncated": truncated,
        "rag_used":  False,
        "chunks":    0,
    }


def analyze_with_context(
    chunks: list[str],
    question: str,
    file_name: str,
) -> dict:
    """RAG 검색으로 찾은 청크를 사용해 Ollama LLM으로 분석합니다.

    Args:
        chunks:    search_config()가 반환한 관련 청크 리스트
        question:  사용자 질문 (한국어 가능)
        file_name: 분석 대상 파일명 (응답에 포함)

    Returns:
        dict: {
            "answer":    str,
            "model":     str,
            "file":      str,
            "truncated": bool,  # RAG 모드에서는 항상 False
            "rag_used":  bool,  # 항상 True
            "chunks":    int,   # 참조한 청크 수
        }

    Raises:
        ValueError:   질문이 비어있는 경우
        RuntimeError: Ollama API 호출 실패
    """
    if not question or not question.strip():
        raise ValueError("질문을 입력해 주세요.")

    logger.info(
        f"Config 분석 시작 (RAG) | 파일: {file_name} | "
        f"청크: {len(chunks)}개 | 모델: {_OLLAMA_MODEL}"
    )

    prompt = _build_prompt_rag(chunks, question)
    answer = _call_ollama(prompt)

    logger.info(f"Config 분석 완료 (RAG) | 파일: {file_name}")

    return {
        "answer":    answer,
        "model":     _OLLAMA_MODEL,
        "file":      file_name,
        "truncated": False,
        "rag_used":  True,
        "chunks":    len(chunks),
    }


def health_check() -> dict:
    """Ollama 서버 상태를 확인합니다.

    Returns:
        dict: {"status": "ok" | "error", "message": str}
    """
    try:
        req = urllib.request.Request(_OLLAMA_URL, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode()
            if "running" in body.lower():
                return {"status": "ok", "message": body.strip()}
            return {"status": "error", "message": body.strip()}
    except urllib.error.URLError as e:
        return {"status": "error", "message": f"Ollama 서버에 연결할 수 없습니다: {e}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _build_prompt_fallback(config_text: str, question: str) -> str:
    """fallback용 프롬프트 — config 전체를 포함합니다."""
    return f"""{_SYSTEM_PROMPT}

## Network Device Configuration
```
{config_text}
```

## Question
{question}

## Answer (in Korean)"""


def _build_prompt_rag(chunks: list[str], question: str) -> str:
    """RAG용 프롬프트 — 검색된 청크만 포함합니다."""
    sections = ""
    for i, chunk in enumerate(chunks, 1):
        sections += f"\n### Section {i}\n```\n{chunk}\n```\n"

    return f"""{_SYSTEM_PROMPT}

## Relevant Configuration Sections
{sections}

## Question
{question}

## Answer (in Korean)"""


def _call_ollama(prompt: str) -> str:
    """Ollama /api/generate 엔드포인트를 호출합니다.

    Args:
        prompt: 조립된 프롬프트 문자열

    Returns:
        str: LLM이 생성한 답변 텍스트

    Raises:
        RuntimeError: HTTP 오류 또는 응답 파싱 실패
    """
    url     = f"{_OLLAMA_URL}/api/generate"
    payload = json.dumps({
        "model":  _OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Ollama API HTTP 오류: {e.code} {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Ollama 서버 연결 실패: {e}") from e
    except TimeoutError:
        raise RuntimeError(
            f"Ollama 응답 시간 초과 ({_REQUEST_TIMEOUT}s). "
            "모델 로딩 중이거나 서버 부하가 높습니다."
        )

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Ollama 응답 파싱 실패: {e}") from e

    answer = data.get("response", "").strip()
    if not answer:
        raise RuntimeError("Ollama가 빈 응답을 반환했습니다.")

    return answer