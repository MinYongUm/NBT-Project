"""
NBT (Network Backup Tools) - RAG Pipeline
- Cisco running-config 청크 분할 + ChromaDB 벡터 저장 + 유사 검색

흐름:
    인덱싱: chunk_config → nomic-embed-text → ChromaDB
    검색:   query → nomic-embed-text → ChromaDB 유사 검색 → 청크 반환

환경변수:
    NBT_CHROMA_URL  : ChromaDB 서버 주소 (기본: http://nbt-chroma:8000)
    NBT_OLLAMA_URL  : Ollama 서버 주소   (기본: http://nbt-ollama:11434)
    NBT_EMBED_MODEL : 임베딩 모델명      (기본: nomic-embed-text)

Update History:
- ver 5.1 (2026/03/18): 신규 작성
- ver 5.1.1 (2026/03/18): chunk_config() NBT 백업 파일 구조 대응
                           ("====..." 구분자 기준 1차 분할 추가)
"""

import hashlib
import json
import logging
import os
import re
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 환경변수
# ------------------------------------------------------------------
_CHROMA_URL  = os.environ.get("NBT_CHROMA_URL",  "http://nbt-chroma:8000")
_OLLAMA_URL  = os.environ.get("NBT_OLLAMA_URL",  "http://nbt-ollama:11434")
_EMBED_MODEL = os.environ.get("NBT_EMBED_MODEL", "nomic-embed-text")

# 청크 최소 길이 — 너무 짧은 청크(배너, 주석 등) 제외
_MIN_CHUNK_LEN = 30


# ------------------------------------------------------------------
# 컬렉션명 정규화
# ------------------------------------------------------------------
def _sanitize_collection_name(name: str) -> str:
    """ChromaDB 컬렉션명 규칙을 만족하도록 정규화합니다.

    규칙:
        - 3~512자
        - [a-zA-Z0-9._-] 문자만 허용
        - 시작/끝은 영숫자

    예: "R5"        → "nbt_R5"
        "SW-CORE 01"→ "SW-CORE_01"
    """
    safe = re.sub(r"[^a-zA-Z0-9._\-]", "_", name)
    if not safe[0:1].isalnum():
        safe = "nbt_" + safe
    if not safe[-1:].isalnum():
        safe = safe + "_0"
    if len(safe) < 3:
        safe = "nbt_" + safe
    return safe[:512]


# ------------------------------------------------------------------
# ChromaDB 클라이언트 (싱글턴)
# ------------------------------------------------------------------
_chroma_client: Optional[chromadb.HttpClient] = None


def _get_client() -> chromadb.HttpClient:
    """ChromaDB HttpClient 싱글턴을 반환합니다."""
    global _chroma_client
    if _chroma_client is None:
        host, port = _parse_url(_CHROMA_URL)
        _chroma_client = chromadb.HttpClient(
            host=host,
            port=port,
            settings=Settings(anonymized_telemetry=False),
        )
        logger.info(f"ChromaDB 클라이언트 초기화: {_CHROMA_URL}")
    return _chroma_client


def _parse_url(url: str) -> tuple[str, int]:
    """'http://host:port' 형식에서 host, port를 분리합니다."""
    url = url.replace("http://", "").replace("https://", "")
    if ":" in url:
        host, port_str = url.rsplit(":", 1)
        return host, int(port_str)
    return url, 8000


