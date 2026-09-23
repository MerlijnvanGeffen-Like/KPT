"""Kernlogica: rapporten, planning, facturen en PDF-export."""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import re
import secrets
import zipfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import unquote

REPORT_EXTENSIONS = {".xls", ".xlsx", ".csv"}
ALLOWED_UPLOAD = REPORT_EXTENSIONS | {".pdf"}
PASSWORD_MIN_LENGTH = 4

CUSTOMER_ALIASES = {
    "powercast": "PowerCast",
    "huism": "Huism.",
    "huism.": "Huism.",
}


def validate_credentials(username: str, password: str) -> tuple[str, str]:
    username = (username or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{3,40}", username):
        raise ValueError("Gebruikersnaam: 3-40 letters, cijfers, punten, streepjes of underscores.")
    if len(password or "") < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Het wachtwoord moet minimaal {PASSWORD_MIN_LENGTH} tekens bevatten.")
    return username, password


def password_record(password: str, admin: bool = False) -> dict:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return {
        "salt": base64.b64encode(salt).decode("ascii"),
        "hash": base64.b64encode(digest).decode("ascii"),
        "admin": bool(admin),
        "created": datetime.now().isoformat(timespec="seconds"),
    }


def verify_password(password: str, record: dict) -> bool:
    try:
        salt = base64.b64decode(record["salt"])
        expected = base64.b64decode(record["hash"])
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, expected)
    except (KeyError, ValueError, TypeError):
        return False


def public_users(users: dict) -> list[dict]:
    return [
        {"username": username, "admin": bool(record.get("admin")), "created": record.get("created") or ""}
        for username, record in sorted(users.items())
    ]


def money(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value or "").strip().replace(" ", "").replace("'", "")
    if not raw:
        return 0.0
    raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return 0.0


def normalize_plate(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def canon_customer(name: str) -> str:
    raw = " ".join((name or "").split())
    if not raw:
        return ""
    return CUSTOMER_ALIASES.get(raw.lower(), raw)


def safe_filename(name: str) -> str:
    name = Path(unquote(name or "")).name
    name = re.sub(r"[^\w.\- ()]", "_", name, flags=re.UNICODE)
    return name.strip() or "upload.xls"


def is_week_name(name: str) -> bool:
    return name.lower().startswith("week")


def cell_text(value, datemode=None) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, float) and datemode is not None:
        try:
            import xlrd

            if xlrd.xldate.xldate_as_tuple(value, datemode)[0] > 1900:
                dt = xlrd.xldate_as_datetime(value, datemode)
                if dt.hour or dt.minute or dt.second:
                    return dt.strftime("%H:%M:%S") if dt.year < 1901 else dt.strftime("%d/%m/%Y")
                return dt.strftime("%d/%m/%Y")
        except Exception:
            pass
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).strip()


def iter_excel_sheets(path: Path, data: bytes | None = None):
    suffix = path.suffix.lower()
    if suffix == ".xls":
        import xlrd

        workbook = xlrd.open_workbook(file_contents=data) if data is not None else xlrd.open_workbook(str(path))
        for sheet in workbook.sheets():
            rows = []
            for r in range(sheet.nrows):
                line = []
                for c in range(sheet.ncols):
                    cell = sheet.cell(r, c)
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        dt = xlrd.xldate_as_datetime(cell.value, workbook.datemode)
                        line.append(
                            dt.strftime("%H:%M:%S")
                            if dt.hour or dt.minute or dt.second
                            else dt.strftime("%d/%m/%Y")
                        )
                    else:
                        line.append(cell_text(cell.value))
                rows.append(line)
            yield sheet.name, rows
        return
    if suffix == ".xlsx":
        import openpyxl

        source = io.BytesIO(data) if data is not None else path
        workbook = openpyxl.load_workbook(source, data_only=True, read_only=True)
        for sheet in workbook.worksheets:
            rows = [[cell_text(v) for v in row] for row in sheet.iter_rows(values_only=True)]
            yield sheet.title, rows
        workbook.close()


def list_report_files(uploaded: dict) -> list[dict]:
    files = []
    for name, item in uploaded.items():
        path = Path(name)
        if path.suffix.lower() not in REPORT_EXTENSIONS or item.get("kind") != "report":
            continue
        if path.stem.lower().startswith(("tolverdeling", "tol_per_klant")):
            continue
        files.append({"name": name, "size": len(item["data"]), "mtime": int(item["mtime"])})
    files.sort(key=lambda file: file["mtime"], reverse=True)
    return files


