"""Flask-app voor lokale ontwikkeling en Vercel."""
from __future__ import annotations

import time
from pathlib import Path

from flask import Flask, Response, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from kpt import core, storage

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = storage.MAX_SESSION_BYTES

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error):
        return jsonify({
            "error": "Upload is te groot. Comprimeer het bestand of gebruik een kleiner rapport."
        }), 413

    @app.before_request
    def prepare_session():
        # Statische CDN-bestanden raken Flask niet; API en overige routes wel.
        if not request.path.startswith("/api/"):
            return
        storage.purge_expired_sessions()
        session_id = request.cookies.get("kpt_session") or ""
        light_get = request.method in {"GET", "HEAD", "OPTIONS"} and request.path.rstrip("/") in {
            "/api/auth/status",
            "/api/health",
            "/api/files",
            "/api/planning",
            "/api/weeks",
            "/api/pdf-customers",
            "/api/invoice-files",
        }
        session = storage.load_session(session_id, load_files=not light_get)
        new_cookie = False
        if session is None:
            session_id = storage.new_session_id()
            session = storage.empty_session()
            new_cookie = True
        else:
            session["updated"] = time.time()
        g.session_id = session_id
        g.session = session
        g.new_session_cookie = new_cookie
        g.session_dirty = request.method not in {"GET", "HEAD", "OPTIONS"}

    @app.errorhandler(Exception)
    def api_error(exc):
        if isinstance(exc, HTTPException):
            return exc
        if not request.path.startswith("/api/"):
            raise exc
        message = str(exc) or "Onverwachte serverfout."
        return jsonify({"error": message}), 500

    @app.after_request
    def finish_session(response: Response):
        if not hasattr(g, "session_id"):
            return response
        try:
            if getattr(g, "session_dirty", True) or getattr(g, "new_session_cookie", False):
                storage.save_session(g.session_id, g.session)
        except Exception as exc:
            if request.path.startswith("/api/") and response.status_code < 400:
                return jsonify({"error": f"Sessie opslaan mislukt: {exc}"}), 500
        if getattr(g, "new_session_cookie", False):
            secure = request.is_secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https"
            response.set_cookie(
                "kpt_session",
                g.session_id,
                max_age=storage.SESSION_TTL,
                httponly=True,
                samesite="Strict",
                secure=secure,
                path="/",
            )
        response.headers["Cache-Control"] = "no-store"
        return response

    def json_error(message: str, status: int):
        return jsonify({"error": message}), status

    def require_auth(admin: bool = False):
        username = g.session.get("user") or ""
        users = storage.load_users()
        record = users.get(username)
        if not record:
            g.session["user"] = ""
            g.session["admin"] = False
            return json_error("Log eerst in.", 401)
        g.session["admin"] = bool(record.get("admin"))
        if admin and not g.session["admin"]:
            return json_error("Alleen een beheerder mag dit doen.", 403)
        return None

    def uploaded_files() -> dict:
        return g.session.get("files") or {}

    def preview_cache() -> dict:
        return (g.session.get("state") or {}).get("preview") or {}

    def file_info() -> dict:
        return g.session.get("_file_info") or {}

    def refresh_preview() -> dict:
        cache = core.build_preview_cache(uploaded_files())
        g.session.setdefault("state", {})["preview"] = cache
        return cache

    def attachment(data: bytes, filename: str, content_type: str):
        response = Response(data, mimetype=content_type)
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "storage": storage.storage_mode(), "vercel": storage.is_vercel()})

    @app.get("/api/auth/status")
    def auth_status():
        try:
            users = storage.load_users()
        except Exception as exc:
            return json_error(str(exc), 503)
        username = g.session.get("user") or ""
        record = users.get(username)
        if username and not record:
            g.session["user"] = ""
            g.session["admin"] = False
        return jsonify(
            {
                "setupRequired": not bool(users),
                "authenticated": bool(record),
                "username": username if record else "",
                "admin": bool(record and record.get("admin")),
                "storage": storage.storage_mode(),
            }
        )

    @app.post("/api/auth/setup")
    def auth_setup():
        try:
            payload = request.get_json(silent=True) or {}
            username, password = core.validate_credentials(payload.get("username", ""), payload.get("password", ""))
            users = storage.load_users()
            if users:
                return json_error("De beheerder is al ingesteld.", 409)
            users[username] = core.password_record(password, admin=True)
            storage.save_users(users)
            g.session["user"] = username
            g.session["admin"] = True
            return jsonify({"ok": True, "username": username, "admin": True})
        except ValueError as exc:
            return json_error(str(exc), 400)
        except RuntimeError as exc:
            return json_error(str(exc), 503)
        except OSError:
            return json_error("Het beheeraccount kon niet veilig worden opgeslagen.", 500)

    @app.post("/api/auth/login")
    def auth_login():
        payload = request.get_json(silent=True) or {}
        username = (payload.get("username") or "").strip().lower()
        password = payload.get("password") or ""
        key = f"{request.headers.get('X-Forwarded-For', request.remote_addr)}|{username}"
        if storage.login_is_blocked(key):
            return json_error("Te veel mislukte pogingen. Probeer het over 10 minuten opnieuw.", 429)
        try:
            record = storage.load_users().get(username)
        except RuntimeError as exc:
            return json_error(str(exc), 503)
        if not record or not core.verify_password(password, record):
            storage.record_login_failure(key)
            return json_error("Onjuiste gebruikersnaam of wachtwoord.", 401)
        storage.clear_login_failures(key)
        g.session["user"] = username
        g.session["admin"] = bool(record.get("admin"))
        return jsonify({"ok": True, "username": username, "admin": g.session["admin"]})

    @app.post("/api/auth/logout")
    def auth_logout():
        g.session["files"].clear()
        g.session["state"] = {}
        g.session["user"] = ""
        g.session["admin"] = False
        return jsonify({"ok": True})

    @app.get("/api/users")
    def list_users():
        denied = require_auth(admin=True)
        if denied:
            return denied
        return jsonify({"users": core.public_users(storage.load_users())})

    @app.post("/api/users")
    def add_user():
        denied = require_auth(admin=True)
        if denied:
            return denied
        try:
            payload = request.get_json(silent=True) or {}
            username, password = core.validate_credentials(payload.get("username", ""), payload.get("password", ""))
            users = storage.load_users()
            if username in users:
                return json_error("Deze gebruikersnaam bestaat al.", 409)
            users[username] = core.password_record(password, admin=bool(payload.get("admin")))
            storage.save_users(users)
            return jsonify({"ok": True, "user": {"username": username, "admin": bool(payload.get("admin"))}})
        except ValueError as exc:
            return json_error(str(exc), 400)
        except RuntimeError as exc:
            return json_error(str(exc), 503)

    @app.delete("/api/users")
    def delete_user():
        denied = require_auth(admin=True)
        if denied:
            return denied
        username = (request.args.get("username") or "").strip().lower()
        if not username:
            return json_error("Gebruikersnaam ontbreekt.", 400)
        if username == g.session.get("user"):
            return json_error("Je kunt je eigen account niet verwijderen.", 400)
        users = storage.load_users()
        record = users.get(username)
        if not record:
            return json_error("Gebruiker niet gevonden.", 404)
        if record.get("admin") and sum(bool(item.get("admin")) for item in users.values()) <= 1:
            return json_error("De laatste beheerder kan niet worden verwijderd.", 400)
        del users[username]
        storage.save_users(users)
        storage.clear_user_sessions(username)
        return jsonify({"ok": True})

    @app.post("/api/clear")
    def clear_session():
        denied = require_auth()
        if denied:
            return denied
        g.session["files"].clear()
        g.session["state"] = {}
        g.session["_file_info"] = {}
        g.session["_files_loaded"] = True
        return jsonify({"ok": True})

    @app.get("/api/files")
    def files():
        denied = require_auth()
        if denied:
            return denied
        cache = preview_cache()
        if cache.get("reports") is not None:
            return jsonify({"files": cache["reports"]})
        info = file_info()
        files = [
            {"name": name, "size": int(item.get("size") or 0), "mtime": int(float(item.get("mtime") or 0))}
            for name, item in info.items()
            if item.get("kind") == "report"
        ]
        files.sort(key=lambda item: item["mtime"], reverse=True)
        return jsonify({"files": files})

    @app.get("/api/planning")
    def planning():
        denied = require_auth()
        if denied:
            return denied
        cache = preview_cache()
        if cache.get("planningFiles") is not None:
            return jsonify({"files": cache["planningFiles"], "rows": []})
        names = [name for name, item in file_info().items() if item.get("kind") == "planning"]
        return jsonify({"files": names, "rows": []})

    @app.get("/api/invoice-files")
    def invoice_files():
        denied = require_auth()
        if denied:
            return denied
        return jsonify({"files": core.list_invoice_pdf_info(uploaded_files())})

    @app.get("/api/weeks")
    def weeks():
        denied = require_auth()
        if denied:
            return denied
        try:
            cache = preview_cache()
            if cache.get("weeks") is not None:
                return jsonify({"weeks": cache["weeks"]})
            loaded = storage.load_session(g.session_id, load_files=True)
            if loaded:
                g.session = loaded
            g.session_dirty = True
            return jsonify({"weeks": refresh_preview()["weeks"]})
        except Exception as exc:
            return json_error(str(exc), 400)

    @app.get("/api/invoices")
    def invoices():
        denied = require_auth()
        if denied:
            return denied
        name = request.args.get("file") or ""
        try:
            return jsonify({"file": name, "invoices": core.list_invoices(name, uploaded_files())})
        except Exception as exc:
            return json_error(str(exc), 400)

    @app.get("/api/pdf-customers")
    def pdf_customers():
        denied = require_auth()
        if denied:
            return denied
        week = request.args.get("week") or ""
        try:
            cache = preview_cache()
            if not week:
                weeks = cache.get("weeks") or []
                week = weeks[0]["id"] if weeks else ""
            if not week:
                return json_error("Upload eerst een weekplanning (rittenboek).", 400)
            payload = (cache.get("pdf") or {}).get(week)
            if payload is None:
                loaded = storage.load_session(g.session_id, load_files=True)
                if loaded:
                    g.session = loaded
                g.session_dirty = True
                payload = (refresh_preview().get("pdf") or {}).get(week)
            if payload is None:
                return json_error("Week niet gevonden in het rittenboek", 400)
            return jsonify(payload)
        except Exception as exc:
            return json_error(str(exc), 400)

    @app.get("/api/week-check")
    def week_check():
        denied = require_auth()
        if denied:
            return denied
        try:
            return jsonify(core.check_reports_against_planning(uploaded_files()))
        except Exception as exc:
            return json_error(str(exc), 400)

    @app.get("/api/invoice-check")
    def invoice_check():
        denied = require_auth()
        if denied:
            return denied
        reports = core.list_report_files(uploaded_files())
        name = request.args.get("file") or (reports[0]["name"] if reports else "")
        invoice = request.args.get("invoice") or ""
        try:
            return jsonify(core.check_invoice_pdf(name, invoice, uploaded_files()))
        except Exception as exc:
            return json_error(str(exc), 400)

    @app.get("/api/customer-pdf")
    def customer_pdf():
        denied = require_auth()
        if denied:
            return denied
        week = request.args.get("week") or ""
        customer = request.args.get("customer") or ""
        country = request.args.get("country") or ""
        try:
            import maak_pdf_per_klant as pdf

            if not week:
                weeks = core.list_weeks(uploaded_files())
                week = weeks[0]["id"] if weeks else ""
            data = core.build_customer_pdf(week, customer, country, uploaded_files())
            filename = f"Toloverzicht - {pdf.safe_name(week)} - {pdf.safe_name(customer)}.pdf"
            return attachment(data, filename, "application/pdf")
        except Exception as exc:
            return json_error(str(exc), 400)

    @app.get("/api/customer-pdfs.zip")
    def customer_zip():
        denied = require_auth()
        if denied:
            return denied
        week = request.args.get("week") or ""
        try:
            data = core.build_customer_zip(week or None, uploaded_files())
            suffix = week or "alle_weken"
            return attachment(data, f"Toloverzichten_{suffix}.zip", "application/zip")
        except Exception as exc:
            return json_error(str(exc), 400)

    @app.post("/api/upload")
    def upload():
        denied = require_auth()
        if denied:
            return denied
        try:
            if "file" not in request.files:
                return json_error("Geen bestand ontvangen", 400)
            upload_file = request.files["file"]
            name = core.safe_filename(upload_file.filename or "upload.xls")
            data = upload_file.read()
            if (request.form.get("compressed") or "").lower() == "gzip":
                import gzip

                try:
                    data = gzip.decompress(data)
                except OSError as exc:
                    return json_error(f"Gzip-decompressie mislukt: {exc}", 400)
            if Path(name).suffix.lower() not in core.ALLOWED_UPLOAD:
                return json_error("Alleen .csv, .xls, .xlsx of .pdf", 400)
            files = uploaded_files()
            path = Path(name)
            kind = core.detect_kind(path, data)
            expected = request.args.get("expected") or ""
            if expected in {"report", "planning", "invoice"} and kind != expected:
                labels = {"report": "tolrapport", "planning": "weekplanning", "invoice": "factuur-PDF"}
                return json_error(f"{name} is geen geldig {labels[expected]}.", 400)
            if kind == "unknown":
                return json_error(f"{name} wordt niet herkend als tolrapport of weekplanning.", 400)
            # Alleen de bestanden van deze upload: geen oude CSV's of extra rapporten meenemen.
            if (request.args.get("replace") or "") == "1" and expected in {"report", "planning"}:
                files = {
                    key: item
                    for key, item in files.items()
                    if item.get("kind") not in {expected, "invoice"}
                }
            current_size = sum(len(item["data"]) for item in files.values())
            previous_size = len(files.get(name, {}).get("data", b""))
            if current_size - previous_size + len(data) > storage.MAX_SESSION_BYTES:
                return json_error("De tijdelijke sessie mag maximaal 550 MB bevatten.", 413)
            if len(data) > storage.MAX_UPLOAD_BYTES:
                return json_error("Dit bestand is groter dan 500 MB en kan niet worden verwerkt.", 413)
            if storage.is_vercel() and expected == "report" and len(data) > storage.MAX_VERCEL_FILE_BYTES:
                return json_error(
                    "Dit rapport is te groot voor de website. Upload eerst het rittenboek; "
                    "de browser stuurt daarna alleen de regels van die week.",
                    413,
                )
            files[name] = {"data": data, "mtime": time.time(), "kind": kind}
            g.session["files"] = files
            g.session["_files_loaded"] = True
            try:
                refresh_preview()
            except Exception:
                (g.session.get("state") or {}).pop("preview", None)
            return jsonify({"ok": True, "name": name, "kind": kind})
        except Exception as exc:
            return json_error(str(exc), 400)

    @app.get("/")
    def index():
        return send_from_directory(PUBLIC, "index.html")

    @app.get("/<path:filename>")
    def static_files(filename: str):
        if filename.startswith("api/"):
            return jsonify({"error": "Onbekend API-eindpunt."}), 404
        target = PUBLIC / filename
        if target.is_file():
            return send_from_directory(PUBLIC, filename)
        return send_from_directory(PUBLIC, "index.html")

    return app


app = create_app()
