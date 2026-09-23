# ENCA Digital Training Portal — Netlify + Python + GitHub Edition

This replaces the Google Apps Script web app with the same architecture and
behaviour, running as:

```
Browser  --->  Netlify (static frontend)  --->  Render (Python/Flask API)  --->  Google Sheets + Drive
                                                                                  (your own Google account)
```

Why this fixes the mobile-blocking problem: Apps Script web apps are served
from `script.google.com`, which redirects through a sandboxed
`googleusercontent.com` domain before rendering. Some mobile browsers,
in-app browsers, and network filters block or mishandle that redirect chain.
Netlify serves your frontend from a normal custom domain with no redirect
sandboxing, so it behaves like any other website.

## Why a refresh token instead of a "service account"

The usual way a Python backend talks to Google APIs is a **service
account**, but service accounts have **zero personal Drive storage**.
Uploading a file "owned" by a service account into a regular Gmail account's
Drive fails immediately — the standard fix (Shared Drives) is a paid Google
Workspace feature you don't have on a personal Gmail account.

Instead, this backend authenticates as **your own Google account** (the one
that already owns the spreadsheet) via a stored OAuth refresh token. This
exactly matches how the original Apps Script behaved under
"Execute as: Me" — every uploaded file is owned by your account, using your
normal 15 GB Drive quota, with nothing paid or organization-only required.

## Repository layout

```
enca-portal/
├── backend/
│   ├── app.py                 Flask API (routes only)
│   ├── sheets_service.py      All Sheets/Drive logic (Python port of Code.gs)
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── style.css
│   ├── script.js
│   └── netlify.toml           Publish config + API proxy redirect
├── setup/
│   ├── get_refresh_token.py   Run ONCE, locally, to authorize your account
│   └── requirements.txt
├── render.yaml                 Render Blueprint (backend deployment)
└── .gitignore
```

## Current sheet contract

**Students**: A = Reg ID (also the password), B = Name, C = Gender,
D = Username, F = Course.

**Attendance & Assignments**: A = Timetable ID (links to Timetable's own
Col A), B = Course, D = Reg ID, E = Student Name, G = Attendance
(`Present` / `Absent`), H = Status (must read exactly `Issued`),
I = Assignment content, J = Submission Status (`Submitted` once uploaded),
K = Submission Date, L = Submission File (Drive link).

**Timetable**: A = Timetable ID, B = Date (e.g. `16-Sept-2026`),
D = Module, E = Session Focus, G = Learning Resource (YouTube link).
Columns C, F, H, I are also read and shown in the Course Content detail
popup, labelled using whatever text is in that sheet's own header row (row 1)
— so the backend never has to guess what those columns mean.

There is no longer a separate "Assignments Submissions" sheet — submissions
are written straight onto the matching row of "Attendance & Assignments"
(see Feature 3 below).

## What's new: Course Content, dashboard stats, and revised submissions

**1. Course Content tab.** A third dashboard tab pulls the full Timetable
(cols A–I) and shows Date / Module / Session Focus / Attendance / Learning
Resource / Action as a responsive list (a real 6-column table on wider
screens, stacked labelled cards on phones).

- **Locking**: any row whose Date (Col B) is in the future is locked —
  its "Action" button reads "Locked" and is disabled, and the backend
  never even sends that row's Learning Resource link or full detail data to
  the browser (not just a hidden button — the data itself isn't exposed).
- **Attendance per row** comes from "Attendance & Assignments" Col G,
  matched by Timetable Col A == Attendance & Assignments Col A **for this
  specific student's Reg ID**. *(Assumption: since "Attendance & Assignments"
  has one row per student per session, matching on Timetable ID alone would
  be ambiguous across students — the Reg ID match is added to keep this
  correct per-student. Flag it if your sheet is structured differently.)*
- **"View" button** opens a popup listing every pulled column (A–I) as
  vertically stacked label/value pairs, using the Timetable sheet's own
  header row for labels, plus the matched Attendance value. If Col G's
  link is a recognisable YouTube URL, the popup also embeds it as a
  playable video (not just a link).

**2. Dashboard stat cards.** Two new cards on the main Dashboard tab:
- **Attendance** — Present count, Absent count, and attendance rate, all
  computed from "Attendance & Assignments" Col G for this student only.
- **Performance** — assignment submission rate (issued vs. submitted, from
  Cols H/J) averaged with the attendance rate into one overall score.

**3. Submissions now write directly into "Attendance & Assignments".**
Instead of logging to a separate sheet, `submit_assignment()` finds the row
where **Reg ID (Col D) AND Course (Col B) AND Status = "Issued"** all match,
and writes `Submitted` / the timestamp / the Drive file link into that row's
J/K/L columns. *(Assumption: Student ID alone isn't enough to pick the right
row if a student has more than one issued assignment at once, so Course is
used as the disambiguator — this is already the data your frontend sends
with every submission, so nothing changes on your end.)*

Uploading to Drive and mirroring folder access to your other sheet
collaborators (Feature from the previous update) is unchanged.

**Bug fix**: assignments previously showed "Submitted" too broadly. Now
`get_assignments()` reads Col J on that exact row only, and only ever
reports `submitted: true` when J reads exactly `Submitted` — a freshly
issued assignment always starts as open, and only the one specific
assignment that was actually submitted shows as submitted.

---

## Step 1 — Google Cloud: enable APIs & create OAuth credentials

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and
   create a new project (or reuse one) — e.g. "ENCA Digital Training".
