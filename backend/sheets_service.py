"""
sheets_service.py
------------------
Google Sheets / Drive data layer for the ENCA Digital Training Portal.

Sheet contracts:

  "Students"
    Col A  Reg ID           (also used as the login PASSWORD)
    Col B  Name
    Col C  Gender
    Col D  Username         (used as the login USERNAME)
    Col F  Course

  "Attendance & Assignments"
    Col A  Timetable ID     (matches Col A on the "Timetable" sheet)
    Col B  Course
    Col D  Reg ID
    Col E  Student Name
    Col G  Attendance       ("Present" / "Absent")
    Col H  Status               ("Issued" = assignment visible to student)
    Col I  Assignment Content
    Col J  Submission Status    ("Submitted" once a file is uploaded)
    Col K  Submission Date
    Col L  Submission File (Drive URL)

  "Timetable"
    Col A  Timetable ID     (matches Col A on "Attendance & Assignments")
    Col B  Date             (e.g. "16-Sept-2026")
    Col D  Module
    Col E  Session Focus
    Col G  Learning Resource (YouTube link)
    (Cols C, F, H, I are read too, using the sheet's own header row as labels,
     so the "Course Content" detail popup can show whatever they contain.)

Drive destination for uploaded files (created automatically if missing):
  ENCA Digital Training on Data Analytics and Capacity Building
    -> Data
        -> Assignment Submissions

Authentication: this backend acts AS the Google account that owns the
spreadsheet, using a stored OAuth refresh token — see setup/get_refresh_token.py.
"""

import io
import os
import re
from datetime import date, datetime

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
TIMETABLE_SHEET_NAME = "Timetable"

DRIVE_FOLDER_PATH = [
    "ENCA Digital Training on Data Analytics and Capacity Building",
    "Data",
    "Assignment Submissions",
]

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB safety cap

# "Attendance & Assignments" column indices (0-indexed)
AA_TIMETABLE_ID = 0   # A
AA_COURSE = 1         # B
AA_REG_ID = 3         # D
AA_NAME = 4           # E
AA_ATTENDANCE = 6     # G
AA_STATUS = 7         # H
AA_CONTENT = 8        # I
AA_SUB_STATUS = 9     # J
AA_SUB_DATE = 10      # K
AA_SUB_FILE = 11      # L
AA_ROW_WIDTH = 12

# "Timetable" column indices (0-indexed)
TT_ID = 0        # A
TT_DATE = 1      # B
TT_MODULE = 3    # D
TT_FOCUS = 4     # E
TT_RESOURCE = 6  # G
TT_ROW_WIDTH = 9  # A..I

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


def _get_sheet(spreadsheet, name):
    try:
        return spreadsheet.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        raise RuntimeError(f'Sheet "{name}" was not found in this spreadsheet.')


def _sheet_values(sheet_name):
    spreadsheet = open_spreadsheet()
    return _get_sheet(spreadsheet, sheet_name).get_all_values()


def _padded(row, length):
    """Pads a sheet row so index access never throws on short/blank rows."""
    return row + [""] * max(0, length - len(row))


# ---------------------------------------------------------------------------
# Date parsing — sheet dates look like "16-Sept-2026"
# ---------------------------------------------------------------------------

try:
    from dateutil import parser as _date_parser
    _HAS_DATEUTIL = True
except ImportError:  # pragma: no cover - dateutil is in requirements.txt
    _HAS_DATEUTIL = False

_SEPT_FIX = re.compile(r"sept\b", re.IGNORECASE)
_KNOWN_DATE_FORMATS = ["%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"]


def parse_flexible_date(value):
    """Parses dates like '16-Sept-2026', '16-Sep-2026', '16/09/2026', etc."""
    text = str(value or "").strip()
    if not text:
        return None

    normalised = _SEPT_FIX.sub("Sep", text)  # "Sept" -> "Sep" for strptime/dateutil

    for fmt in _KNOWN_DATE_FORMATS:
        try:
            return datetime.strptime(normalised, fmt).date()
        except ValueError:
            continue

    if _HAS_DATEUTIL:
        try:
            return _date_parser.parse(normalised, dayfirst=True).date()
        except (ValueError, OverflowError, TypeError):
            return None
    return None


