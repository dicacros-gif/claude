# -*- coding: utf-8 -*-
"""
run_daily_deep.py
매일 08:00 KST build_deep_highs.main() 호출 (로컬 스케줄러)
서버 환경에서는 .github/workflows/daily.yml (GitHub Actions) 사용 권장
"""
import time
import schedule
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))


def _run():
    print(f"[run_daily_deep] {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')} 실행 시작")
    try:
        import build_deep_highs
        build_deep_highs.main()
    except Exception as e:
        print(f"[run_daily_deep] 오류: {e}")


if __name__ == "__main__":
    schedule.every().day.at("08:00").do(_run)
    print("[run_daily_deep] 스케줄러 시작 — 매일 08:00 KST 실행")
    print("                 서버 배포 시에는 GitHub Actions daily.yml 사용 권장")
    print("                 Ctrl+C로 종료")
    while True:
        schedule.run_pending()
        time.sleep(30)