def pick(mapping: dict, *keys) -> str:
    lower = {str(k).strip().lower(): v for k, v in mapping.items() if k}
    for key in keys:
        val = mapping.get(key)
        if val not in (None, ""):
            return str(val).strip().strip("'")
        val = lower.get(key.lower())
        if val not in (None, ""):
            return str(val).strip().strip("'")
    return ""


def report_row_from_map(row: dict) -> dict | None:
    plate_raw = pick(row, "Truck PlateNr", "Card PlateNr", "File PlateNr", "Kenteken")
    plate = normalize_plate(plate_raw)
    if not plate:
        return None
    return {
        "id": pick(row, "Trx ID", "ID"),
        "date": pick(row, "Trx Date", "Datum"),
        "time": pick(row, "Trx Time", "Tijd"),
        "plate": plate,
        "plateRaw": plate_raw,
        "km": round(money(pick(row, "Distance KM", "KM")), 3),
        "eur": round(money(pick(row, "Total Amt (EUR)", "Trx Amt (EUR)", "Tol EUR", "Tol")), 2),
        "road": pick(row, "Road Name", "Weg"),
        "country": pick(row, "Trx Country", "Land"),
        "invId": pick(row, "Inv ID"),
        "invDate": pick(row, "Inv Date"),
        "spec": pick(row, "Spec Page"),
    }


def parse_csv_report(path: Path, data: bytes | None = None) -> list[dict]:
    rows = []
    handle = io.StringIO(data.decode("utf-8-sig"), newline="") if data is not None else path.open(encoding="utf-8-sig", newline="")
    with handle:
        reader = csv.DictReader(handle, delimiter=";")
        if not reader.fieldnames:
            return []
        for row in reader:
            parsed = report_row_from_map(row)
            if parsed:
                rows.append(parsed)
    return rows


def parse_excel_report(path: Path, data: bytes | None = None) -> list[dict]:
    rows = []
    for _name, sheet in iter_excel_sheets(path, data):
        header_idx = None
        headers = []
        for i, row in enumerate(sheet[:40]):
            labels = [str(c).strip() for c in row]
            joined = " ".join(labels).lower()
            if "truck platenr" in joined or "trx date" in joined or ("kenteken" in joined and "tol" in joined):
                header_idx = i
                headers = labels
                break
        if header_idx is None:
            continue
        for row in sheet[header_idx + 1 :]:
            mapping = {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}
            parsed = report_row_from_map(mapping)
            if parsed:
                rows.append(parsed)
    return rows


def dmy_iso(dmy: str) -> str:
    parts = (dmy or "").split("/")
    if len(parts) != 3:
        return ""
    day, month, year = parts
    return f"{year}-{month.zfill(2)}-{day.zfill(2)}"


def parse_report(name: str, invoice: str | None, uploaded: dict) -> list[dict]:
    item = uploaded.get(name)
    if not item:
        raise FileNotFoundError(name)
    path = Path(name)
    if path.suffix.lower() not in REPORT_EXTENSIONS:
        raise FileNotFoundError(name)
    data = item["data"]
    rows = parse_csv_report(path, data) if path.suffix.lower() == ".csv" else parse_excel_report(path, data)
    if invoice in (None, "", "alles"):
        return rows
    if invoice in ("ongefactureerd", "none"):
        return [r for r in rows if not r.get("invId")]
    return [r for r in rows if r.get("invId") == invoice]


def list_invoices(name: str, uploaded: dict) -> list[dict]:
    rows = parse_report(name, None, uploaded)
    buckets: dict[str, list] = {}
    for row in rows:
        buckets.setdefault(row.get("invId") or "ongefactureerd", []).append(row)
    out = []
    for inv_id, items in buckets.items():
        dates = [dmy_iso(r["date"]) for r in items if dmy_iso(r["date"])]
        out.append(
            {
                "id": inv_id,
                "date": items[0].get("invDate") or "",
                "from": min(dates) if dates else "",
                "to": max(dates) if dates else "",
                "rows": len(items),
                "eur": round(sum(r["eur"] for r in items), 2),
            }
        )
    out.sort(
        key=lambda x: (x["id"] != "ongefactureerd", dmy_iso(x["date"]) or x["from"] or ""),
        reverse=True,
    )
    return out


