"""
app.py
------
Flask REST API for the ENCA Digital Training Portal.

Student-facing:
  POST /api/login              -> authenticate(username, password)
  POST /api/assignments        -> getAssignments(regId, studentName)
  POST /api/course-content     -> getCourseContent(regId)
  POST /api/dashboard-summary  -> getDashboardSummary(regId)
  POST /api/submit             -> submitAssignment(...)  (multipart/form-data)

Admin-facing (separate login, backed by the "Users" sheet):
  POST /api/admin/login                    -> authenticateAdmin(username, password)
  POST /api/admin/students                 -> getAllStudents()
  POST /api/admin/students/update-status   -> updateStudentStatus(id, status)
  POST /api/admin/course-content           -> getAdminCourseContent()
  POST /api/admin/dashboard-summary        -> getAdminDashboardSummary()

  GET  /api/health             -> simple uptime check for Render
"""

import os

from flask import Flask, jsonify, request
from flask_cors import CORS

import sheets_service as svc

app = Flask(__name__)
# CORS stays on as a defensive fallback for local testing / direct calls;
# in production the Netlify redirect proxy makes requests same-origin anyway.
CORS(app)


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/api/login")
def login():
    payload = request.get_json(silent=True) or {}
    try:
        result = svc.authenticate(payload.get("username"), payload.get("password"))
        return jsonify(result)
    except Exception as err:  # noqa: BLE001 - surface a clean message to the client
        return jsonify({"success": False, "message": f"Login error: {err}"}), 500


@app.post("/api/assignments")
def assignments():
    payload = request.get_json(silent=True) or {}
    try:
        result = svc.get_assignments(payload.get("regId"), payload.get("studentName"))
        return jsonify(result)
    except Exception as err:  # noqa: BLE001
        return jsonify({"success": False, "message": f"Could not load assignments: {err}"}), 500


@app.post("/api/course-content")
def course_content():
    payload = request.get_json(silent=True) or {}
    try:
        result = svc.get_course_content(payload.get("regId"))
        return jsonify(result)
    except Exception as err:  # noqa: BLE001
        return jsonify({"success": False, "message": f"Could not load course content: {err}"}), 500


@app.post("/api/dashboard-summary")
def dashboard_summary():
    payload = request.get_json(silent=True) or {}
    try:
        result = svc.get_dashboard_summary(payload.get("regId"))
        return jsonify(result)
    except Exception as err:  # noqa: BLE001
        return jsonify({"success": False, "message": f"Could not load dashboard summary: {err}"}), 500


@app.post("/api/submit")
def submit():
    lecture_id = request.form.get("lectureId")
    reg_id = request.form.get("regId")
    student_name = request.form.get("studentName")
    course = request.form.get("course")
    description = request.form.get("description", "")
    file_storage = request.files.get("file")

    try:
        result = svc.submit_assignment(lecture_id, reg_id, student_name, course, description, file_storage)
        return jsonify(result), (200 if result.get("success") else 400)
    except Exception as err:  # noqa: BLE001
        return jsonify({"success": False, "message": f"Submission failed: {err}"}), 500


@app.post("/api/admin/login")
def admin_login():
    payload = request.get_json(silent=True) or {}
    try:
        result = svc.authenticate_admin(payload.get("username"), payload.get("password"))
        return jsonify(result)
    except Exception as err:  # noqa: BLE001
        return jsonify({"success": False, "message": f"Login error: {err}"}), 500


@app.post("/api/admin/students")
def admin_students():
    try:
        result = svc.get_all_students()
        return jsonify(result)
    except Exception as err:  # noqa: BLE001
        return jsonify({"success": False, "message": f"Could not load students: {err}"}), 500


@app.post("/api/admin/students/update-status")
def admin_update_student_status():
    payload = request.get_json(silent=True) or {}
    try:
        result = svc.update_student_status(payload.get("id"), payload.get("status"))
        return jsonify(result), (200 if result.get("success") else 400)
    except Exception as err:  # noqa: BLE001
        return jsonify({"success": False, "message": f"Could not update status: {err}"}), 500


@app.post("/api/admin/course-content")
def admin_course_content():
    try:
        result = svc.get_admin_course_content()
        return jsonify(result)
    except Exception as err:  # noqa: BLE001
        return jsonify({"success": False, "message": f"Could not load course content: {err}"}), 500


@app.post("/api/admin/projects")
def admin_projects():
    try:
        result = svc.get_admin_capstone_projects()
        return jsonify(result)
    except Exception as err:  # noqa: BLE001
        return jsonify({"success": False, "message": f"Could not load student projects: {err}"}), 500


@app.post("/api/admin/dashboard-summary")
def admin_dashboard_summary():
    try:
        result = svc.get_admin_dashboard_summary()
        return jsonify(result)
    except Exception as err:  # noqa: BLE001
        return jsonify({"success": False, "message": f"Could not load dashboard summary: {err}"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
