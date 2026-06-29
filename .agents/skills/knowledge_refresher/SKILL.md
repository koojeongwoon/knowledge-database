---
name: knowledge_refresher
description: 로컬 지식베이스의 범주형 노화(Drift)를 백그라운드에서 감지하고, 대화형 알림 대장을 통해 최신화 및 아카이빙을 수행합니다.
allowed-tools:
  - run_command
  - replace_file_content
  - write_file
metadata:
  version: 1.0.0
  author: Antigravity
---

# knowledge_refresher

본 스킬은 지식베이스의 신선도(Freshness)를 유지하는 도구입니다.
백그라운드 스케줄러가 `.agents/schedules/`를 스캔하여 갱신일이 지난 대상을 분석하고, 임시 초안 빌드 및 알림 대장을 갱신하는 절차를 담당합니다.

## 1. 실행 가이드
- **백그라운드 감지 실행**: `scripts/run_refresher.py`를 실행하여 스케줄이 만료된 노트의 팩트 체크와 임시 초안을 자동 빌드하고, `.agents/drift_notifications.json`에 알림 대장을 누적합니다.
- **알림 확인**: `.agents/drift_notifications.json` 파일이 존재하고 데이터가 들어있다면, 에이전트는 기동 또는 대화 세션 진입 즉시 갱신 권고 알림 요약을 사용자에게 리포팅합니다.