2. **APIs & Services → Library** — enable:
   - **Google Sheets API**
   - **Google Drive API**
3. **APIs & Services → OAuth consent screen**:
   - User type: **External**
   - Fill in the required app name/support email fields
   - Under **Test users**, add the Gmail address that owns your training
     spreadsheet (e.g. `assazaims@gmail.com`)
   - Leave it in **Testing** status — you don't need to publish it
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - Application type: **Desktop app**
   - Name it anything, e.g. "ENCA Portal Setup"
   - Click **Create**, then **Download JSON**
   - Rename the downloaded file to `client_secret.json` and place it in the
     `setup/` folder (it's already git-ignored — never commit it)

## Step 2 — Get your Spreadsheet ID

Open your training spreadsheet in a browser. The ID is the long string in
the URL between `/d/` and `/edit`:

```
https://docs.google.com/spreadsheets/d/  1AbC-Def6HijK...  /edit
                                          ^^^^^^^^^^^^^^^^ this part
```

Keep this handy — it becomes the `SPREADSHEET_ID` environment variable.

## Step 3 — Generate your refresh token (run once, locally)

On your own computer, with Python installed:

```bash
cd enca-portal/setup
pip install -r requirements.txt
python get_refresh_token.py
```

A browser window opens — sign in with the Gmail account from Step 1 and
approve access. The script then prints three values:

```
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REFRESH_TOKEN=...
```

Copy these somewhere safe — you'll paste them into Render in Step 5.
`client_secret.json` and this script are never deployed anywhere; they're
only for this one-time local step.

## Step 4 — Push the code to GitHub

```bash
cd enca-portal
git init
git add .
git commit -m "Initial commit: ENCA portal (Netlify + Flask + Google Sheets)"
git branch -M main
git remote add origin https://github.com/<your-username>/enca-portal.git
git push -u origin main
```

(Create the empty repository on GitHub first if you haven't already —
no README/license needed there, this local repo already has one.)

## Step 5 — Deploy the backend on Render

1. Go to [render.com](https://render.com) → sign in → **New → Blueprint**.
2. Connect your GitHub account and select the `enca-portal` repository.
   Render will detect `render.yaml` automatically.
3. When prompted for the environment variables it left blank
   (`sync: false` in the blueprint), enter:
   - `GOOGLE_CLIENT_ID`
   - `GOOGLE_CLIENT_SECRET`
   - `GOOGLE_REFRESH_TOKEN`
   - `SPREADSHEET_ID`
4. Click **Apply** / **Create**. Render will install `backend/requirements.txt`
   and start the service with `gunicorn app:app`.
5. Once deployed, copy the service URL, e.g.
   `https://enca-portal-backend.onrender.com`.
6. Visit `https://enca-portal-backend.onrender.com/api/health` — you should
   see `{"status": "ok"}`.

*(No `render.yaml`? You can set this up manually instead: New → Web Service
→ connect the repo → Root Directory: `backend` → Build Command:
`pip install -r requirements.txt` → Start Command: `gunicorn app:app` → add
the same four environment variables.)*

## Step 6 — Deploy the frontend on Netlify

1. Update `frontend/netlify.toml` — replace
   `https://YOUR-RENDER-BACKEND-URL.onrender.com` with your actual Render
   URL from Step 5, then commit and push:
   ```bash
   git add frontend/netlify.toml
   git commit -m "Point Netlify proxy at Render backend"
   git push
   ```
2. Go to [netlify.com](https://netlify.com) → **Add new site → Import an
   existing project** → connect GitHub → select `enca-portal`.
3. Set:
   - **Base directory**: `frontend`
   - **Publish directory**: `frontend` (or leave blank if base directory is
     already set to `frontend` — Netlify treats it as the root either way)
   - **Build command**: leave blank (this is a static site, nothing to build)
4. Click **Deploy site**. Netlify gives you a URL like
   `https://your-site-name.netlify.app` — that's the link you share with
   students.

Every future `git push` to `main` automatically redeploys both Netlify and
Render — no manual re-upload needed for either platform.

## Step 7 — Test it end to end

1. Open the Netlify URL on both a desktop browser and a phone.
2. Log in with a real student's username/Reg ID.
3. Confirm the dashboard shows the right Reg ID, Name, Gender, Course.
4. Open an issued assignment, add a description, attach a file, submit.
5. Verify:
   - A new row appeared in **Assignments Submissions**.
   - The file appears in **Drive → ENCA Digital Training on Data Analytics
     and Capacity Building → Data → Assignment Submissions**.
   - Anyone who already had Viewer/Commenter/Editor access to the
     spreadsheet now has that same access on the **Assignment Submissions**
     folder (check via the folder's Share dialog).

## Ongoing notes

- **Adding instructors later**: just share the spreadsheet with them as
  usual. The next submission automatically syncs their same access level
  onto the folder — no separate step needed.
- **If the refresh token stops working** (e.g. you revoked access at
  [myaccount.google.com/permissions](https://myaccount.google.com/permissions)),
  re-run `setup/get_refresh_token.py` and update `GOOGLE_REFRESH_TOKEN` on
  Render.
- **Custom domain**: both Netlify and Render support attaching your own
  domain for free (Netlify: Site settings → Domain management; Render:
  Settings → Custom Domains) if you'd like a branded URL instead of
  `*.netlify.app`.
- **Render free tier** spins down after inactivity and takes a few seconds
  to wake on the next request — the first login after idle time may feel
  slightly slow. Upgrading to a paid Render instance removes that if it
  becomes an issue.
