"""
NBT (Network Backup Tools) - Report Generator
- 일일 분석 리포트 HTML 생성 모듈 (v5.2)
- 분석 결과 dict를 받아 HTML 문자열로 변환합니다.
- DB 조회 / 장비 접속 / 메일 발송은 수행하지 않습니다.

호출 흐름:
    analysis_task → build_report(report_data) → HTML 문자열
                                                     │
                                              notifier.send_report()
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 색상 팔레트 (Web UI 기준 dark theme 동일 적용)
# ------------------------------------------------------------------
_COLOR = {
    "bg":       "#0f0f0f",
    "surface":  "#1a1a1a",
    "border":   "#2a2a2a",
    "accent":   "#3b82f6",
    "success":  "#22c55e",
    "fail":     "#ef4444",
    "warning":  "#f59e0b",
    "text":     "#e5e5e5",
    "muted":    "#71717a",
}


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def build_report(report_data: dict[str, Any]) -> str:
    """분석 결과 dict를 HTML 리포트 문자열로 변환합니다.

    Args:
        report_data: analysis_task가 전달하는 리포트 데이터.
            {
                "date":    str,               # 리포트 날짜 (YYYY-MM-DD)
                "summary": {
                    "total":   int,
                    "success": int,
                    "fail":    int,
                    "diff":    int,
                },
                "analyzed":  list[dict],      # 변경 감지 + 분석 완료 장비
                    각 dict: {
                        "hostname":   str,
                        "ip":         str,
                        "diff_lines": int,
                        "analysis":   str,    # Ollama 분석 결과
                    }
                "no_change": list[str],       # 변경 없는 장비 hostname 목록
                "failed":    list[str],       # 백업 실패 장비 hostname 목록
            }

    Returns:
        str: 완성된 HTML 문자열
    """
    date      = report_data.get("date", "")
    summary   = report_data.get("summary", {})
    analyzed  = report_data.get("analyzed", [])
    no_change = report_data.get("no_change", [])
    failed    = report_data.get("failed", [])

    logger.info(
        f"리포트 생성 시작: {date} | "
        f"분석={len(analyzed)}대 변경없음={len(no_change)}대 실패={len(failed)}대"
    )

    html = "\n".join([
        _build_html_head(date),
        "<body>",
        '<div class="container">',
        _build_header(date),
        _build_summary_cards(summary),
        _build_analyzed_section(analyzed),
        _build_no_change_section(no_change),
        _build_failed_section(failed),
        _build_footer(),
        "</div>",
        "</body>",
        "</html>",
    ])

    logger.info(f"리포트 생성 완료: {date}")
    return html


# ------------------------------------------------------------------
# Private builders
# ------------------------------------------------------------------

def _build_html_head(date: str) -> str:
    """HTML head 섹션을 생성합니다."""
    c = _COLOR
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>[NBT] 일일 분석 리포트 {date}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background-color: {c['bg']};
    color: {c['text']};
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 14px;
    line-height: 1.6;
  }}
  .container {{
    max-width: 800px;
    margin: 0 auto;
    padding: 32px 24px;
  }}

  /* 헤더 */
  .report-header {{
    border-bottom: 2px solid {c['accent']};
    padding-bottom: 16px;
    margin-bottom: 28px;
  }}
  .report-header h1 {{
    font-size: 22px;
    font-weight: 700;
    color: {c['text']};
  }}
  .report-header .date {{
    font-size: 13px;
    color: {c['muted']};
    margin-top: 4px;
  }}

  /* 요약 카드 */
  .cards {{
    display: flex;
    gap: 12px;
    margin-bottom: 28px;
  }}
  .card {{
    flex: 1;
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 12px;
    padding: 16px;
    text-align: center;
    border-top: 3px solid {c['border']};
  }}
  .card.success {{ border-top-color: {c['success']}; }}
  .card.fail    {{ border-top-color: {c['fail']}; }}
  .card.diff    {{ border-top-color: {c['accent']}; }}
  .card .value {{
    font-size: 32px;
    font-weight: 700;
    font-family: 'Courier New', monospace;
  }}
  .card.success .value {{ color: {c['success']}; }}
  .card.fail    .value {{ color: {c['fail']}; }}
  .card.diff    .value {{ color: {c['accent']}; }}
  .card .label {{
    font-size: 12px;
    color: {c['muted']};
    margin-top: 4px;
  }}

  /* 섹션 공통 */
  .section {{
    margin-bottom: 28px;
  }}
  .section-title {{
    font-size: 15px;
    font-weight: 600;
    color: {c['text']};
    margin-bottom: 12px;
    padding-left: 10px;
    border-left: 3px solid {c['accent']};
  }}

  /* 장비 카드 (분석 결과) */
  .device-card {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 10px;
  }}
  .device-card .device-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
  }}
  .device-card .hostname {{
    font-weight: 600;
    font-size: 15px;
  }}
  .device-card .ip {{
    font-size: 12px;
    color: {c['muted']};
    margin-left: 8px;
  }}
  .badge {{
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 4px;
    background: {c['warning']}22;
    color: {c['warning']};
    font-family: 'Courier New', monospace;
  }}
  .analysis-box {{
    background: {c['bg']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    padding: 12px;
    font-size: 13px;
    color: {c['text']};
    white-space: pre-wrap;
    word-break: break-word;
  }}

  /* 변경 없음 / 실패 장비 */
  .tag-list {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }}
  .tag {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    padding: 4px 12px;
    font-size: 13px;
    font-family: 'Courier New', monospace;
  }}
  .tag.fail {{
    border-color: {c['fail']}44;
    color: {c['fail']};
  }}

  /* 푸터 */
  .footer {{
    margin-top: 32px;
    padding-top: 16px;
    border-top: 1px solid {c['border']};
    font-size: 12px;
    color: {c['muted']};
    text-align: center;
  }}
</style>
</head>"""