def extract_invoice_pdf_info(name: str, data: bytes) -> dict:
    info = {
        "name": name,
        "invoiceNumber": "",
        "invoiceDate": "",
        "customerNumber": "",
        "dutchToll": None,
        "total": None,
        "textReadable": False,
    }
    filename_match = re.search(r"(20\d{2})(\d{2})(\d{2})[_ -](\d+)", Path(name).stem)
    if filename_match:
        year, month, day, customer = filename_match.groups()
        info["invoiceDate"] = f"{year}-{month}-{day}"
        info["customerNumber"] = customer

    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:3])
    except Exception:
        text = ""
    compact = " ".join(text.split())
    if not compact:
        return info

    recognized = False
    for key, pattern in {
        "invoiceNumber": r"Factuurnummer\s*:?\s*(\d+)",
        "customerNumber": r"Klantnummer\s*:?\s*(\d+)",
    }.items():
        match = re.search(pattern, compact, re.IGNORECASE)
        if match:
            info[key] = match.group(1)
            recognized = True

    date_match = re.search(r"Factuurdatum\s*:?\s*(\d{1,2})[-/](\d{1,2})[-/](20\d{2})", compact, re.IGNORECASE)
    if date_match:
        day, month, year = date_match.groups()
        info["invoiceDate"] = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        recognized = True

    def amount(pattern: str) -> float | None:
        match = re.search(pattern, compact, re.IGNORECASE)
        if not match:
            return None
        return round(money(match.group(1).replace(".", "")), 2)

    info["dutchToll"] = amount(r"Nederlandse\s+tolwegen.*?(\d[\d.]*,\d{2})")
    info["total"] = amount(r"\bTotaal\s+(\d[\d.]*,\d{2})")
    info["textReadable"] = recognized or info["dutchToll"] is not None or info["total"] is not None
    return info


def list_invoice_pdf_info(uploaded: dict) -> list[dict]:
    return [
        extract_invoice_pdf_info(name, item["data"])
        for name, item in uploaded.items()
        if item.get("kind") == "invoice"
    ]


def check_invoice_pdf(report_name: str, invoice_id: str, uploaded: dict) -> dict:
    invoices = list_invoices(report_name, uploaded)
    invoice = next((item for item in invoices if item["id"] == invoice_id), None)
    if not invoice:
        raise ValueError("Factuur niet gevonden")
    pdfs = list_invoice_pdf_info(uploaded)
    if invoice_id == "ongefactureerd":
        return {
            "uploaded": len(pdfs),
            "match": None,
            "status": "none",
            "message": "Deze transacties zijn nog niet gefactureerd.",
        }

    target_date = dmy_iso(invoice.get("date") or "")
    match = next((pdf for pdf in pdfs if pdf["invoiceNumber"] == invoice_id), None)
    if not match and target_date:
        match = next((pdf for pdf in pdfs if pdf["invoiceDate"] == target_date), None)
    if not match:
        return {
            "uploaded": len(pdfs),
            "match": None,
            "status": "warning" if pdfs else "none",
            "message": "Geen bijpassende factuur-PDF geüpload." if pdfs else "",
        }

    amount_matches = match["dutchToll"] is None or abs(match["dutchToll"] - invoice["eur"]) < 0.01
    number_matches = not match["invoiceNumber"] or match["invoiceNumber"] == invoice_id
    date_matches = not target_date or match["invoiceDate"] == target_date
    verified = amount_matches and number_matches and date_matches
    if match["textReadable"]:
        message = (
            "Factuur-PDF sluit aan op het tolrapport."
            if verified
            else "Controleer deze PDF: niet alle factuurgegevens sluiten aan."
        )
    else:
        message = "Factuurdatum komt overeen. De gescande PDF bevat geen uitleesbare tekst voor verdere controle."
    return {
        "uploaded": len(pdfs),
        "match": match,
        "status": "ok" if verified else "warning",
        "message": message,
        "reportToll": invoice["eur"],
    }


