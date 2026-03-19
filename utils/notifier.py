"""
NBT (Network Backup Tools) - Notifier Module
- Slack Webhook 및 Email(SMTP) 알림 전송 모듈

알림 시점:
    - 장비 개별 최종 실패 시
    - Config Diff 감지 시
    - 전체 백업 완료 요약
    - 일일 분석 리포트 발송 (v5.2)

환경변수 (민감 정보):
    - NBT_SLACK_WEBHOOK : Slack Incoming Webhook URL
    - NBT_SMTP_USER     : SMTP 계정 (이메일 주소)
    - NBT_SMTP_PASSWORD : SMTP 비밀번호 (앱 비밀번호 권장)
"""

import json
import logging
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class NotifyConfig:
    # Slack
    slack_enabled: bool = False
    slack_webhook_url: str = ""
    # Email
    email_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_addr: str = ""
    to_addrs: list = field(default_factory=list)
    # 알림 시점 제어
    on_device_failure: bool = True
    on_diff_detected: bool = True
    on_summary: bool = True


class Notifier:
    """Slack Webhook + Email 알림 전송 클래스.

    모든 send_* 메서드는 내부에서 예외를 처리하므로
    호출 측 백업 흐름에 영향을 주지 않습니다.
    """

    def __init__(self, config: NotifyConfig) -> None:
        self._cfg = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_device_failure(self, hostname: str, ip: str, error: str) -> None:
        """장비 개별 최종 실패 알림."""
        if not self._cfg.on_device_failure:
            return
        title = f"[NBT] 백업 실패: {hostname}"
        body = f"장비: {hostname} ({ip})\n오류: {error}"
        self._dispatch(title, body)

    def send_diff_detected(self, hostname: str, ip: str, diff_lines: int) -> None:
        """Config Diff 감지 알림."""
        if not self._cfg.on_diff_detected:
            return
        title = f"[NBT] Config 변경 감지: {hostname}"
        body = f"장비: {hostname} ({ip})\n변경 라인: {diff_lines}개"
        self._dispatch(title, body)

    def send_summary(self, total: int, success: int, fail: int, diff_count: int) -> None:
        """전체 백업 완료 요약 알림."""
        if not self._cfg.on_summary:
            return
        status = "정상" if fail == 0 else f"실패 {fail}대 포함"
        title = f"[NBT] 백업 완료 ({status})"
        body = (
            f"전체: {total}  성공: {success}  실패: {fail}\n"
            f"Config 변경: {diff_count}건"
        )
        self._dispatch(title, body)

    def send_report(self, subject: str, html_body: str) -> None:
        """일일 분석 리포트 HTML 메일 발송 (v5.2).

        Email만 지원합니다. Slack은 HTML 렌더링 미지원으로 보류.

        Args:
            subject:   메일 제목 (예: "[NBT] 일일 분석 리포트 2026-03-18")
            html_body: HTML 형식의 리포트 본문
        """
        if not self._cfg.email_enabled:
            logger.debug("Email 비활성화 상태 — 리포트 발송 생략")
            return
        if not self._cfg.smtp_host or not self._cfg.to_addrs:
            logger.warning("리포트 발송 실패: SMTP 설정 누락 (smtp_host 또는 to_addrs)")
            return

        self._send_email_html(subject, html_body)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _dispatch(self, title: str, body: str) -> None:
        """활성화된 채널로 알림 전송."""
        if self._cfg.slack_enabled and self._cfg.slack_webhook_url:
            self._send_slack(title, body)
        if self._cfg.email_enabled and self._cfg.smtp_host and self._cfg.to_addrs:
            self._send_email(title, body)

    def _send_slack(self, title: str, body: str) -> None:
        """Slack Incoming Webhook으로 메시지 전송."""
        text = f"*{title}*\n{body}"
        payload = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(
            self._cfg.slack_webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    logger.warning(f"Slack 알림 전송 실패: HTTP {resp.status}")
                else:
                    logger.info(f"Slack 알림 전송 성공: {title}")
        except urllib.error.URLError as e:
            logger.warning(f"Slack 알림 전송 실패 (URLError): {e}")
        except Exception as e:
            logger.warning(f"Slack 알림 예외 발생: {e}")

    def _send_email(self, title: str, body: str) -> None:
        """SMTP(STARTTLS)로 plain text 이메일 전송."""
        msg = MIMEMultipart()
        msg["From"] = self._cfg.from_addr
        msg["To"] = ", ".join(self._cfg.to_addrs)
        msg["Subject"] = title
        msg.attach(MIMEText(body, "plain", "utf-8"))

        try:
            context = ssl.create_default_context()
            with smtplib.SMTP(self._cfg.smtp_host, self._cfg.smtp_port, timeout=10) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.login(self._cfg.smtp_user, self._cfg.smtp_password)
                smtp.sendmail(
                    self._cfg.from_addr,
                    self._cfg.to_addrs,
                    msg.as_string(),
                )
            logger.info(f"Email 알림 전송 성공: {title}")
        except smtplib.SMTPAuthenticationError as e:
            logger.warning(f"Email 알림 전송 실패 (인증 오류): {e}")
        except smtplib.SMTPException as e:
            logger.warning(f"Email 알림 전송 실패 (SMTP): {e}")
        except Exception as e:
            logger.warning(f"Email 알림 예외 발생: {e}")

    def _send_email_html(self, subject: str, html_body: str) -> None:
        """SMTP(STARTTLS)로 HTML 이메일 전송 (v5.2).

        리포트 발송 전용입니다. 기존 _send_email()의 plain text와 독립적으로 동작합니다.

        Args:
            subject:   메일 제목
            html_body: HTML 형식의 메일 본문
        """
        msg = MIMEMultipart()
        msg["From"] = self._cfg.from_addr
        msg["To"] = ", ".join(self._cfg.to_addrs)
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            context = ssl.create_default_context()
            with smtplib.SMTP(self._cfg.smtp_host, self._cfg.smtp_port, timeout=10) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.login(self._cfg.smtp_user, self._cfg.smtp_password)
                smtp.sendmail(
                    self._cfg.from_addr,
                    self._cfg.to_addrs,
                    msg.as_string(),
                )
            logger.info(f"리포트 Email 전송 성공: {subject}")
        except smtplib.SMTPAuthenticationError as e:
            logger.warning(f"리포트 Email 전송 실패 (인증 오류): {e}")
        except smtplib.SMTPException as e:
            logger.warning(f"리포트 Email 전송 실패 (SMTP): {e}")
        except Exception as e:
            logger.warning(f"리포트 Email 전송 예외 발생: {e}")


def build_notifier(notify_cfg: Optional[dict]) -> Notifier:
    """settings.yaml의 notify 섹션으로 Notifier 인스턴스를 생성합니다.

    민감 정보(Webhook URL, SMTP 계정)는 환경변수가 settings.yaml보다 우선합니다.

    Args:
        notify_cfg: config_loader가 파싱한 notify 딕셔너리 (없으면 None)

    Returns:
        Notifier: 알림이 비활성화된 경우에도 항상 유효한 인스턴스 반환
    """
    if not notify_cfg:
        return Notifier(NotifyConfig())

    slack = notify_cfg.get("slack", {})
    email = notify_cfg.get("email", {})
    events = notify_cfg.get("events", {})

    cfg = NotifyConfig(
        # Slack — Webhook URL은 환경변수 우선
        slack_enabled=slack.get("enabled", False),
        slack_webhook_url=os.environ.get(
            "NBT_SLACK_WEBHOOK", slack.get("webhook_url", "")
        ),
        # Email — 계정 정보는 환경변수 우선
        email_enabled=email.get("enabled", False),
        smtp_host=email.get("smtp_host", ""),
        smtp_port=email.get("smtp_port", 587),
        smtp_user=os.environ.get("NBT_SMTP_USER", email.get("smtp_user", "")),
        smtp_password=os.environ.get(
            "NBT_SMTP_PASSWORD", email.get("smtp_password", "")
        ),
        from_addr=email.get("from_addr", ""),
        to_addrs=email.get("to_addrs", []),
        # 알림 시점
        on_device_failure=events.get("on_device_failure", True),
        on_diff_detected=events.get("on_diff_detected", True),
        on_summary=events.get("on_summary", True),
    )
    return Notifier(cfg)