def _build_header(date: str) -> str:
    """리포트 헤더 섹션을 생성합니다."""
    return f"""
<div class="report-header">
  <h1>NBT 일일 분석 리포트</h1>
  <div class="date">{date} 자동 생성</div>
</div>"""


def _build_summary_cards(summary: dict) -> str:
    """백업 결과 요약 카드 3개를 생성합니다."""
    total   = summary.get("total", 0)
    success = summary.get("success", 0)
    fail    = summary.get("fail", 0)
    diff    = summary.get("diff", 0)

    return f"""
<div class="cards">
  <div class="card success">
    <div class="value">{success}<span style="font-size:16px;color:#71717a;">/{total}</span></div>
    <div class="label">백업 성공</div>
  </div>
  <div class="card fail">
    <div class="value">{fail}</div>
    <div class="label">백업 실패</div>
  </div>
  <div class="card diff">
    <div class="value">{diff}</div>
    <div class="label">Config 변경</div>
  </div>
</div>"""


def _build_analyzed_section(analyzed: list[dict]) -> str:
    """변경 감지 + 분석 완료 장비 섹션을 생성합니다."""
    if not analyzed:
        return ""

    items = []
    for device in analyzed:
        hostname   = _escape(device.get("hostname", ""))
        ip         = _escape(device.get("ip", ""))
        diff_lines = device.get("diff_lines", 0)
        analysis   = _escape(device.get("analysis", "분석 결과 없음"))

        items.append(f"""
<div class="device-card">
  <div class="device-header">
    <div>
      <span class="hostname">{hostname}</span>
      <span class="ip">({ip})</span>
    </div>
    <span class="badge">{diff_lines}줄 변경</span>
  </div>
  <div class="analysis-box">{analysis}</div>
</div>""")

    return f"""
<div class="section">
  <div class="section-title">Config 변경 감지 — AI 분석 결과 ({len(analyzed)}대)</div>
  {"".join(items)}
</div>"""


def _build_no_change_section(no_change: list[str]) -> str:
    """변경 없는 장비 섹션을 생성합니다."""
    if not no_change:
        return ""

    tags = "".join(
        f'<span class="tag">{_escape(h)}</span>' for h in no_change
    )
    return f"""
<div class="section">
  <div class="section-title">변경 없음 ({len(no_change)}대)</div>
  <div class="tag-list">{tags}</div>
</div>"""


def _build_failed_section(failed: list[str]) -> str:
    """백업 실패 장비 섹션을 생성합니다."""
    if not failed:
        return ""

    tags = "".join(
        f'<span class="tag fail">{_escape(h)}</span>' for h in failed
    )
    return f"""
<div class="section">
  <div class="section-title">백업 실패 ({len(failed)}대)</div>
  <div class="tag-list">{tags}</div>
</div>"""


def _build_footer() -> str:
    """리포트 푸터를 생성합니다."""
    return """
<div class="footer">
  NBT (Network Backup Tools) — 자동 생성 리포트
</div>"""


def _escape(text: str) -> str:
    """HTML 특수문자를 이스케이프합니다."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )