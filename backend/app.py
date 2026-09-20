"""
app.py
------
Flask REST API for the ENCA Digital Training Portal.
Same architecture and endpoints as the original Apps Script functions —
just served over HTTP so a static frontend (hosted on Netlify) can call it.

  POST /api/login        -> authenticate(username, password)
  POST /api/assignments  -> getAssignments(regId, studentName)
  POST /api/submit       -> submitAssignment(...)  (multipart/form-data)
  GET  /api/health       -> simple uptime check for Render
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


@app.post("/api/submit")
def submit():
    reg_id = request.form.get("regId")
    student_name = request.form.get("studentName")
    course = request.form.get("course")
    description = request.form.get("description", "")
    file_storage = request.files.get("file")

    try:
        result = svc.submit_assignment(reg_id, student_name, course, description, file_storage)
        return jsonify(result), (200 if result.get("success") else 400)
    except Exception as err:  # noqa: BLE001
        return jsonify({"success": False, "message": f"Submission failed: {err}"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
