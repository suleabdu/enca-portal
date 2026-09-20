"""
sheets_service.py
------------------
Google Sheets / Drive data layer for the ENCA Digital Training Portal.
Python port of the original Apps Script Code.gs, same sheet contracts:

  "Students"
    Col A  Reg ID           (also used as the login PASSWORD)
    Col B  Name
    Col C  Gender
    Col D  Username         (used as the login USERNAME)
    Col F  Course

  "Attendance & Assignments"
    Col B  Course
    Col D  Reg ID
    Col E  Student Name
    Col H  Status               ("Issued" = visible to student)
    Col I  Assignment Content

  "Assignments Submissions"  (created automatically on first submission)
    Col A  ID
    Col B  Date
    Col C  Student Name
    Col D  Reg ID
    Col E  Course
    Col F  Submission File (Drive URL)

Drive destination for uploaded files (created automatically if missing):
  ENCA Digital Training on Data Analytics and Capacity Building
    -> Data
        -> Assignment Submissions

Authentication: this backend acts AS the Google account that owns the
spreadsheet (the same account that used to "Execute as: Me" under Apps
Script), using a stored OAuth refresh token — NOT a service account. See
setup/get_refresh_token.py and the README for why.
"""

import io
import os
import re
from datetime import datetime

import gspread
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

STUDENTS_SHEET_NAME = "Students"
ASSIGNMENTS_SHEET_NAME = "Attendance & Assignments"
SUBMISSIONS_SHEET_NAME = "Assignments Submissions"
SUBMISSIONS_HEADERS = ["ID", "Date", "Student Name", "Reg ID", "Course", "Submission File"]

DRIVE_FOLDER_PATH = [
    "ENCA Digital Training on Data Analytics and Capacity Building",
    "Data",
    "Assignment Submissions",
]

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB safety cap

SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]

# ---------------------------------------------------------------------------
# Auth — OAuth refresh token for the sheet/Drive-owning account
# ---------------------------------------------------------------------------

_credentials = None
_gspread_client = None
_drive_service = None


def _load_credentials():
    client_id = os.environ["GOOGLE_CLIENT_ID"]
    client_secret = os.environ["GOOGLE_CLIENT_SECRET"]
    refresh_token = os.environ["GOOGLE_REFRESH_TOKEN"]
    return UserCredentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )


def _creds():
    global _credentials
    if _credentials is None:
        _credentials = _load_credentials()
    return _credentials


def get_gspread_client():
    global _gspread_client
    if _gspread_client is None:
        _gspread_client = gspread.authorize(_creds())
    return _gspread_client


def get_drive_service():
    global _drive_service
    if _drive_service is None:
        _drive_service = build("drive", "v3", credentials=_creds())
    return _drive_service


def open_spreadsheet():
    return get_gspread_client().open_by_key(SPREADSHEET_ID)


def _sheet_values(sheet_name):
    spreadsheet = open_spreadsheet()
    try:
        worksheet = spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        raise RuntimeError(f'Sheet "{sheet_name}" was not found in this spreadsheet.')
    return worksheet.get_all_values()


def _padded(row, length):
    """Pads a sheet row so index access never throws on short/blank rows."""
    return row + [""] * max(0, length - len(row))


# ---------------------------------------------------------------------------
# Phase 1 — Authentication
# ---------------------------------------------------------------------------

def authenticate(username, password):
    username = (username or "").strip()
    password = (password or "").strip()
    if not username or not password:
        return {"success": False, "message": "Please enter both username and password."}

    rows = _sheet_values(STUDENTS_SHEET_NAME)
    for row in rows[1:]:
        row = _padded(row, 6)
        reg_id = str(row[0]).strip()   # Col A
        name = str(row[1]).strip()     # Col B
        gender = str(row[2]).strip()   # Col C
        uname = str(row[3]).strip()    # Col D
        course = str(row[5]).strip()   # Col F

        if uname == username and reg_id == password:
            return {
                "success": True,
                "student": {"regId": reg_id, "name": name, "gender": gender, "course": course},
            }
    return {"success": False, "message": "Incorrect username or password."}


# ---------------------------------------------------------------------------
# Phase 2 — Assignments
# ---------------------------------------------------------------------------