# ------------------------------------------------------------------
# 임베딩
# ------------------------------------------------------------------
def _embed(text: str) -> list[float]:
    """Ollama /api/embeddings 엔드포인트로 텍스트를 벡터로 변환합니다.

    Args:
        text: 임베딩할 텍스트

    Returns:
        list[float]: 임베딩 벡터

    Raises:
        RuntimeError: Ollama 서버 오류 또는 응답 파싱 실패
    """
    payload = json.dumps({
        "model":  _EMBED_MODEL,
        "prompt": text,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{_OLLAMA_URL}/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["embedding"]
    except urllib.error.URLError as e:
        raise RuntimeError(f"Ollama 임베딩 요청 실패: {e}") from e
    except (KeyError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Ollama 임베딩 응답 파싱 실패: {e}") from e


# ------------------------------------------------------------------
# 청크 분할
# ------------------------------------------------------------------
def _split_by_bang(text: str) -> list[str]:
    """'!' 기준으로 텍스트를 분할합니다. (running-config 내부 섹션용)"""
    result: list[str] = []
    current: list[str] = []

    for line in text.splitlines():
        if line.strip() == "!":
            block = "\n".join(current).strip()
            if len(block) >= _MIN_CHUNK_LEN:
                result.append(block)
            current = []
        else:
            current.append(line)

    block = "\n".join(current).strip()
    if len(block) >= _MIN_CHUNK_LEN:
        result.append(block)

    return result


def chunk_config(text: str) -> list[str]:
    """NBT 백업 파일을 청크로 분할합니다.

    1차 분할: "========== 명령어 ==========" 구분자 기준
    2차 분할: running-config 섹션은 추가로 "!" 기준 분할

    Args:
        text: NBT 백업 파일 전체 텍스트

    Returns:
        list[str]: 청크 리스트
    """
    chunks: list[str] = []

    # 1차 분할 — "====..." 구분자 기준
    sections = re.split(r"={5,}.*?={5,}", text)

    for section in sections:
        section = section.strip()
        if not section:
            continue

        # running-config 섹션은 "!" 기준으로 추가 분할
        if "Current configuration" in section or (
            "interface " in section and "router " in section
        ):
            sub_chunks = _split_by_bang(section)
            chunks.extend(sub_chunks)
        else:
            # 그 외 섹션 (show version, show log 등)은 통째로 하나의 청크
            if len(section) >= _MIN_CHUNK_LEN:
                chunks.append(section)

    # 1차 분할 결과가 없으면 "!" 기준으로 fallback
    if not chunks:
        chunks = _split_by_bang(text)

    logger.info(f"청크 분할 완료: {len(chunks)}개")
    return chunks


# ------------------------------------------------------------------
# 인덱싱
# ------------------------------------------------------------------
def index_config(file_path: str, collection_name: str) -> int:
    """running-config 파일을 청크 분할 후 ChromaDB에 인덱싱합니다.

    동일 파일을 재인덱싱하면 기존 데이터를 덮어씁니다.
    (청크 ID가 파일 경로 + 인덱스 기반 해시로 결정됨)

    Args:
        file_path:       백업 파일 절대 경로
        collection_name: ChromaDB 컬렉션명 (장비 hostname 권장)

    Returns:
        int: 저장된 청크 수

    Raises:
        FileNotFoundError: 파일이 존재하지 않는 경우
        RuntimeError:      임베딩 또는 ChromaDB 저장 실패
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {file_path}")

    text = path.read_text(encoding="utf-8")
    chunks = chunk_config(text)

    if not chunks:
        logger.warning(f"청크 없음 — 인덱싱 건너뜀: {file_path}")
        return 0

    client = _get_client()
    collection = client.get_or_create_collection(
        name=_sanitize_collection_name(collection_name),
        metadata={"hnsw:space": "cosine"},
    )

    # 청크별 ID 생성 (파일 경로 + 인덱스 → MD5 해시)
    ids: list[str] = []
    for i in range(len(chunks)):
        raw = f"{file_path}::{i}"
        chunk_id = hashlib.md5(raw.encode()).hexdigest()
        ids.append(chunk_id)

    # 임베딩 생성
    logger.info(f"임베딩 생성 중: {len(chunks)}개 청크")
    embeddings: list[list[float]] = []
    for i, chunk in enumerate(chunks):
        try:
            vec = _embed(chunk)
            embeddings.append(vec)
        except RuntimeError as e:
            logger.error(f"청크 {i} 임베딩 실패: {e}")
            raise

    # ChromaDB 저장 (upsert: 같은 ID면 덮어씀)
    collection.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=chunks,
        metadatas=[{"file_path": file_path, "chunk_index": i}
                   for i in range(len(chunks))],
    )

    logger.info(f"ChromaDB 저장 완료: collection={collection_name}, chunks={len(chunks)}")
    return len(chunks)


# ------------------------------------------------------------------
# 검색
# ------------------------------------------------------------------
def search_config(
    query: str,
    collection_name: str,
    n_results: int = 5,
) -> list[str]:
    """질문과 유사한 config 청크를 ChromaDB에서 검색합니다.

    Args:
        query:           사용자 질문
        collection_name: 검색할 ChromaDB 컬렉션명
        n_results:       반환할 청크 수 (기본: 5)

    Returns:
        list[str]: 유사도 높은 청크 리스트 (최대 n_results개)
                   컬렉션이 없거나 결과 없으면 빈 리스트 반환
    """
    client = _get_client()

    safe_name = _sanitize_collection_name(collection_name)
    existing  = [c.name for c in client.list_collections()]
    if safe_name not in existing:
        logger.warning(f"컬렉션 없음: {safe_name} — 인덱싱 먼저 필요")
        return []

    collection = client.get_collection(name=safe_name)

    count = collection.count()
    if count == 0:
        logger.warning(f"컬렉션이 비어 있음: {collection_name}")
        return []
    n_results = min(n_results, count)

    query_vec = _embed(query)
    results = collection.query(
        query_embeddings=[query_vec],
        n_results=n_results,
    )

    chunks = results.get("documents", [[]])[0]
    logger.info(f"유사 청크 검색 완료: {len(chunks)}개 반환")
    return chunks