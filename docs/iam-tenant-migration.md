# IAM Tenant Endpoint 전환

## 기준 계약

- Canonical source: `/Users/jw/__dev/iam-server/TENANT_ENDPOINT_MIGRATION.md`
- Tenant: `ten_9664c024babc4110` (Lynply)
- Client ID: `knowledge-service`
- Redirect URI: `https://knowledge.lynply.com/callback`
- Issuer: `https://auth.snappytory.com/t/ten_9664c024babc4110`
- Discovery: `https://auth.snappytory.com/t/ten_9664c024babc4110/.well-known/openid-configuration`
- Authorization endpoint: `https://auth.snappytory.com/t/ten_9664c024babc4110/oauth2/authorize`
- Token endpoint: `https://auth.snappytory.com/t/ten_9664c024babc4110/oauth2/token`
- JWKS: `https://auth.snappytory.com/t/ten_9664c024babc4110/oauth2/jwks`

## 수정 범위

- `src/api_keys/auth.py`
  - Tenant issuer/JWKS로 전환한다.
  - `client_id == knowledge-service`, `tenant_id == ten_9664c024babc4110`, 비어 있지 않은 `sub`를 강제한다.
  - 발급 계약에 맞게 audience를 검증한다.
- `src/settings/oauth_session.py`
  - authorize/token endpoint를 Tenant 경로로 전환한다.
  - callback, PKCE, refresh/revoke/logout 흐름을 유지한다.
- 배포 ConfigMap
  - URL, issuer, tenant, client ID만 저장한다.
  - Client secret은 기존 Vault/ExternalSecret 경계를 유지한다.

IAM은 신원만 제공한다. Knowledge API key의 발급, 저장, scope, 폐기, 검증은 계속 Knowledge가 소유한다.

## 완료 게이트

1. 로컬 인증/JWT/OAuth session 테스트가 통과한다.
2. 운영 ConfigMap을 server-side dry-run한다.
3. Argo sync/health와 Pod의 non-secret 설정 주입을 확인한다.
4. 브라우저 login -> callback -> refresh -> logout을 확인한다.
5. Knowledge API key 발급/호출/폐기가 기존대로 동작한다.
6. Lynply Tenant가 아닌 token과 다른 Client audience를 401로 거부한다.

IAM의 legacy endpoint는 모든 서비스 전환이 완료될 때까지 끈지 않는다.