def _sheet_date(title: str, header_cell: str) -> str | None:
    text = f"{title} {header_cell}"
    match = re.search(r"(\d{1,2})\s*-\s*(\d{1,2})\s*-?\s*(20\d{2})?", text)
    if not match:
        return None
    day, month, year = int(match.group(1)), int(match.group(2)), match.group(3)
    year = int(year) if year else datetime.now().year
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def parse_week_sheet(name: str, sheet_rows: list[list[str]], trips: list) -> None:
    header = sheet_rows[0][3] if sheet_rows and len(sheet_rows[0]) > 3 else ""
    date_val = _sheet_date(name, header)
    last_customer = ""
    for row in sheet_rows:
        customer = (row[0] if row else "").strip()
        vehicle = (row[1] if len(row) > 1 else "").strip()
        plate_raw = (row[6] if len(row) > 6 else "").strip()
        trip_number = (row[2] if len(row) > 2 else "").strip()
        desc = (row[3] if len(row) > 3 else "").strip()
        km = money(row[5] if len(row) > 5 else 0)
        low = customer.lower()
        if low == "klant":
            continue
        if low.startswith("rit") or customer.replace(" ", "") in {"-", ".", "--"}:
            customer = last_customer
        elif customer:
            last_customer = customer
        else:
            customer = last_customer
        plate = normalize_plate(plate_raw)
        customer = canon_customer(customer)
        if not plate or not customer or not date_val:
            continue
        if customer.lower() == "loos" and not trip_number:
            trip_number = vehicle
        trips.append(
            {
                "date": date_val,
                "plate": plate,
                "plateRaw": plate_raw,
                "customer": customer,
                "km": km,
                "tripNumber": trip_number,
                "desc": desc,
            }
        )


def parse_week_files(uploaded: dict) -> dict:
    files = []
    trips = []
    sources = sorted(
        (
            (Path(name), item["data"])
            for name, item in uploaded.items()
            if item.get("kind") == "planning" and Path(name).suffix.lower() in {".xls", ".xlsx"}
        ),
        key=lambda item: item[0].name.lower(),
    )
    for path, data in sources:
        files.append(path.name)
        try:
            for sheet_name, rows in iter_excel_sheets(path, data):
                parse_week_sheet(sheet_name, rows, trips)
        except Exception as exc:
            return {"error": f"{path.name}: {exc}", "files": files, "rows": []}

    grouped: dict[tuple[str, str], dict] = {}
    for trip in trips:
        key = (trip["date"], trip["plate"])
        item = grouped.setdefault(
            key,
            {
                "date": trip["date"],
                "plate": trip["plate"],
                "plateRaw": trip["plateRaw"],
                "km": 0.0,
                "customers": defaultdict(float),
                "tripNumbers": [],
                "desc": [],
            },
        )
        item["km"] += trip["km"]
        item["customers"][trip["customer"]] += trip["km"] or 1
        if trip["tripNumber"] and trip["tripNumber"] not in item["tripNumbers"]:
            item["tripNumbers"].append(trip["tripNumber"])
        if trip["desc"]:
            item["desc"].append(trip["desc"])
        if "-" in trip["plateRaw"]:
            item["plateRaw"] = trip["plateRaw"]

    rows = []
    for item in grouped.values():
        customer = max(item["customers"].items(), key=lambda kv: kv[1])[0]
        extra = [name for name in item["customers"] if name != customer]
        rows.append(
            {
                "date": item["date"],
                "plate": item["plate"],
                "plateRaw": item["plateRaw"],
                "customer": customer,
                "km": round(item["km"], 1),
                "tripNumber": " / ".join(item["tripNumbers"]),
                "desc": " / ".join(item["desc"][:3]),
                "extra": extra,
            }
        )
    rows.sort(key=lambda r: (r["date"], r["plate"]))
    return {"files": files, "rows": rows}


def detect_kind(path: Path, data: bytes | None = None) -> str:
    if path.suffix.lower() == ".pdf":
        return "invoice"
    if is_week_name(path.name):
        return "planning"
    if path.suffix.lower() == ".csv":
        try:
            if parse_csv_report(path, data):
                return "report"
        except Exception:
            pass
        return "unknown"
    try:
        for _name, rows in iter_excel_sheets(path, data):
            sample = " ".join(" ".join(r[:8]) for r in rows[:8]).lower()
            if "truck platenr" in sample or "trx date" in sample:
                return "report"
            if "klant" in sample and "kenteken" in sample:
                return "planning"
        return "unknown"
    except Exception:
        return "unknown"


