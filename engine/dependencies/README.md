# Python 의존성

저장소 루트에서 실행합니다.

```powershell
python -m pip install -r engine/dependencies/requirements.txt
python -m pip install -r engine/dependencies/requirements-rag.txt
```

첫 명령은 기본 수집·Firestore·설정 패키지, 두 번째는 RAG 임베딩 패키지를 추가합니다. 필요한 기능에 맞춰 설치하세요.

requirements 파일 4개는 내용 변경 없이 함께 이동했습니다. 내부 `-r` 상대 참조를 유지하려면 함께 보관합니다. `*-lock.txt`는 하위 의존성까지 버전을 고정하는 파일이므로 임시 결과물이 아닙니다.

Node·Playwright 의존성은 기존 engine/package.json과 package-lock.json으로 관리하며 설치 명령은 `npm ci --prefix engine`입니다.
