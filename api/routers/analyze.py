"""
NBT (Network Backup Tools) - Analyze Router
- Ollama LLM 기반 Config 분석 API 엔드포인트

Endpoints:
    POST /api/analyze        : Config 파일 분석 요청
    GET  /api/analyze/health : Ollama 서버 상태 확인

Update History:
- ver 5.0 (2026/03/17): 신규 작성
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.analyzer import analyze_config, health_check
from core.auth import get_current_user_api

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analyze", tags=["analyze"])


# ------------------------------------------------------------------
# Request / Response 모델
# ------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    file_path: str
    question:  str


class AnalyzeResponse(BaseModel):
    answer:    str
    model:     str
    file:      str
    truncated: bool


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.post("", response_model=AnalyzeResponse)
async def post_analyze(
    body: AnalyzeRequest,
    _: str = Depends(get_current_user_api),
) -> AnalyzeResponse:
    """백업 파일을 Ollama LLM으로 분석합니다.

    Args:
        body.file_path: 백업 파일 절대 경로
        body.question:  분석 질문 (한국어 가능)

    Returns:
        AnalyzeResponse: LLM 분석 결과

    Raises:
        400: 질문이 비어있는 경우
        404: 백업 파일이 존재하지 않는 경우
        503: Ollama 서버 오류
    """
    logger.info(
        f"Config 분석 요청 | file: {body.file_path} | "
        f"question: {body.question[:50]}..."
    )

    try:
        result = analyze_config(
            file_path=body.file_path,
            question=body.question,
        )
        return AnalyzeResponse(**result)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        logger.error(f"Ollama 분석 오류: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"분석 중 예기치 않은 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="분석 중 오류가 발생했습니다.")


@router.get("/health")
async def get_health(
    _: str = Depends(get_current_user_api),
) -> dict:
    """Ollama 서버 상태를 확인합니다.

    Returns:
        dict: {"status": "ok" | "error", "message": str}
    """
    return health_check()