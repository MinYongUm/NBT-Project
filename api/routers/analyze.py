"""
NBT (Network Backup Tools) - Analyze Router
- Ollama LLM 기반 Config 분석 API 엔드포인트
- v5.1: RAG 파이프라인 연동 (ChromaDB + nomic-embed-text)

Endpoints:
    POST /api/analyze          : Config 파일 분석 요청 (RAG 우선, fallback 자동)
    POST /api/analyze/index    : Config 파일 ChromaDB 인덱싱
    POST /api/analyze/compare  : 다중 장비 Config 비교 분석 (v5.1.1)
    GET  /api/analyze/health   : Ollama 서버 상태 확인

Update History:
- ver 5.0 (2026/03/17): 신규 작성
- ver 5.1 (2026/03/18): RAG 파이프라인 연동, /api/analyze/index 추가
- ver 5.1.1 (2026/03/18): /api/analyze/compare 추가 — 다중 장비 비교 분석
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.analyzer import analyze_compare, analyze_config, analyze_with_context, health_check
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
    rag_used:  bool
    chunks:    int


class IndexRequest(BaseModel):
    file_path:       str
    collection_name: str


class IndexResponse(BaseModel):
    collection: str
    chunks:     int


class CompareDevice(BaseModel):
    hostname:  str   # ChromaDB collection_name 기준 (예: R26)
    file_path: str   # 백업 파일 절대 경로 (fallback용)


class CompareRequest(BaseModel):
    devices:  list[CompareDevice]   # 비교할 장비 목록 (최소 2대)
    question: str


class CompareResponse(BaseModel):
    answer:   str
    model:    str
    devices:  list[str]   # 비교한 장비 hostname 목록
    chunks:   int         # 전체 참조 청크 수
    rag_used: bool


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.post("/index", response_model=IndexResponse)
async def post_index(
    body: IndexRequest,
    _: str = Depends(get_current_user_api),
) -> IndexResponse:
    """백업 파일을 청크 분할 후 ChromaDB에 인덱싱합니다."""
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


@router.post("/compare", response_model=CompareResponse)
async def post_compare(
    body: CompareRequest,
    _: str = Depends(get_current_user_api),
) -> CompareResponse:
    """다중 장비 Config를 비교 분석합니다.

    장비별 ChromaDB에서 관련 청크를 검색한 후
    구조화된 프롬프트로 LLM에 전달하여 비교 분석합니다.

    인덱싱되지 않은 장비는 비교 대상에서 제외되며
    결과에 경고가 포함됩니다.

    Args:
        body.devices:  비교할 장비 목록 (hostname + file_path)
        body.question: 비교 질문 (한국어 가능)

    Returns:
        CompareResponse: 비교 분석 결과

    Raises:
        400: 질문이 비어있거나 장비가 1대 이하인 경우
        503: Ollama 또는 ChromaDB 서버 오류
    """
    logger.info(
        f"비교 분석 요청 | 장비: {[d.hostname for d in body.devices]} | "
        f"question: {body.question[:50]}..."
    )

    try:
        if len(body.devices) < 2:
            raise ValueError("비교 분석은 최소 2대 이상의 장비가 필요합니다.")

        # 장비별 RAG 검색
        device_chunks: dict[str, list[str]] = {}
        skipped: list[str] = []

        for device in body.devices:
            chunks = search_config(
                query=body.question,
                collection_name=device.hostname,
            )
            if chunks:
                device_chunks[device.hostname] = chunks
                logger.info(
                    f"비교 분석 청크 수집 | {device.hostname}: {len(chunks)}개"
                )
            else:
                skipped.append(device.hostname)
                logger.warning(
                    f"비교 분석 제외 | {device.hostname}: 인덱싱 없음"
                )

        # 인덱싱된 장비가 2대 미만이면 오류
        if len(device_chunks) < 2:
            raise ValueError(
                f"인덱싱된 장비가 {len(device_chunks)}대입니다. "
                f"비교 분석은 최소 2대 인덱싱이 필요합니다. "
                f"미인덱싱 장비: {skipped}"
            )

        result = analyze_compare(
            device_chunks=device_chunks,
            question=body.question,
        )

        # 제외된 장비가 있으면 답변에 경고 추가
        if skipped:
            result["answer"] = (
                f"[주의] 다음 장비는 인덱싱되지 않아 비교에서 제외됐습니다: "
                f"{', '.join(skipped)}\n\n" + result["answer"]
            )

        return CompareResponse(**result)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        logger.error(f"비교 분석 오류: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"비교 분석 중 예기치 않은 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="비교 분석 중 오류가 발생했습니다.")


@router.post("", response_model=AnalyzeResponse)
async def post_analyze(
    body: AnalyzeRequest,
    _: str = Depends(get_current_user_api),
) -> AnalyzeResponse:
    """백업 파일을 Ollama LLM으로 분석합니다.

    인덱싱된 파일: RAG 모드 (ChromaDB 유사 청크 검색 후 LLM 전달)
    미인덱싱 파일: fallback 모드 (파일 전체를 LLM에 직접 전달)
    """
    logger.info(
        f"Config 분석 요청 | file: {body.file_path} | "
        f"question: {body.question[:50]}..."
    )

    try:
        collection_name = Path(body.file_path).stem

        chunks = search_config(
            query=body.question,
            collection_name=collection_name,
        )

        if chunks:
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
    """Ollama 서버 상태를 확인합니다."""
    return health_check()