def get_submitted_courses(reg_id):
    spreadsheet = open_spreadsheet()
    try:
        worksheet = spreadsheet.worksheet(SUBMISSIONS_SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        return []

    rows = worksheet.get_all_values()
    courses = []
    for row in rows[1:]:
        row = _padded(row, 6)
        if str(row[3]).strip() == reg_id:  # Col D = Reg ID
            courses.append(str(row[4]).strip())  # Col E = Course
    return courses


def get_assignments(reg_id, student_name):
    reg_id = (reg_id or "").strip()
    student_name = (student_name or "").strip()

    rows = _sheet_values(ASSIGNMENTS_SHEET_NAME)
    submitted_courses = get_submitted_courses(reg_id)

    assignments = []
    for row in rows[1:]:
        row = _padded(row, 9)
        course = str(row[1]).strip()      # Col B
        row_reg_id = str(row[3]).strip()  # Col D
        row_name = str(row[4]).strip()    # Col E
        status = str(row[7]).strip()      # Col H
        content = str(row[8]).strip()     # Col I

        if (
            row_reg_id == reg_id
            and row_name == student_name
            and status.lower() == "issued"
            and content
        ):
            assignments.append({
                "course": course,
                "content": content,
                "submitted": course in submitted_courses,
            })
    return {"success": True, "assignments": assignments}


# ---------------------------------------------------------------------------
# Phase 3 & 4 — Submission handling
# ---------------------------------------------------------------------------

def _get_or_create_submissions_sheet():
    spreadsheet = open_spreadsheet()
    try:
        return spreadsheet.worksheet(SUBMISSIONS_SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=SUBMISSIONS_SHEET_NAME, rows=1000, cols=len(SUBMISSIONS_HEADERS)
        )
        worksheet.append_row(SUBMISSIONS_HEADERS)
        worksheet.format(
            f"A1:{chr(64 + len(SUBMISSIONS_HEADERS))}1", {"textFormat": {"bold": True}}
        )
        return worksheet


def _find_or_create_drive_folder(name, parent_id=None):
    drive = get_drive_service()
    parent_clause = f"'{parent_id}' in parents" if parent_id else "'root' in parents"
    query = (
        f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false and {parent_clause}"
    )
    response = drive.files().list(q=query, fields="files(id, name)").execute()
    files = response.get("files", [])
    if files:
        return files[0]["id"]

    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id] if parent_id else ["root"],
    }
    folder = drive.files().create(body=metadata, fields="id").execute()
    return folder["id"]


def _get_or_create_folder_path(path_parts):
    parent_id = None
    for part in path_parts:
        parent_id = _find_or_create_drive_folder(part, parent_id)
    return parent_id


def _build_submission_filename(reg_id, course, original_filename):
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    clean_course = re.sub(r"[^\w\- ]+", "", course or "Course").strip()
    clean_reg = re.sub(r"[^\w\-]+", "", reg_id or "")
    parts = [p for p in [clean_reg, clean_course, stamp, original_filename] if p]
    return "_".join(parts)


def sync_folder_permissions_with_sheet(folder_id):
    """
    Gives every OTHER person who currently has access to the spreadsheet
    (instructors, staff — anyone besides the account this backend runs as)
    the SAME level of access on the submissions folder:

        spreadsheet editor (writer)    -> folder editor
        spreadsheet commenter          -> folder commenter
        spreadsheet viewer (reader)    -> folder viewer

    This runs on every submission so newly-added instructors get folder
    access automatically, without a separate manual step. A sharing hiccup
    for one person never blocks the submission itself.
    """
    drive = get_drive_service()
    try:
        sheet_perms = (
            drive.permissions()
            .list(fileId=SPREADSHEET_ID, fields="permissions(type,role,emailAddress)")
            .execute()
            .get("permissions", [])
        )
        existing_folder_perms = (
            drive.permissions()
            .list(fileId=folder_id, fields="permissions(type,role,emailAddress)")
            .execute()
            .get("permissions", [])
        )
        existing_by_email = {
            p.get("emailAddress"): p.get("role")
            for p in existing_folder_perms
            if p.get("emailAddress")
        }

        for perm in sheet_perms:
            email = perm.get("emailAddress")
            role = perm.get("role")  # 'owner' | 'writer' | 'commenter' | 'reader'
            if not email or perm.get("type") != "user":
                continue
            if role == "owner":
                continue  # that's the account this backend already runs as

            if existing_by_email.get(email) == role:
                continue  # already matches — nothing to do

            try:
                drive.permissions().create(
                    fileId=folder_id,
                    body={"type": "user", "role": role, "emailAddress": email},
                    sendNotificationEmail=False,
                ).execute()
            except HttpError as err:
                print(f"Could not share folder with {email}: {err}")
    except HttpError as err:
        print(f"Could not sync folder permissions with sheet collaborators: {err}")


def submit_assignment(reg_id, student_name, course, description, file_storage):
    if not (reg_id and student_name and course):
        return {"success": False, "message": "Missing required submission details."}
    if not file_storage or not file_storage.filename:
        return {"success": False, "message": "Please attach a file before submitting."}

    data = file_storage.read()
    if len(data) > MAX_UPLOAD_BYTES:
        return {"success": False, "message": "File is too large (20 MB limit)."}

    # Phase 4 — store the file in the correct Drive subfolder
    folder_id = _get_or_create_folder_path(DRIVE_FOLDER_PATH)
    sync_folder_permissions_with_sheet(folder_id)

    drive = get_drive_service()
    safe_name = _build_submission_filename(reg_id, course, file_storage.filename)
    media = MediaIoBaseUpload(
        io.BytesIO(data),
        mimetype=file_storage.mimetype or "application/octet-stream",
        resumable=False,
    )
    metadata = {
        "name": safe_name,
        "parents": [folder_id],
        "description": (
            f"Submitted by {student_name} ({reg_id}) for {course}"
            + (f"\n\nNote: {description}" if description else "")
        ),
    }
    created = drive.files().create(body=metadata, media_body=media, fields="id, webViewLink").execute()
    file_url = created.get("webViewLink")

    # Phase 3 — log the submission
    worksheet = _get_or_create_submissions_sheet()
    new_id = len(worksheet.get_all_values())  # header = row 1, so this equals (existing rows + 1)
    worksheet.append_row([
        new_id,
        datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        student_name,
        reg_id,
        course,
        file_url,
    ])

    return {
        "success": True,
        "message": f"Assignment submitted successfully for {course}.",
        "fileUrl": file_url,
    }
