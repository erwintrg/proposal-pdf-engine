"""Google Drive upload (real mode only: render.py --drive).

Uploads the PDF to <parent>/Proposals/<slug>/ (folders are created on first use), replaces an
earlier upload with the same file name, and returns the view link. With --share, anyone with the
link can view the file; without it the file stays private to the account.

Credentials, first match wins:
  GOOGLE_SERVICE_ACCOUNT_FILE                                       service account JSON file
  GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET + GOOGLE_REFRESH_TOKEN    OAuth user credentials
Scope: drive.file, so the tool only ever sees files and folders it created itself.
DRIVE_PARENT_FOLDER_ID (optional) puts the Proposals folder inside an existing folder; with the
drive.file scope that folder has to be one this tool created.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveError(Exception):
    pass


def credentials_from_env(env: Mapping[str, str] = os.environ):
    if env.get("GOOGLE_SERVICE_ACCOUNT_FILE"):
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_file(env["GOOGLE_SERVICE_ACCOUNT_FILE"], scopes=SCOPES)
    if all(env.get(k) for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")):
        from google.oauth2.credentials import Credentials

        return Credentials(
            token=None,
            refresh_token=env["GOOGLE_REFRESH_TOKEN"],
            client_id=env["GOOGLE_CLIENT_ID"],
            client_secret=env["GOOGLE_CLIENT_SECRET"],
            token_uri="https://oauth2.googleapis.com/token",
            scopes=SCOPES,
        )
    raise DriveError("no Google credentials: set GOOGLE_SERVICE_ACCOUNT_FILE, "
                     "or GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN")


def _quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def drive_service():
    try:
        from googleapiclient.discovery import build
    except ImportError:
        raise DriveError("the Google client is missing: pip install -r requirements-real.txt") from None
    return build("drive", "v3", credentials=credentials_from_env(), cache_discovery=False)


def upload_pdf(pdf_path: Path, slug: str, *, share: bool = False, parent_id: Optional[str] = None, service=None) -> str:
    """Upload pdf_path and return its webViewLink. `service` can be injected for tests."""
    from googleapiclient.http import MediaFileUpload

    service = service or drive_service()
    files = service.files()
    parent_id = parent_id or os.environ.get("DRIVE_PARENT_FOLDER_ID") or None

    def ensure_folder(name: str, parent: Optional[str]) -> str:
        query = (f"name = '{_quote(name)}' and mimeType = '{FOLDER_MIME}' and trashed = false "
                 f"and '{parent or 'root'}' in parents")
        found = files.list(q=query, fields="files(id)", spaces="drive").execute().get("files", [])
        if found:
            return found[0]["id"]
        body = {"name": name, "mimeType": FOLDER_MIME, **({"parents": [parent]} if parent else {})}
        return files.create(body=body, fields="id").execute()["id"]

    folder = ensure_folder(slug, ensure_folder("Proposals", parent_id))
    pdf_path = Path(pdf_path)
    media = MediaFileUpload(str(pdf_path), mimetype="application/pdf", resumable=False)
    query = f"name = '{_quote(pdf_path.name)}' and '{folder}' in parents and trashed = false"
    existing = files.list(q=query, fields="files(id)", spaces="drive").execute().get("files", [])
    if existing:
        uploaded = files.update(fileId=existing[0]["id"], media_body=media, fields="id, webViewLink").execute()
    else:
        uploaded = files.create(
            body={"name": pdf_path.name, "parents": [folder]}, media_body=media, fields="id, webViewLink"
        ).execute()
    if share:
        service.permissions().create(fileId=uploaded["id"], body={"type": "anyone", "role": "reader"}).execute()
    return uploaded.get("webViewLink") or f"https://drive.google.com/file/d/{uploaded['id']}/view"