def build_customer_pdf_rows(
    report_name: str, invoice_id: str, uploaded: dict
) -> tuple[dict, dict[str, list[dict]]]:
    invoices = list_invoices(report_name, uploaded)
    invoice = next((item for item in invoices if item["id"] == invoice_id), None)
    if not invoice:
        raise ValueError("Factuur niet gevonden")

    transactions = parse_report(report_name, invoice_id, uploaded)
    planning = parse_week_files(uploaded)
    plan_map = {(row["date"], row["plate"]): row for row in planning.get("rows") or []}
    frequency: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for plan in plan_map.values():
        frequency[plan["plate"]][plan["customer"]] += 1

    grouped: dict[tuple[str, str, str], dict] = {}
    for transaction in transactions:
        iso_date = dmy_iso(transaction.get("date") or "")
        plate = transaction["plate"]
        plan = plan_map.get((iso_date, plate))
        counts = frequency.get(plate) or {}
        fallback = max(counts.items(), key=lambda entry: entry[1])[0] if counts else ""
        automatic_customer = plan["customer"] if plan else fallback
        customer = automatic_customer or "Nog te verdelen"
        key = (iso_date, plate, customer)
        item = grouped.setdefault(
            key,
            {
                "date": datetime.strptime(iso_date, "%Y-%m-%d").date() if iso_date else None,
                "plate": (plan.get("plateRaw") if plan else "") or transaction.get("plateRaw") or plate,
                "ritten": 0,
                "km": 0.0,
                "eur": 0.0,
                "customer": customer,
                "ritnr": (plan or {}).get("tripNumber") or "",
            },
        )
        item["ritten"] += 1
        item["km"] += transaction["km"]
        item["eur"] += transaction["eur"]
        if "-" in (transaction.get("plateRaw") or ""):
            item["plate"] = transaction["plateRaw"]

    by_customer: dict[str, list[dict]] = defaultdict(list)
    for item in grouped.values():
        item["km"] = round(item["km"], 1)
        item["eur"] = round(item["eur"], 2)
        by_customer[item["customer"]].append(item)
    for rows in by_customer.values():
        rows.sort(key=lambda row: ((row["date"] or date.min), row["plate"]))
    return invoice, dict(by_customer)


def list_pdf_customers(report_name: str, invoice_id: str, uploaded: dict) -> list[dict]:
    _invoice, by_customer = build_customer_pdf_rows(report_name, invoice_id, uploaded)
    result = []
    for customer, rows in sorted(by_customer.items(), key=lambda item: item[0].lower()):
        result.append(
            {
                "customer": customer,
                "rows": len(rows),
                "trips": sum(row["ritten"] for row in rows),
                "km": round(sum(row["km"] for row in rows), 1),
                "eur": round(sum(row["eur"] for row in rows), 2),
                "missingTripNumbers": sum(not row["ritnr"] for row in rows),
            }
        )
    return result


def build_customer_pdf(report_name: str, invoice_id: str, customer: str, uploaded: dict) -> bytes:
    import maak_pdf_per_klant as pdf

    invoice, by_customer = build_customer_pdf_rows(report_name, invoice_id, uploaded)
    rows = by_customer.get(customer)
    if not rows:
        raise ValueError("Klant niet gevonden in deze factuur")
    return pdf.make_pdf_bytes(customer, rows, invoice, report_name)


def build_customer_zip(report_name: str, invoice_id: str | None, uploaded: dict) -> bytes:
    import maak_pdf_per_klant as pdf

    invoices = list_invoices(report_name, uploaded)
    if invoice_id and invoice_id != "alles":
        invoices = [item for item in invoices if item["id"] == invoice_id]
    if not invoices:
        raise ValueError("Geen facturen gevonden")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for invoice in invoices:
            current, by_customer = build_customer_pdf_rows(report_name, invoice["id"], uploaded)
            folder = f"Factuur {pdf.safe_name(current['id'])}"
            if current.get("date"):
                folder += " - " + current["date"].replace("/", "-")
            for customer, rows in sorted(by_customer.items(), key=lambda item: item[0].lower()):
                filename = f"Toloverzicht - {pdf.safe_name(customer)}.pdf"
                archive.writestr(
                    f"{folder}/{filename}",
                    pdf.make_pdf_bytes(customer, rows, current, report_name),
                )
    return buffer.getvalue()
