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
    Col A  Lecture ID       (matches Col A on the "Timetable" sheet; also
                             the exact-row key used to file a submission)
    Col B  Course
    Col D  Reg ID
    Col E  Student Name
    Col F  Category         (e.g. "Capstone Project" — scanned by the admin
                             Student Projects tab)
    Col G  Attendance       ("Present" / "Absent")
    Col H  Status               ("Issued" = assignment visible to student)
    Col I  Assignment Content
    Col J  Assignment Mark      (max obtainable mark)
    Col K  Score                (student's obtained score, once graded)
    Col L  Submission Status    ("Submitted" once a file is uploaded)
    Col M  Submission Date
    Col N  Submission File (Drive URL)

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
USERS_SHEET_NAME = "Users"

DRIVE_FOLDER_PATH = [
    "ENCA Digital Training on Data Analytics and Capacity Building",
    "Data",
    "Assignment Submissions",
]

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB safety cap

ADMITTED_STATUS = "admitted"  # case-insensitive comparison value

# "Students" column indices (0-indexed), full A:J schema used by the admin
# dashboard. Note: Col D is used both as the displayed "Email" field AND as
# the student's login username (there is no separate Username column in
# this schema — see README for this assumption).
ST_ID = 0               # A — Reg ID, also the login PASSWORD
ST_NAME = 1             # B
ST_GENDER = 2           # C
ST_EMAIL = 3            # D — login USERNAME
ST_PHONE = 4            # E
ST_COURSE = 5           # F
ST_COHORT = 6           # G
ST_ADMISSION_DATE = 7   # H
ST_YEAR = 8             # I
ST_STATUS = 9           # J — must read "Admitted" to unlock course content/assignments
ST_ROW_WIDTH = 10

# "Users" (admin accounts) column indices (0-indexed)
US_USERNAME = 0  # A
US_NAME = 1      # B
US_PASSWORD = 2  # C
US_PHONE = 3     # D
US_ROW_WIDTH = 4

# "Attendance & Assignments" column indices (0-indexed)
AA_LECTURE_ID = 0     # A  — links to Timetable Col A, and uniquely IDs each session's row
AA_COURSE = 1         # B
AA_CATEGORY = 5       # F  — e.g. "Capstone Project", used to spot capstone rows
AA_REG_ID = 3         # D
AA_NAME = 4           # E
AA_ATTENDANCE = 6     # G
AA_STATUS = 7         # H  — "Issued" = assignment visible to student
AA_CONTENT = 8        # I
AA_MARK = 9           # J  — max mark for this assignment
AA_SCORE = 10         # K  — student's obtained score, once graded
AA_SUB_STATUS = 11    # L  — "Submitted" once a file is uploaded
AA_SUB_DATE = 12      # M
AA_SUB_FILE = 13      # N
AA_ROW_WIDTH = 14

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


def _to_number(value):
    """Parses a Mark/Score cell into a float, or None if blank/unparsable."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


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
        row = _padded(row, ST_ROW_WIDTH)
        reg_id = str(row[ST_ID]).strip()
        name = str(row[ST_NAME]).strip()
        gender = str(row[ST_GENDER]).strip()
        email = str(row[ST_EMAIL]).strip()
        course = str(row[ST_COURSE]).strip()
        status = str(row[ST_STATUS]).strip()

        if email == username and reg_id == password:
            return {
                "success": True,
                "student": {
                    "regId": reg_id,
                    "name": name,
                    "gender": gender,
                    "course": course,
                    "status": status or "Not Set",
                },
            }
    return {"success": False, "message": "Incorrect username or password."}


def get_student_status(reg_id):
    """Looks up a student's admission Status (Col J) by Reg ID (Col A)."""
    reg_id = (reg_id or "").strip()
    rows = _sheet_values(STUDENTS_SHEET_NAME)
    for row in rows[1:]:
        row = _padded(row, ST_ROW_WIDTH)
        if str(row[ST_ID]).strip() == reg_id:
            return str(row[ST_STATUS]).strip()
    return ""


def is_admitted(status):
    return (status or "").strip().lower() == ADMITTED_STATUS


# ---------------------------------------------------------------------------
# Phase 2 — Assignments
# ---------------------------------------------------------------------------

def get_assignments(reg_id, student_name):
    """
    Returns every "Issued" assignment matched to this student on
    "Attendance & Assignments" (Reg ID + Name), including its Lecture ID
    (Col A) and Mark/Score (Cols J/K). "submitted" is read strictly from
    column L on THAT SAME ROW, and is only ever true when L reads exactly
    "Submitted" (case-insensitive) — so a newly issued assignment is always
    open, and only the specific row that was actually submitted shows as
    submitted. Nothing here looks at any other row.
    """
    reg_id = (reg_id or "").strip()
    student_name = (student_name or "").strip()

    status = get_student_status(reg_id)
    if not is_admitted(status):
        return {
            "success": True,
            "restricted": True,
            "status": status or "Not Set",
            "message": (
                f'Your admission status is currently "{status or "Not Set"}". '
                "Please contact the training administrator to access assignments."
            ),
            "assignments": [],
        }

    rows = _sheet_values(ASSIGNMENTS_SHEET_NAME)

    assignments = []
    for row in rows[1:]:
        row = _padded(row, AA_ROW_WIDTH)
        lecture_id = str(row[AA_LECTURE_ID]).strip()
        course = str(row[AA_COURSE]).strip()
        row_reg_id = str(row[AA_REG_ID]).strip()
        row_name = str(row[AA_NAME]).strip()
        status = str(row[AA_STATUS]).strip()
        content = str(row[AA_CONTENT]).strip()
        mark = str(row[AA_MARK]).strip()
        score = str(row[AA_SCORE]).strip()
        sub_status = str(row[AA_SUB_STATUS]).strip()

        if (
            row_reg_id == reg_id
            and row_name == student_name
            and status.lower() == "issued"
            and content
        ):
            assignments.append({
                "lectureId": lecture_id,
                "course": course,
                "content": content,
                "mark": mark or None,
                "score": score or None,
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


def submit_assignment(lecture_id, reg_id, student_name, course, description, file_storage):
    """
    Uploads the file to Drive, then records the submission directly on the
    matching "Attendance & Assignments" row (columns L/M/N) — found by
    Lecture ID (Col A) AND Reg ID (Col D). This is an exact-row key, unlike
    matching on the free-text Course name (which previously failed silently
    whenever a stray space or capitalization difference meant the row was
    never found — that was the root cause of submissions not writing).
    """
    lecture_id = (lecture_id or "").strip()
    reg_id = (reg_id or "").strip()
    course = (course or "").strip()

    if not (lecture_id and reg_id and student_name and course):
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
        row = _padded(row, AA_REG_ID + 1)
        row_lecture_id = str(row[AA_LECTURE_ID]).strip()
        row_reg_id = str(row[AA_REG_ID]).strip()
        if row_lecture_id == lecture_id and row_reg_id == reg_id:
            target_row_number = idx
            break

    if target_row_number is None:
        return {
            "success": False,
            "message": "Could not find a matching row for this Lecture ID and Student ID.",
        }

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
            f"Submitted by {student_name} ({reg_id}) for {course} (Lecture ID: {lecture_id})"
            + (f"\n\nNote: {description}" if description else "")
        ),
    }
    created = drive.files().create(body=metadata, media_body=media, fields="id, webViewLink").execute()
    file_url = created.get("webViewLink")

    # Phase 3 (revised) — log the submission directly on the matched row.
    # Keyword arguments here are deliberate: gspread flipped the positional
    # order of update()'s arguments between major versions, so passing
    # range_name/values by name avoids that ambiguity entirely.
    submitted_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    worksheet.update(
        range_name=f"L{target_row_number}:N{target_row_number}",
        values=[["Submitted", submitted_at, file_url]],
    )

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

    status = get_student_status(reg_id)
    if not is_admitted(status):
        return {
            "success": True,
            "restricted": True,
            "status": status or "Not Set",
            "message": (
                f'Your admission status is currently "{status or "Not Set"}". '
                "Please contact the training administrator to access course content."
            ),
            "headers": [],
            "entries": [],
        }

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
            key = (str(row[AA_LECTURE_ID]).strip(), str(row[AA_REG_ID]).strip())
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

def _compute_student_stats(reg_id, aa_rows):
    """
    Shared aggregation used by both the student-facing dashboard and the
    admin analytics cards, so the whole "Attendance & Assignments" sheet is
    only fetched once even when computing this for every student.

    Aggregate Performance is the average of whichever of {attendance rate,
    submission rate, score percentage} actually have data — a student with
    no graded assignments yet still gets a fair figure from the other two,
    rather than being dragged down by an empty category.
    """
    reg_id = (reg_id or "").strip()

    present = 0
    absent = 0
    issued = 0
    submitted = 0
    graded_count = 0
    total_score = 0.0
    total_mark = 0.0

    for row in aa_rows[1:]:
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

        mark_val = _to_number(row[AA_MARK])
        score_val = _to_number(row[AA_SCORE])
        if mark_val is not None and score_val is not None:
            graded_count += 1
            total_mark += mark_val
            total_score += score_val

    total_attendance = present + absent
    attendance_rate = round((present / total_attendance) * 100, 1) if total_attendance else 0.0
    submission_rate = round((submitted / issued) * 100, 1) if issued else 0.0
    score_percentage = round((total_score / total_mark) * 100, 1) if total_mark > 0 else 0.0

    components = []
    if total_attendance > 0:
        components.append(attendance_rate)
    if issued > 0:
        components.append(submission_rate)
    if graded_count > 0:
        components.append(score_percentage)
    aggregate_performance = round(sum(components) / len(components), 1) if components else 0.0

    return {
        "present": present,
        "absent": absent,
        "attendanceRate": attendance_rate,
        "issued": issued,
        "submitted": submitted,
        "submissionRate": submission_rate,
        "gradedCount": graded_count,
        "totalScore": total_score,
        "totalMark": total_mark,
        "scorePercentage": score_percentage,
        "aggregatePerformance": aggregate_performance,
        "hasData": total_attendance > 0 or issued > 0,
    }


def get_dashboard_summary(reg_id):
    aa_rows = _sheet_values(ASSIGNMENTS_SHEET_NAME)
    stats = _compute_student_stats(reg_id, aa_rows)
    return {"success": True, **stats}


# ---------------------------------------------------------------------------
# Admin — authentication
# ---------------------------------------------------------------------------

def authenticate_admin(username, password):
    username = (username or "").strip()
    password = (password or "").strip()
    if not username or not password:
        return {"success": False, "message": "Please enter both username and password."}

    rows = _sheet_values(USERS_SHEET_NAME)
    for row in rows[1:]:
        row = _padded(row, US_ROW_WIDTH)
        row_username = str(row[US_USERNAME]).strip()
        name = str(row[US_NAME]).strip()
        row_password = str(row[US_PASSWORD]).strip()
        phone = str(row[US_PHONE]).strip()

        if row_username == username and row_password == password:
            return {"success": True, "admin": {"username": row_username, "name": name, "phone": phone}}
    return {"success": False, "message": "Incorrect username or password."}


# ---------------------------------------------------------------------------
# Admin — Students tab
# ---------------------------------------------------------------------------

def get_all_students():
    """Full A:J roster for the admin Students tab."""
    rows = _sheet_values(STUDENTS_SHEET_NAME)
    students = []
    for row in rows[1:]:
        row = _padded(row, ST_ROW_WIDTH)
        reg_id = str(row[ST_ID]).strip()
        if not reg_id:
            continue
        students.append({
            "id": reg_id,
            "name": str(row[ST_NAME]).strip(),
            "gender": str(row[ST_GENDER]).strip(),
            "email": str(row[ST_EMAIL]).strip(),
            "phone": str(row[ST_PHONE]).strip(),
            "course": str(row[ST_COURSE]).strip(),
            "cohort": str(row[ST_COHORT]).strip(),
            "admissionDate": str(row[ST_ADMISSION_DATE]).strip(),
            "year": str(row[ST_YEAR]).strip(),
            "status": str(row[ST_STATUS]).strip() or "Not Set",
        })
    return {"success": True, "students": students}


def update_student_status(student_id, new_status):
    student_id = (student_id or "").strip()
    new_status = (new_status or "").strip()
    if not student_id or not new_status:
        return {"success": False, "message": "Missing student ID or status."}

    worksheet = _get_sheet(open_spreadsheet(), STUDENTS_SHEET_NAME)
    rows = worksheet.get_all_values()

    target_row = None
    for idx, row in enumerate(rows[1:], start=2):
        row = _padded(row, ST_ID + 1)
        if str(row[ST_ID]).strip() == student_id:
            target_row = idx
            break

    if target_row is None:
        return {"success": False, "message": f'No student found with ID "{student_id}".'}

    worksheet.update(range_name=f"J{target_row}", values=[[new_status]])
    return {"success": True, "message": f"Status updated to \"{new_status}\".", "id": student_id, "status": new_status}


# ---------------------------------------------------------------------------
# Admin — Course Content tab (unlocked, cohort-wide attendance)
# ---------------------------------------------------------------------------

def get_admin_course_content():
    """
    Same Timetable-driven list as the student view, but never locked by
    date (admins manage the schedule, they don't consume it), and with a
    cohort-wide Attendance figure (Present / Total marked, across every
    student) instead of one student's personal attendance.
    """
    spreadsheet = open_spreadsheet()
    timetable_ws = _get_sheet(spreadsheet, TIMETABLE_SHEET_NAME)
    timetable_rows = timetable_ws.get_all_values()
    if not timetable_rows:
        return {"success": True, "headers": [], "entries": []}

    headers = _padded(timetable_rows[0], TT_ROW_WIDTH)[:TT_ROW_WIDTH]

    tally = {}  # lecture_id -> [present, marked_total]
    try:
        aa_ws = _get_sheet(spreadsheet, ASSIGNMENTS_SHEET_NAME)
        for row in aa_ws.get_all_values()[1:]:
            row = _padded(row, AA_ATTENDANCE + 1)
            lecture_id = str(row[AA_LECTURE_ID]).strip()
            attendance = str(row[AA_ATTENDANCE]).strip().lower()
            if not lecture_id or attendance not in ("present", "absent"):
                continue
            present, total = tally.get(lecture_id, [0, 0])
            total += 1
            if attendance == "present":
                present += 1
            tally[lecture_id] = [present, total]
    except RuntimeError:
        pass

    entries = []
    for row in timetable_rows[1:]:
        row = _padded(row, TT_ROW_WIDTH)
        lecture_id = str(row[TT_ID]).strip()
        date_str = str(row[TT_DATE]).strip()
        module = str(row[TT_MODULE]).strip()
        session_focus = str(row[TT_FOCUS]).strip()
        resource = str(row[TT_RESOURCE]).strip()

        if not (lecture_id or date_str or module or session_focus):
            continue

        present, total = tally.get(lecture_id, [0, 0])
        attendance_display = f"{present}/{total} ({round(present / total * 100)}%)" if total else "—"

        details = {
            (headers[i] if i < len(headers) and headers[i] else f"Column {chr(65 + i)}"): row[i]
            for i in range(TT_ROW_WIDTH)
        }
        details["Cohort Attendance"] = attendance_display

        entries.append({
            "id": lecture_id,
            "date": date_str,
            "module": module,
            "sessionFocus": session_focus,
            "attendance": attendance_display,
            "locked": False,
            "learningResource": resource or None,
            "videoId": extract_youtube_id(resource),
            "details": details,
        })

    def sort_key(entry):
        parsed = parse_flexible_date(entry["date"])
        return (parsed is None, parsed or date.max)

    entries.sort(key=sort_key)
    return {"success": True, "headers": headers, "entries": entries}


# ---------------------------------------------------------------------------
# Admin — Overview analytics (total students, top/bottom performer)
# ---------------------------------------------------------------------------

def _build_performance_headline(student, positive):
    name = student["name"] or student["id"]
    aggregate = student["aggregatePerformance"]
    attendance_rate = student["attendanceRate"]
    submission_rate = student["submissionRate"]
    score_pct = student["scorePercentage"]

    if positive:
        headline = f"{name} leads the cohort with an aggregate performance of {aggregate}%."
        strengths = []
        if attendance_rate >= 80:
            strengths.append(f"{attendance_rate}% attendance")
        if submission_rate >= 80:
            strengths.append(f"{student['submitted']}/{student['issued']} assignments submitted")
        if student["gradedCount"] > 0 and score_pct >= 70:
            strengths.append(f"an average score of {score_pct}%")
        if strengths:
            headline += " Consistently strong on " + ", ".join(strengths) + "."
        return headline

    headline = f"{name} has the lowest aggregate performance in the cohort at {aggregate}%."
    concerns = []
    if attendance_rate < 60:
        concerns.append(f"attendance is only {attendance_rate}%")
    if submission_rate < 60:
        concerns.append(f"only {student['submitted']} of {student['issued']} assignments submitted")
    if student["gradedCount"] > 0 and score_pct < 60:
        concerns.append(f"an average score of {score_pct}%")
    if concerns:
        headline += " Main concerns: " + "; ".join(concerns) + "."
    return headline


def _build_consistency_remark(student):
    """
    One-line automated remark for the leaderboard, tiered by Consistency %
    (the same Aggregate Performance figure used elsewhere), naming whichever
    specific factor — attendance, submissions, or scores — stands out most,
    so two students at a similar percentage don't get an identical remark.
    """
    pct = student["aggregatePerformance"]
    attendance_rate = student["attendanceRate"]
    submission_rate = student["submissionRate"]
    score_pct = student["scorePercentage"]

    if pct >= 85:
        tier = "Outstanding consistency"
    elif pct >= 70:
        tier = "Strong, dependable performance"
    elif pct >= 50:
        tier = "Moderate consistency, room to improve"
    else:
        tier = "Low consistency, needs support"

    factors = [("attendance", attendance_rate), ("submissions", submission_rate), ("scores", score_pct)]

    if pct >= 70:
        best = max(factors, key=lambda f: f[1])
        return f"{tier} — particularly strong in {best[0]} ({best[1]}%)."
    else:
        worst = min(factors, key=lambda f: f[1])
        return f"{tier} — {worst[0]} is the main area to address ({worst[1]}%)."


def get_admin_dashboard_summary():
    student_rows = _sheet_values(STUDENTS_SHEET_NAME)
    aa_rows = _sheet_values(ASSIGNMENTS_SHEET_NAME)

    profiled = []
    for row in student_rows[1:]:
        row = _padded(row, ST_ROW_WIDTH)
        reg_id = str(row[ST_ID]).strip()
        if not reg_id:
            continue
        stats = _compute_student_stats(reg_id, aa_rows)
        profiled.append({
            "id": reg_id,
            "name": str(row[ST_NAME]).strip() or reg_id,
            "course": str(row[ST_COURSE]).strip(),
            "status": str(row[ST_STATUS]).strip() or "Not Set",
            **stats,
        })

    total_students = len(profiled)
    eligible = [s for s in profiled if s["hasData"]]

    top = None
    bottom = None
    if eligible:
        top = max(eligible, key=lambda s: s["aggregatePerformance"])
        if len(eligible) > 1:
            remaining = [s for s in eligible if s["id"] != top["id"]]
            bottom = min(remaining, key=lambda s: s["aggregatePerformance"]) if remaining else None

    top_card = None
    if top:
        top_card = {
            "name": top["name"],
            "course": top["course"],
            "aggregatePerformance": top["aggregatePerformance"],
            "headline": _build_performance_headline(top, positive=True),
        }

    bottom_card = None
    if bottom:
        bottom_card = {
            "name": bottom["name"],
            "course": bottom["course"],
            "aggregatePerformance": bottom["aggregatePerformance"],
            "headline": _build_performance_headline(bottom, positive=False),
        }

    # Leaderboard + bar-chart data: every student with recorded activity,
    # ranked by Consistency % (== Aggregate Performance), each with an
    # automated remark. Students with zero recorded activity are left out
    # of the ranking (nothing to compare yet) but counted separately.
    leaderboard = sorted(
        (
            {
                "id": s["id"],
                "name": s["name"],
                "course": s["course"],
                "consistency": s["aggregatePerformance"],
                "remark": _build_consistency_remark(s),
            }
            for s in eligible
        ),
        key=lambda s: s["consistency"],
        reverse=True,
    )

    return {
        "success": True,
        "totalStudents": total_students,
        "studentsWithData": len(eligible),
        "studentsWithoutData": total_students - len(eligible),
        "topPerformer": top_card,
        "lowestPerformer": bottom_card,
        "leaderboard": leaderboard,
    }


# ---------------------------------------------------------------------------
# Admin — Student Projects tab (capstone submissions)
# ---------------------------------------------------------------------------

def get_admin_capstone_projects():
    """
    Scans "Attendance & Assignments" Col F for the keyword "Capstone Project"
    (case-insensitive substring match, so "Capstone Project - Final" still
    matches), keeping only rows where Col H also reads "Issued". Returns
    Reg ID / Name / Category (D, E, F) for the summary row, plus the full
    detail set (D, E, F, H, I, J, K, M, N) for the "View Details" popup —
    N (the submitted file) is returned separately as a direct link rather
    than a plain text field, so the frontend can render it as an
    "Open File" button instead of a dead-looking string.
    """
    rows = _sheet_values(ASSIGNMENTS_SHEET_NAME)

    projects = []
    for row in rows[1:]:
        row = _padded(row, AA_ROW_WIDTH)
        category = str(row[AA_CATEGORY]).strip()
        status = str(row[AA_STATUS]).strip()

        if "capstone project" not in category.lower():
            continue
        if status.lower() != "issued":
            continue

        reg_id = str(row[AA_REG_ID]).strip()
        name = str(row[AA_NAME]).strip()
        content = str(row[AA_CONTENT]).strip()
        mark = str(row[AA_MARK]).strip()
        score = str(row[AA_SCORE]).strip()
        sub_date = str(row[AA_SUB_DATE]).strip()
        sub_file = str(row[AA_SUB_FILE]).strip()
        sub_status = str(row[AA_SUB_STATUS]).strip()

        projects.append({
            "regId": reg_id,
            "name": name,
            "category": category,
            "submitted": sub_status.lower() == "submitted",
            "details": {
                "Reg ID": reg_id,
                "Student Name": name,
                "Category": category,
                "Status": status,
                "Assignment Content": content,
                "Mark": mark or "—",
                "Score": score or "Not graded yet",
                "Submitted Date": sub_date or "—",
            },
            "fileUrl": sub_file or None,
        })

    return {"success": True, "projects": projects}
