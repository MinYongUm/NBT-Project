"""
NBT (Network Backup Tools) - Config Analyzer
- Ollama 로컬 LLM을 사용한 네트워크 장비 Config 분석 모듈
- 외부 API 전송 없음 — 모든 처리는 로컬 Ollama 컨테이너에서 수행

Update History:
- ver 5.0 (2026/03/17): 신규 작성 — Ollama /api/generate 연동
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

# config 텍스트 최대 길이 — 초과 시 앞부분만 사용
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
    """백업 파일을 읽어 Ollama LLM으로 분석합니다.

    Args:
        file_path: 백업 파일 절대 경로 (/data/backup/YYYYMMDD_HHMM/hostname.txt)
        question:  사용자 질문 (한국어 가능)

    Returns:
        dict: {
            "answer":    str,   # LLM 답변
            "model":     str,   # 사용된 모델명
            "file":      str,   # 분석한 파일명
            "truncated": bool,  # config 잘림 여부
        }

    Raises:
        FileNotFoundError: 백업 파일이 존재하지 않는 경우
        ValueError:        질문이 비어있는 경우
        RuntimeError:      Ollama API 호출 실패
    """
    # 1. 입력값 검증
    if not question or not question.strip():
        raise ValueError("질문을 입력해 주세요.")

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"백업 파일을 찾을 수 없습니다: {file_path}")

    # 2. 파일 읽기
    config_text = path.read_text(encoding="utf-8")
    truncated = len(config_text) > _MAX_CHARS
    if truncated:
        config_text = config_text[:_MAX_CHARS]
        logger.warning(
            f"Config 파일이 {_MAX_CHARS}자를 초과하여 앞부분만 분석에 사용합니다: {path.name}"
        )

    logger.info(f"Config 분석 시작 | 파일: {path.name} | 모델: {_OLLAMA_MODEL}")

    # 3. 프롬프트 조립
    prompt = _build_prompt(config_text, question)

    # 4. Ollama API 호출
    answer = _call_ollama(prompt)

    logger.info(f"Config 분석 완료 | 파일: {path.name}")

    return {
        "answer":    answer,
        "model":     _OLLAMA_MODEL,
        "file":      path.name,
        "truncated": truncated,
    }


def health_check() -> dict:
    """Ollama 서버 상태를 확인합니다.

    Returns:
        dict: {"status": "ok" | "error", "message": str}
    """
    try:
        req = urllib.request.Request(
            _OLLAMA_URL,
            method="GET",
        )
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

def _build_prompt(config_text: str, question: str) -> str:
    """시스템 프롬프트 + config + 질문을 하나의 프롬프트로 조립합니다."""
    return f"""{_SYSTEM_PROMPT}

## Network Device Configuration
```
{config_text}
```

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
        "stream": False,           # 전체 응답을 한 번에 받음
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

    # 응답 파싱
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Ollama 응답 파싱 실패: {e}") from e

    answer = data.get("response", "").strip()
    if not answer:
        raise RuntimeError("Ollama가 빈 응답을 반환했습니다.")

    return answer