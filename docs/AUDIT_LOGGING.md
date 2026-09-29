# 감사 로그의 메모리 상한과 DB 전송

`log_audit()`는 민감 값을 마스킹하고 크기를 제한한 JSON 기록을 만듭니다.
각 기록에는 `event_id`와 원래 기록 시각이 포함됩니다.

## 처리 경로

1. 요청 스레드에서 로컬 SQLite outbox에 기록을 커밋합니다. 이 경로에는 DB 네트워크 호출이 없습니다.
2. 제한된 메모리 큐에 넣고 별도 파일 리스너가 `logs/audit.log`와 stdout에 기록합니다.
3. 별도 DB 작업자가 outbox의 한 기록을 lease로 획득해 PostgreSQL에 전송합니다.
   DB 커밋 후에만 outbox에서 삭제합니다. 네트워크 대기 중 SQLite 락을 보유하지 않습니다.

SQLite 쓰기는 `synchronous=FULL`로 실행합니다. 로컬 디스크 지연은 요청 처리 시간에 영향을 줄 수 있습니다.
이 설계는 DB 장애로 요청 스레드가 네트워크 연결을 기다리는 상황을 제거하며,
모든 부하에서 요청 지연이 없다는 보장은 제공하지 않습니다.

## 기본 상한과 설정

| 환경 변수 | 기본값 | 의미 |
|---|---:|---|
| `AUDIT_QUEUE_SIZE` | 512 | 파일 기록용 메모리 큐의 항목 수 |
| `AUDIT_RECORD_MAX_BYTES` | 16384 | 큐에 넣기 전 JSON 기록의 UTF-8 바이트 상한 |
| `AUDIT_OUTBOX_PATH` | `logs/audit-outbox.sqlite3` | 로컬 DB 전송 대기 파일 |
| `AUDIT_OUTBOX_MAX_BYTES` | 67108864 | 대기 JSON 데이터의 합계 상한, 64 MiB |
| `AUDIT_OUTBOX_MAX_EVENTS` | 10000 | DB 전송 대기 항목 수 |

대기 JSON 바이트 상한은 SQLite 파일 자체의 물리적 크기 상한과 다릅니다.
인덱스·페이지·저널 공간이 추가되며 삭제된 공간은 재사용됩니다.
SQLite 페이지 캐시 예산은 연결당 약 1 MiB입니다.
파일 큐의 메시지 본문은 기본 설정에서 최대 약 8 MiB이며 LogRecord 등 객체 오버헤드는 별도입니다.
마스킹·직렬화 중의 임시 할당량은 원래 입력 크기에 영향을 받습니다.

## 포화와 장애 정책

- 파일 큐가 가득 차면 요청 스레드에서 파일·stdout에 동기 기록합니다.
  추가 큐에 쌓거나 기록을 버리지 않습니다. `file_queue_full_sync_write` 카운터가 증가합니다.
- outbox가 가득 찼거나 쓰기에 실패하면 파일 경로로 기록하고,
  그 기록에 `db_delivery: file_only`를 남깁니다. DB 전송 대상으로 자동 등록되지는 않습니다.
  각각 `outbox_full_file_only`, `outbox_write_failed_file_only` 카운터와 경고가 발생합니다.
  파일에서 event_id를 확인해 별도 복구할 수 있으며 자동 파일 재전송 기능은 포함하지 않습니다.
- 정상적으로 outbox에 등록된 파일 기록에는 `db_delivery: outbox_pending`이 남습니다.
  파일의 이 값은 나중에 DB 전송이 끝나도 변경되지 않습니다.
- 기록이 바이트 상한을 초과하면 payload 전체를 생략하고 `_audit_payload_omitted`,
  원래 마스킹된 기록의 크기와 SHA-256을 남깁니다. `oversized_payload_summarized`로 집계합니다.
  action·status·identity도 각각 64·32·16자로 제한됩니다. 원문 payload의 무손실 보관을 보장하지 않습니다.
- 파일 쓰기 실패는 `file_write_failed`로 보고합니다. outbox까지 실패하면 영구 보존을 보장할 수 없습니다.
  큐·outbox·파일까지 모두 포화된 상태에서 무손실·무제한 가용성을 동시에 보장하지 않습니다.
