"""
NBT (Network Backup Tools) - Analyze Router
- Ollama LLM 기반 Config 분석 API 엔드포인트
- v5.1: RAG 파이프라인 연동 (ChromaDB + nomic-embed-text)

Endpoints:
    POST /api/analyze        : Config 파일 분석 요청 (RAG 우선, fallback 자동)
    POST /api/analyze/index  : Config 파일 ChromaDB 인덱싱
    GET  /api/analyze/health : Ollama 서버 상태 확인

Update History:
- ver 5.0 (2026/03/17): 신규 작성
- ver 5.1 (2026/03/18): RAG 파이프라인 연동, /api/analyze/index 추가
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.analyzer import analyze_config, analyze_with_context, health_check
from core.auth import get_current_user_api
from core.rag import index_config, search_config

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
    rag_used:  bool   # 추가 (v5.1): RAG 파이프라인 사용 여부
    chunks:    int    # 추가 (v5.1): 참조한 청크 수


class IndexRequest(BaseModel):
    file_path:       str
    collection_name: str   # 장비 hostname 권장 (예: SW-CORE-01)


class IndexResponse(BaseModel):
    collection: str
    chunks:     int


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.post("/index", response_model=IndexResponse)
async def post_index(
    body: IndexRequest,
    _: str = Depends(get_current_user_api),
) -> IndexResponse:
    """백업 파일을 청크 분할 후 ChromaDB에 인덱싱합니다.

    인덱싱 완료 후 POST /api/analyze 호출 시 RAG 모드로 자동 전환됩니다.

    Args:
        body.file_path:       백업 파일 절대 경로
        body.collection_name: ChromaDB 컬렉션명 (장비 hostname 권장)

    Returns:
        IndexResponse: 저장된 컬렉션명 및 청크 수

    Raises:
        404: 백업 파일이 존재하지 않는 경우
        503: ChromaDB 또는 Ollama 서버 오류
    """
    logger.info(
        f"인덱싱 요청 | file: {body.file_path} | "
        f"collection: {body.collection_name}"
    )

    try:
        count = index_config(
            file_path=body.file_path,
            collection_name=body.collection_name,
        )
        return IndexResponse(collection=body.collection_name, chunks=count)

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        logger.error(f"인덱싱 오류: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"인덱싱 중 예기치 않은 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="인덱싱 중 오류가 발생했습니다.")


@router.post("", response_model=AnalyzeResponse)
async def post_analyze(
    body: AnalyzeRequest,
    _: str = Depends(get_current_user_api),
) -> AnalyzeResponse:
    """백업 파일을 Ollama LLM으로 분석합니다.

    인덱싱된 파일: RAG 모드 (ChromaDB 유사 청크 검색 후 LLM 전달)
    미인덱싱 파일: fallback 모드 (파일 전체를 LLM에 직접 전달)

    Args:
        body.file_path: 백업 파일 절대 경로
        body.question:  분석 질문 (한국어 가능)

    Returns:
        AnalyzeResponse: LLM 분석 결과 (rag_used, chunks 포함)

    Raises:
        400: 질문이 비어있는 경우
        404: 백업 파일이 존재하지 않는 경우
        503: Ollama 또는 ChromaDB 서버 오류
    """
    logger.info(
        f"Config 분석 요청 | file: {body.file_path} | "
        f"question: {body.question[:50]}..."
    )

    try:
        # file_path에서 collection_name 자동 추출
        # 예: /data/backup/20260318_0200/SW-CORE-01.txt → "SW-CORE-01"
        collection_name = Path(body.file_path).stem

        # RAG 검색 시도
        chunks = search_config(
            query=body.question,
            collection_name=collection_name,
        )

        if chunks:
            # RAG 모드 — 관련 청크만 LLM에 전달
            logger.info(
                f"RAG 모드 | collection: {collection_name} | "
                f"청크: {len(chunks)}개"
            )
            result = analyze_with_context(
                chunks=chunks,
                question=body.question,
                file_name=Path(body.file_path).name,
            )
        else:
            # fallback 모드 — 파일 전체를 LLM에 직접 전달
            logger.info(
                f"fallback 모드 | collection: {collection_name} | "
                f"인덱싱 필요"
            )
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
        logger.error(f"분석 오류: {e}")
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