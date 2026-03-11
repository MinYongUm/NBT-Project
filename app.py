"""
NBT (Network Backup Tools)
FastAPI Web 서버 진입점 - v4.0
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.routers import history, diff, backup, pages

app = FastAPI(
    title="NBT - Network Backup Tools",
    version="4.0",
)

# 정적 파일 및 템플릿 설정
app.mount("/static", StaticFiles(directory="web/static"), name="static")

app.include_router(history.router)
app.include_router(diff.router)
app.include_router(backup.router)

# 페이지 라우터
app.include_router(pages.router)