_YOUTUBE_PATTERN = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([A-Za-z0-9_-]{6,})"
)


def extract_youtube_id(url):
    if not url:
        return None
    match = _YOUTUBE_PATTERN.search(url)
    return match.group(1) if match else None


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

def get_assignments(reg_id, student_name):
    """
    Returns every "Issued" assignment matched to this student on
    "Attendance & Assignments" (Reg ID + Name). "submitted" is read
    strictly from column J on THAT SAME ROW, and is only ever true when J
    reads exactly "Submitted" (case-insensitive) — so a newly issued
    assignment is always open, and only the specific row that was actually
    submitted shows as submitted. Nothing here looks at any other row.
    """
    reg_id = (reg_id or "").strip()
    student_name = (student_name or "").strip()

    rows = _sheet_values(ASSIGNMENTS_SHEET_NAME)

    assignments = []
    for row in rows[1:]:
        row = _padded(row, AA_ROW_WIDTH)
        course = str(row[AA_COURSE]).strip()
        row_reg_id = str(row[AA_REG_ID]).strip()
        row_name = str(row[AA_NAME]).strip()
        status = str(row[AA_STATUS]).strip()
        content = str(row[AA_CONTENT]).strip()
        sub_status = str(row[AA_SUB_STATUS]).strip()

        if (
            row_reg_id == reg_id
            and row_name == student_name
            and status.lower() == "issued"
            and content
        ):
            assignments.append({
                "course": course,
                "content": content,
                "submitted": sub_status.lower() == "submitted",
            })
    return {"success": True, "assignments": assignments}


# ---------------------------------------------------------------------------
# Phase 3 & 4 — Submission handling (writes into Attendance & Assignments)
# ---------------------------------------------------------------------------

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
    the SAME level of access on the submissions folder.
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
            role = perm.get("role")
            if not email or perm.get("type") != "user" or role == "owner":
                continue
            if existing_by_email.get(email) == role:
                continue
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
    """
    Uploads the file to Drive, then records the submission directly on the
    matching "Attendance & Assignments" row (columns J/K/L) — found by
    Reg ID (Col D) AND Course (Col B) AND Status = "Issued", so a student
    with more than one active assignment updates the correct one.
    """
    reg_id = (reg_id or "").strip()
    course = (course or "").strip()

    if not (reg_id and student_name and course):
        return {"success": False, "message": "Missing required submission details."}
    if not file_storage or not file_storage.filename:
        return {"success": False, "message": "Please attach a file before submitting."}

    data = file_storage.read()
    if len(data) > MAX_UPLOAD_BYTES:
        return {"success": False, "message": "File is too large (20 MB limit)."}

    spreadsheet = open_spreadsheet()
    worksheet = _get_sheet(spreadsheet, ASSIGNMENTS_SHEET_NAME)
    rows = worksheet.get_all_values()

    target_row_number = None
    for idx, row in enumerate(rows[1:], start=2):  # sheet rows are 1-indexed; header is row 1
        row = _padded(row, AA_STATUS + 1)
        row_reg_id = str(row[AA_REG_ID]).strip()
        row_course = str(row[AA_COURSE]).strip()
        row_status = str(row[AA_STATUS]).strip().lower()
        if row_reg_id == reg_id and row_course == course and row_status == "issued":
            target_row_number = idx
            break

    if target_row_number is None:
        return {"success": False, "message": "Could not find a matching assignment row for this submission."}

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

    # Phase 3 (revised) — log the submission directly on the matched row
    submitted_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    worksheet.update(f"J{target_row_number}:L{target_row_number}", [["Submitted", submitted_at, file_url]])

    return {
        "success": True,
        "message": f"Assignment submitted successfully for {course}.",
        "fileUrl": file_url,
    }


# ---------------------------------------------------------------------------
# Course Content tab
# ---------------------------------------------------------------------------

