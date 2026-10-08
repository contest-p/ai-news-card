"""Engine server connection; never imports Backend or exposes credentials."""

import json
import os
from pathlib import Path


def create_client(project: str, credential_path: str | None = None):
    from dotenv import load_dotenv
    from google.cloud import firestore
    from google.oauth2 import service_account

    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / "engine" / ".env", override=False)
    load_dotenv(root / "backend" / ".env", override=False)
    path = credential_path or os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY")
    if path:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise ValueError("CREDENTIAL_FILE_NOT_FOUND")
        credentials = service_account.Credentials.from_service_account_file(str(resolved))
        if credentials.project_id != project:
            raise ValueError("CREDENTIAL_PROJECT_MISMATCH")
        return firestore.Client(project=project, credentials=credentials)
    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if raw:
        try:
            credentials = service_account.Credentials.from_service_account_info(json.loads(raw))
        except (ValueError, TypeError, KeyError):
            raise ValueError("CREDENTIAL_JSON_INVALID") from None
        if credentials.project_id != project:
            raise ValueError("CREDENTIAL_PROJECT_MISMATCH")
        return firestore.Client(project=project, credentials=credentials)
    # ADC supports local gcloud login and GOOGLE_APPLICATION_CREDENTIALS,
    # including federated credentials without assuming a service-account key.
    return firestore.Client(project=project)
