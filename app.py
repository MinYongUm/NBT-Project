"""
NBT (Network Backup Tools)
FastAPI Web 서버 진입점 - v4.0
"""

from fastapi import FastAPI

from api.routers import history, diff, backup

app = FastAPI(
    title="NBT - Network Backup Tools",
    version="4.0",
)

app.include_router(history.router)
app.include_router(diff.router)
app.include_router(backup.router)

@app.get("/")
def health_check():
    return {"status": "ok", "version": "4.0"}