- 경고는 원인별 첫 발생과 이후 최대 분당 한 번 출력하며 기록 본문·접속 정보·예외 메시지를 출력하지 않습니다.
  카운터는 프로세스 내 누적값이며 재시작 시 초기화됩니다.

## 재시도·재시작·종료

DB 작업자는 공유 애플리케이션 연결 풀을 사용하지 않고 전용 연결 하나를 엽니다.
연결 제한은 `connect_timeout=2`, SQL 제한은 `statement_timeout=3000`, 락 대기는 `lock_timeout=1000`입니다.
실패 기록은 삭제하지 않고 1·2·4초부터 최대 60초까지 backoff합니다.
lease는 60초이므로 작업자 중단 후에는 만료된 기록을 다른 작업자가 다시 처리할 수 있습니다.

전송은 at-least-once입니다. DB 커밋 직후 로컬 acknowledge 전에 중단되면 중복 DB 기록이 생길 수 있습니다.
DB payload의 `_audit_delivery.event_id`와 `recorded_at`으로 같은 원본 기록을 식별합니다.
현재 PostgreSQL 스키마에는 event_id 유일성 제약을 추가하지 않았습니다.

종료 대기는 리스너와 DB 작업자 각각 제한합니다. 남은 outbox 기록은 파일에 남기며,
다음 프로세스가 같은 파일을 사용하면 다시 전송합니다. 파일 큐가 종료까지 비워진다는 보장은 없습니다.
`audit.log`는 일별 회전하며 과거 파일 30개를 보관합니다. 크기 기반 디스크 상한은 별도로 설정해야 합니다.

**운영 전제:** outbox 파일이 있는 디렉토리를 영속 볼륨에 연결해야 Pod 교체 후에도 유지됩니다.
k3s 매니페스트에 서버와 인덱싱 워커 각각의 PVC와 `/app/logs` 마운트를 추가했습니다.
2026-09-29 운영 적용·두 PVC의 바인딩·서버의 실제 Pod 교체 후 재전송 검증을 완료했습니다.
여러 프로세스가 outbox를 공유하면 lease로 전송을 조정하지만 기존 회전 로그 파일은
다중 프로세스 회전을 지원하지 않으므로 파일 기록 경로는 프로세스별로 운영해야 합니다.

## k3s 영속 볼륨

| 실행 대상 | PVC | 마운트 | outbox 경로 |
|---|---|---|---|
| `mcp-server` Deployment | `mcp-audit-logs` | `/app/logs` | `/app/logs/audit-outbox.sqlite3` |
| `knowledge-indexing-worker` CronJob | `indexing-audit-logs` | `/app/logs` | `/app/logs/audit-outbox.sqlite3` |

각 PVC는 `ReadWriteOnce`, 요청 용량 `1Gi`, StorageClass `local-path-retain`을 사용합니다.
StorageClass는 k3s 저장소의 `namespaces/infra/shared/storageclass-retain.yaml`에 정의되어 있습니다.
`infra`에서 이 클래스를 먼저 적용해야 하며 `WaitForFirstConsumer`이므로 Pod 스케줄링 전까지
PVC가 Pending인 것은 정상일 수 있습니다.

디렉토리 전체를 마운트하여 SQLite 저널과 `audit.log`, 회전 파일도 같은 볼륨에 보관합니다.
두 실행 대상은 별도 볼륨을 사용하여 파일 회전을 분리합니다. 현재 서버는 단일 프로세스·replica 1이며,
워커는 `concurrencyPolicy: Forbid`입니다. 동일 PVC를 여러 로그 기록 프로세스에 공유하도록
replica·프로세스 수를 늘리거나 수동 Job을 겹쳐 실행하기 전에 로그 경로를 분리해야 합니다.

`Prune=false`는 Argo CD 자동 prune에서 PVC를 보존하고, `Retain`은 PVC 삭제 후 PV 데이터를
즉시 지우지 않도록 합니다. PVC를 삭제하면 재연결에는 수동 복구가 필요하므로 정상 재배포에서
PVC를 삭제하거나 새 이름으로 교체하지 않습니다.

