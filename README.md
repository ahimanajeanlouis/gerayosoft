# GerayoSoft / GerayoRide — Python Flask version

This is the repaired Python/Flask version of the supplied PHP website. The frontend pages were restored from the original PHP pages so the landing page, client page, driver page, forms, colors, layout, sliders, maps, dashboards, and language text follow the original design instead of the simplified placeholder pages.

## 1. Run on Windows

Open Command Prompt or PowerShell inside this folder:

```text
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python app.py
```

Then open:

```text
http://127.0.0.1:5000
```

If Python is installed as `py`, use `py -m venv .venv` and `py app.py`.

## 2. Database

If `DATABASE_URL` is empty, the application creates a local SQLite database named `gerayoride.db` automatically.

For MySQL/MariaDB, set this in `.env`:

```text
DATABASE_URL=mysql+pymysql://USERNAME:PASSWORD@HOST/DATABASE
```

Then create the tables using `database/schema.sql` in your MySQL/MariaDB database.

## 3. Email verification and password reset

This version does not use SMTP or an external email service. Registration is auto-verified locally, and password reset uses an in-app token flow without sending emails.

## 4. Admin

Open `/admin`. The Python version uses the credentials from `.env`:

```text
ADMIN_EMAIL=admin@gerayoride.local
ADMIN_PASSWORD=change-this-password
```

Change these before deploying publicly.

## 5. Main routes

- `/` — homepage
- `/client` — client information page
- `/driver` — driver information page
- `/register` — registration
- `/login` — login
- `/client/dashboard` — client dashboard
- `/driver/dashboard` — approved driver dashboard
- `/driver/documents` — driver document verification
- `/admin` — administrator dashboard

The ride, driver-location, nearby-driver, verification, and password-reset endpoints are implemented in Flask as well.

## 6. Assets

The original `styles.css`, main image, uploaded documents, and language files are included. The two screenshot filenames referenced by the original public pages were not present as standalone root assets in the supplied website, so the repaired package includes safe fallback copies of the main image under those filenames rather than showing broken images.
"# gerayosoft" 


## GerayoFix vehicle repair service

GerayoFix is integrated into the existing Flask application. Signed-in users can search approved mechanics, submit workshop or roadside repair requests, and track request status. New users can select **Mechanic** when registering, then sign in to complete their mechanic profile application. Existing users can also apply using **GerayoFix → Join as mechanic**. Mechanic profiles remain hidden until an administrator approves them in `/admin`. Existing MySQL databases automatically gain the `Mechanic` account type at startup.

The GerayoFix experience is translated in English and Kinyarwanda and its pages adapt to mobile screens. The app creates `mechanic_profiles` and `repair_requests` automatically at startup for SQLite and MySQL. It does not reuse or change the existing ride `requests` table. Mechanic applications go to `/gerayofix/application-status`, which checks for administrator approval automatically; approved mechanics are sent to `/gerayofix/dashboard` to accept or reject incoming repair requests. Customers see the mechanic's decision in their repair requests page, which refreshes automatically. These are in-app notifications; this version does not send SMS or email. Other pages: `/gerayofix`, `/gerayofix/apply`, `/gerayofix/my-requests`. Configure `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `SECRET_KEY`, and `DATABASE_URL` using your normal environment settings before deployment.