def get_course_content(reg_id):
    """
    Builds the Course Content list from the "Timetable" sheet (cols A-I),
    locking any row whose date (Col B) is in the future, and attaching this
    student's Attendance (Col G on "Attendance & Assignments") by matching
    Timetable Col A == Attendance & Assignments Col A, for THIS student's
    Reg ID (Col D) specifically.
    """
    reg_id = (reg_id or "").strip()

    spreadsheet = open_spreadsheet()
    timetable_ws = _get_sheet(spreadsheet, TIMETABLE_SHEET_NAME)
    timetable_rows = timetable_ws.get_all_values()
    if not timetable_rows:
        return {"success": True, "headers": [], "entries": []}

    headers = _padded(timetable_rows[0], TT_ROW_WIDTH)[:TT_ROW_WIDTH]

    attendance_lookup = {}
    try:
        aa_ws = _get_sheet(spreadsheet, ASSIGNMENTS_SHEET_NAME)
        for row in aa_ws.get_all_values()[1:]:
            row = _padded(row, AA_ATTENDANCE + 1)
            key = (str(row[AA_TIMETABLE_ID]).strip(), str(row[AA_REG_ID]).strip())
            attendance_lookup[key] = str(row[AA_ATTENDANCE]).strip()
    except RuntimeError:
        pass  # Attendance sheet missing — attendance just shows as unavailable

    today = datetime.utcnow().date()
    entries = []

    for row in timetable_rows[1:]:
        row = _padded(row, TT_ROW_WIDTH)
        timetable_id = str(row[TT_ID]).strip()
        date_str = str(row[TT_DATE]).strip()
        module = str(row[TT_MODULE]).strip()
        session_focus = str(row[TT_FOCUS]).strip()
        resource = str(row[TT_RESOURCE]).strip()

        if not (timetable_id or date_str or module or session_focus):
            continue  # skip fully blank rows

        parsed_date = parse_flexible_date(date_str)
        locked = parsed_date is None or parsed_date > today
        attendance = attendance_lookup.get((timetable_id, reg_id), "") or "—"

        entry = {
            "id": timetable_id,
            "date": date_str,
            "module": module,
            "sessionFocus": session_focus,
            "attendance": attendance,
            "locked": locked,
            "learningResource": None,
            "videoId": None,
            "details": None,
        }

        if not locked:
            entry["learningResource"] = resource or None
            entry["videoId"] = extract_youtube_id(resource)
            details = {
                (headers[i] if i < len(headers) and headers[i] else f"Column {chr(65 + i)}"): row[i]
                for i in range(TT_ROW_WIDTH)
            }
            details["Attendance"] = attendance
            entry["details"] = details

        entries.append(entry)

    def sort_key(entry):
        parsed = parse_flexible_date(entry["date"])
        return (parsed is None, parsed or date.max)

    entries.sort(key=sort_key)

    return {"success": True, "headers": headers, "entries": entries}


# ---------------------------------------------------------------------------
# Dashboard summary cards
# ---------------------------------------------------------------------------

def get_dashboard_summary(reg_id):
    """
    Attendance card: counts Present/Absent from Col G, this student's rows only.
    Performance card: attendance rate + assignment submission rate, averaged.
    """
    reg_id = (reg_id or "").strip()
    rows = _sheet_values(ASSIGNMENTS_SHEET_NAME)

    present = 0
    absent = 0
    issued = 0
    submitted = 0

    for row in rows[1:]:
        row = _padded(row, AA_ROW_WIDTH)
        if str(row[AA_REG_ID]).strip() != reg_id:
            continue

        attendance = str(row[AA_ATTENDANCE]).strip().lower()
        if attendance == "present":
            present += 1
        elif attendance == "absent":
            absent += 1

        status = str(row[AA_STATUS]).strip().lower()
        if status == "issued":
            issued += 1
            if str(row[AA_SUB_STATUS]).strip().lower() == "submitted":
                submitted += 1

    total_attendance = present + absent
    attendance_rate = round((present / total_attendance) * 100, 1) if total_attendance else 0.0
    submission_rate = round((submitted / issued) * 100, 1) if issued else 0.0
    has_any_data = total_attendance > 0 or issued > 0
    performance_score = round((attendance_rate + submission_rate) / 2, 1) if has_any_data else 0.0

    return {
        "success": True,
        "present": present,
        "absent": absent,
        "attendanceRate": attendance_rate,
        "issued": issued,
        "submitted": submitted,
        "submissionRate": submission_rate,
        "performanceScore": performance_score,
    }