local-path는 노드의 로컬 디스크를 사용합니다. 같은 디스크를 사용할 수 있는 Pod 교체는 보존 대상이지만,
노드·디스크 손실은 별도 백업과 복구가 필요합니다. 요청 용량 `1Gi`는 local-path의 실제 디스크 사용량을
강제로 제한하지 않습니다. 일별 로그와 SQLite 물리 파일 사용량·노드 여유 공간을 별도로 관리합니다.
근거: [Kubernetes PV 수명주기](https://kubernetes.io/docs/concepts/storage/persistent-volumes/),
[local-path-provisioner 제한](https://github.com/rancher/local-path-provisioner#cons).

운영 적용은 outbox 소스가 포함된 새 이미지 배포와 함께 진행합니다.
현재 매니페스트에는 검증한 소스 커밋 `8e1c009847a69b7b40a0ae18971cbf8c37d0ecec` 이미지가 반영되어 있습니다.
적용 후 운영 클러스터에서 다음을 확인합니다.

```sh
kubectl get storageclass local-path-retain
kubectl -n llm-wiki get pvc mcp-audit-logs indexing-audit-logs
kubectl -n llm-wiki rollout status deployment/mcp-server
kubectl -n llm-wiki exec deployment/mcp-server -c mcp-app -- \
  sh -c 'test "$AUDIT_OUTBOX_PATH" = /app/logs/audit-outbox.sqlite3 && df -h /app/logs'
```

완료 조건은 두 PVC의 Bound 상태, 서버와 워커의 볼륨 연결, 격리된 DB 장애 상황에서 생성한
미전송 기록이 Pod 교체 후 같은 event_id로 DB에 전송되는 것입니다. 파일 존재·Pod Ready만으로
재전송 검증을 대신하지 않습니다. 최초 마운트는 이전 컨테이너의 `/app/logs`를 가리므로 기존
미전송 기록이 있다면 최초 교체 전에 별도 보관해야 합니다.

### 2026-09-29 운영 검증 결과

- 소스·이미지 커밋: `8e1c009847a69b7b40a0ae18971cbf8c37d0ecec`.
  [ARM64 이미지 CI](https://github.com/koojeongwoon/knowledge-database/actions/runs/36511787928) 성공.
- GitOps 커밋: `236583244ee124400cefe09ca57df448553802a0`.
  Argo CD `app-llm-wiki`는 해당 revision에서 `Synced / Healthy`를 확인했습니다.
- 서버·워커의 실제 imageID:
  `sha256:4fc25e51df5cb83ec271f5ccf4ff3282c0bcb3be9ff809687a97c228e5338c55`.
  레지스트리 OCI index digest와 일치합니다.
- 두 PVC는 Bound이며 서버의 `/app/logs` 마운트와 워커의 `indexing-audit-logs` 연결을 확인했습니다.
  새 워커 Job `knowledge-indexing-worker-29844141`의 종료 코드는 0입니다.
- 서버 Pod를 `mcp-server-df68b449-v9626`에서 `mcp-server-df68b449-jv8dz`로 실제 교체했습니다.
  UID도 `8d5a53e4-21b4-49a1-a764-bf8598b65638`에서
  `e9b09e0a-1fc1-448e-99d2-94ee35429db9`로 변경됐습니다.
- 합성 기록 `1de2965f-fd1b-4245-81ef-6fe65ff444d0`은 실제 `log_audit()`와 운영 outbox에 등록했습니다.
  검증 프로세스의 SQLite TEMP trigger로 이 기록만 전송을 미뤘고,
  교체 후 동일 해시를 확인해 지연을 해제했습니다. 새 서버의 전송 작업자가 DB 커밋 후 삭제했습니다.
- 합성 기록 `607bd7fe-8fd6-49b6-8db9-a6c3b0907aeb`은 같은 PVC의 별도 검증 outbox에 등록했습니다.
  검증용 PostgreSQL 연결의 search_path만 `pg_temp`로 바꿔 실제 `UndefinedTable` 실패와 재시도를
  확인했습니다. Pod 교체 후 동일 해시와 시도 횟수 1을 확인했고 정상 연결로 전송해 삭제했습니다.
  운영 DB 전체나 다른 연결에는 장애를 주지 않았습니다.
- 두 event_id는 DB에서 각각 1건 확인됐고 해당 대기 기록은 모두 0건이 됐습니다.
  합성 DB 감사 기록은 검증 증거로 남기며 별도 검증 SQLite 파일은 성공 후 제거했습니다.
- 교체 후 두 외부 호스트의 헬스는 200, 미인증 MCP 요청은 401입니다.
  로그인은 302 → 302 → IAM 로그인 페이지 200까지 확인했습니다. 사용자 로그인 완료 시험은 포함하지 않습니다.
- 최초 교체 전 기존 로그는 노드의
  `/home/ubuntu/knowledge-audit-rollout-20260929/audit-logs-before-pvc.tar`에 권한을 제한해 보관했습니다.
- 기존 CI의 이미지 정리 단계는 사용 중인 태그를 구분하지 않았고 구 이미지 태그는 레지스트리에서
  NotFound였습니다. 시작하지 못한 구 워커 Job을 정리해 예약 실행을 복구했고,
  운영 이미지·연결된 OCI manifest를 보존할 수 없는 개수 기반 자동 삭제 단계는 제거했습니다.

이 검증은 같은 운영 노드·디스크에서 서버 Pod를 정상 종료해 교체한 경우입니다.
노드·디스크 손실, 전원 장애, 워커 Pod의 강제 중단까지 검증한 것은 아닙니다.

## 상태 확인과 검증

실행 중인 프로세스에서 `src.core.logging.audit.audit_status()`를 호출하면 파일 큐 크기·상한,
DB 대기 항목 수·바이트·상한과 원인별 카운터를 읽을 수 있습니다.
별도 프로세스에서 단순 점검하기 위해 audit 모듈을 import하면 작업자도 시작되므로 주의합니다.

```sh
.venv/bin/python -m pytest -q tests/test_audit_delivery.py tests/test_security_guardrail.py
```

실제 DB 복구 테스트는 `AUDIT_TEST_DATABASE_URL`에 격리된 PostgreSQL 테스트 주소를 지정합니다.
고유 테스트 스키마에서 실제 SQL 실패 → outbox 재개방 → DB 복구 → 커밋 확인 → 대기 삭제를 검증하며
끝나면 해당 스키마를 제거합니다. 운영 DB를 테스트 주소로 사용하지 않습니다.

2026-09-29 검증 기록:

- 일반 전체 테스트: 351개 통과, 10개 건너뜀. 감사 DB 핸들러 대체 없이 실행했습니다.
- 감사 처리 테스트: 16개 통과. 포화, I/O 실패, DB·파일 작업 종료 기한과 미확인 기록 보존을 포함합니다.
- 실제 PostgreSQL 감사 복구·서버 커서 검증: 8개 통과.
- k3s 영속 볼륨 설정: `kubectl kustomize namespaces/llm-wiki` 렌더링과 두 PVC의
  namespace·참조·마운트·outbox 환경 변수, Retain 클래스와 실행 정책 연결을 확인했습니다.
  별도 프로세스가 outbox 커밋 후 `os._exit()`로 종료된 뒤 새 프로세스가 같은 파일의 기록을
  전달하고 acknowledge하는 로컬 검증을 통과했습니다. 실제 PV·Pod 검증은 위 운영 결과에 기록했습니다.
- 5,000건 포화 검증: 파일 큐 32개, outbox 128 KiB·100건으로 설정하고 DB 소비를 중단했습니다.
  대기 값은 32개와 97건·130,940바이트에서 제한됐고 파일 기록 5,000건이 모두 남았습니다.
  다섯 반복의 `tracemalloc` 현재 할당량은 약 215~217 KiB, 프로세스 최대 RSS는 약 46.5 MiB였습니다.
  이 측정은 격리된 로컬 부하이며 실제 서비스·S3·Redis를 포함한 장시간 운영 검증은 별도입니다.
