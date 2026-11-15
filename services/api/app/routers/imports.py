"""Employee CSV/XLSX import: upload -> preview (validation, duplicates, row errors) -> explicit commit. Never overwrites existing rows."""
import csv
import hashlib
import io
import json
import re
import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ..auth import Principal, db_for, require_roles
from ..common import AppError, audit, client_ip
from ..config import get_settings
from ..schemas import EmployeeIn

router = APIRouter(prefix="/employees/import", tags=["employee-import"])

TARGETS = ["employee_code", "name", "work_email", "phone", "department", "designation", "gender", "branch", "shift", "pickup_address", "pickup_lat",
           "pickup_lon", "drop_address", "drop_lat", "drop_lon", "preferred_pickup_time", "accessibility_needs"]
REQUIRED = {"employee_code", "name", "work_email"}
SYNONYMS = {"employee_code": ["employee id", "emp id", "emp_id", "employee_id", "code", "empcode", "id"], "name": ["full name", "employee name", "name"],
            "work_email": ["email", "work email", "email id", "mail"], "phone": ["mobile", "phone number", "contact", "contact number"],
            "pickup_address": ["pickup", "home address", "pickup location", "address"], "drop_address": ["drop", "drop location"],
            "pickup_lat": ["pickup latitude", "lat"], "pickup_lon": ["pickup longitude", "lng", "lon", "long"], "drop_lat": ["drop latitude"], "drop_lon": ["drop longitude"],
            "branch": ["location", "branch name", "office"], "shift": ["shift name"], "preferred_pickup_time": ["pickup time", "preferred time"],
            "accessibility_needs": ["accessibility", "special needs", "special requirements"]}


def _norm(h: str) -> str:
    return re.sub(r"[\s_\-]+", " ", str(h or "").strip().lower())


def auto_map(headers: list[str]) -> dict[str, str]:
    by_norm = {_norm(h): h for h in headers}
    mapping: dict[str, str] = {}
    for t in TARGETS:
        for cand in [t.replace("_", " "), *SYNONYMS.get(t, [])]:
            if cand in by_norm and by_norm[cand] not in mapping.values():
                mapping[t] = by_norm[cand]
                break
    return mapping


def read_table(filename: str, data: bytes) -> tuple[list[str], list[dict]]:
    s = get_settings()
    name = (filename or "").lower()
    if len(data) > s.max_import_bytes:
        raise AppError(413, "file_too_large", f"File exceeds {s.max_import_bytes} bytes")
    if name.endswith(".csv"):
        try:
            text_data = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise AppError(422, "bad_encoding", "CSV must be UTF-8") from exc
        reader = csv.DictReader(io.StringIO(text_data))
        headers = [h for h in (reader.fieldnames or []) if h]
        rows = []
        for i, r in enumerate(reader):
            if i >= s.max_import_rows:
                raise AppError(422, "too_many_rows", f"At most {s.max_import_rows} rows per import")
            rows.append({k: (v or "").strip() for k, v in r.items() if k})
        return headers, rows
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook
        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)     # no macros, formulas read as cached values
        except Exception as exc:
            raise AppError(422, "bad_workbook", "Could not read the .xlsx file") from exc
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        head = next(it, None)
        if not head:
            return [], []
        headers = [str(h).strip() for h in head if h not in (None, "")]
        rows = []
        for i, vals in enumerate(it):
            if i >= s.max_import_rows:
                raise AppError(422, "too_many_rows", f"At most {s.max_import_rows} rows per import")
            if all(v in (None, "") for v in vals):
                continue
            rows.append({headers[j]: ("" if v is None else str(v).strip()) for j, v in enumerate(vals[:len(headers)])})
        return headers, rows
    raise AppError(415, "unsupported_type", "Only .csv and .xlsx files are accepted")


@router.post("/preview")
async def preview(request: Request, file: UploadFile = File(...), mapping: str | None = Form(None),
                  p: Principal = Depends(require_roles("org_admin")), conn: AsyncConnection = Depends(db_for)):
    data = await file.read(get_settings().max_import_bytes + 1)
    headers, rows = read_table(file.filename or "", data)
    if not headers:
        raise AppError(422, "empty_file", "No header row found")
    try:
        mapping_d = json.loads(mapping) if mapping else auto_map(headers)
    except json.JSONDecodeError as exc:
        raise AppError(422, "bad_mapping", "mapping must be JSON {target: source_header}") from exc
    unknown = {v for v in mapping_d.values() if v not in headers} | (set(mapping_d) - set(TARGETS))
    if unknown:
        raise AppError(422, "bad_mapping", f"Unknown columns in mapping: {sorted(map(str, unknown))}")
    missing = REQUIRED - set(mapping_d)
    if missing:
        raise AppError(422, "missing_required_columns", f"Map the required columns: {sorted(missing)}", {"auto_mapping": mapping_d, "headers": headers})

    branches = {r.name.lower(): r.id for r in await conn.execute(text("SELECT id, name FROM locations WHERE organization_id=:o AND kind IN ('branch','office')"), {"o": p.org_id})}
    shifts = {r.name.lower(): r.id for r in await conn.execute(text("SELECT id, name FROM shifts WHERE organization_id=:o"), {"o": p.org_id})}
    existing_codes = {r.employee_code.lower() for r in await conn.execute(text("SELECT employee_code FROM employees WHERE organization_id=:o"), {"o": p.org_id})}
    existing_emails = {r.work_email.lower() for r in await conn.execute(text("SELECT work_email FROM employees WHERE organization_id=:o"), {"o": p.org_id})}

    valid, errors, dup_n = [], [], 0
    seen_codes: set[str] = set()
    seen_emails: set[str] = set()
    for idx, raw in enumerate(rows, start=2):       # row 1 = header
        rec = {t: raw.get(src, "") for t, src in mapping_d.items()}
        problems: list[str] = []
        payload: dict = {}
        for k in ("employee_code", "name", "work_email", "phone", "department", "designation", "pickup_address", "drop_address"):
            if rec.get(k):
                payload[k] = rec[k]
        if rec.get("gender"):
            payload["gender"] = rec["gender"].lower()
        for k in ("pickup_lat", "pickup_lon", "drop_lat", "drop_lon"):
            if rec.get(k):
                try:
                    payload[k] = float(rec[k])
                except ValueError:
                    problems.append(f"{k}: not a number")
        if rec.get("preferred_pickup_time"):
            payload["preferred_pickup_time"] = rec["preferred_pickup_time"]
        if rec.get("accessibility_needs"):
            payload["accessibility_needs"] = [x.strip().lower() for x in re.split(r"[;,|]", rec["accessibility_needs"]) if x.strip()]
        if rec.get("branch"):
            if rec["branch"].lower() in branches:
                payload["branch_id"] = branches[rec["branch"].lower()]
            else:
                problems.append(f"branch '{rec['branch']}' not found")
        if rec.get("shift"):
            if rec["shift"].lower() in shifts:
                payload["shift_id"] = shifts[rec["shift"].lower()]
            else:
                problems.append(f"shift '{rec['shift']}' not found")
        try:
            emp = EmployeeIn(**payload)
        except ValidationError as exc:
            problems += [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
            emp = None
        except TypeError as exc:
            problems.append(str(exc)); emp = None
        if emp is not None and not problems:
            code, email = emp.employee_code.lower(), str(emp.work_email).lower()
            if code in existing_codes or email in existing_emails:
                dup_n += 1
                errors.append({"row": idx, "kind": "duplicate_existing", "messages": ["employee_code or email already exists; row will be skipped (existing data is never overwritten)"]})
                continue
            if code in seen_codes or email in seen_emails:
                dup_n += 1
                errors.append({"row": idx, "kind": "duplicate_in_file", "messages": ["duplicate employee_code or email within the file"]})
                continue
            seen_codes.add(code); seen_emails.add(email)
            valid.append(json.loads(emp.model_dump_json()))
        else:
            errors.append({"row": idx, "kind": "invalid", "messages": problems})

    job = (await conn.execute(text("""INSERT INTO import_jobs (organization_id, filename, content_hash, mapping, parsed_rows, total_rows, valid_rows, error_rows, duplicate_rows, errors, created_by)
        VALUES (:o,:f,:h,CAST(:m AS jsonb),CAST(:pr AS jsonb),:t,:v,:e,:d,CAST(:er AS jsonb),:u) RETURNING id"""),
                              {"o": p.org_id, "f": (file.filename or "upload")[:200], "h": hashlib.sha256(data).hexdigest(), "m": json.dumps(mapping_d), "pr": json.dumps(valid),
                               "t": len(rows), "v": len(valid), "e": len(errors) - dup_n, "d": dup_n, "er": json.dumps(errors[:500]), "u": p.user_id})).first()
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="import.previewed", entity_type="import_job", entity_id=job.id,
                details={"rows": len(rows), "valid": len(valid), "errors": len(errors) - dup_n, "duplicates": dup_n}, ip=client_ip(request))
    return {"job_id": job.id, "mapping": mapping_d, "unmapped_headers": [h for h in headers if h not in mapping_d.values()], "total_rows": len(rows), "valid_rows": len(valid),
            "error_rows": len(errors) - dup_n, "duplicate_rows": dup_n, "errors": errors[:100], "sample": valid[:5], "commit_url": f"/api/v1/employees/import/{job.id}/commit"}


@router.post("/{job_id}/commit")
async def commit(job_id: uuid.UUID, request: Request, p: Principal = Depends(require_roles("org_admin")), conn: AsyncConnection = Depends(db_for)):
    """Inserts the previewed valid rows. Safe to retry: a committed job returns its original result; conflicts are skipped, never overwritten."""
    job = (await conn.execute(text("SELECT * FROM import_jobs WHERE organization_id=:o AND id=:i FOR UPDATE"), {"o": p.org_id, "i": job_id})).first()
    if job is None:
        raise AppError(404, "not_found", "Import job not found")
    if job.status == "committed":
        return {"job_id": job.id, "status": "committed", "inserted": job.valid_rows, "already_committed": True}
    inserted = skipped = 0
    cols = list(EmployeeIn.model_fields)
    for row in job.parsed_rows:
        vals = {c: row.get(c) for c in cols}
        vals["accessibility_needs"] = vals.get("accessibility_needs") or []
        vals.pop("user_id", None)
        names = [c for c in vals if c != "user_id"]
        r = (await conn.execute(text(f"INSERT INTO employees (organization_id, {', '.join(names)}) VALUES (:o, {', '.join(':' + n for n in names)}) ON CONFLICT DO NOTHING RETURNING id"),
                                {"o": p.org_id, **{n: vals[n] for n in names}})).first()
        inserted += 1 if r else 0
        skipped += 0 if r else 1
    await conn.execute(text("UPDATE import_jobs SET status='committed', committed_at=now(), valid_rows=:n, parsed_rows='[]' WHERE organization_id=:o AND id=:i"), {"n": inserted, "o": p.org_id, "i": job_id})
    await audit(conn, org_id=p.org_id, actor=p.user_id, action="import.committed", entity_type="import_job", entity_id=job_id, details={"inserted": inserted, "skipped_conflicts": skipped}, ip=client_ip(request))
    return {"job_id": job_id, "status": "committed", "inserted": inserted, "skipped_conflicts": skipped}


@router.get("/{job_id}")
async def get_job(job_id: uuid.UUID, p: Principal = Depends(require_roles("org_admin")), conn: AsyncConnection = Depends(db_for)):
    j = (await conn.execute(text("SELECT id, filename, status, total_rows, valid_rows, error_rows, duplicate_rows, errors, created_at, committed_at FROM import_jobs WHERE organization_id=:o AND id=:i"), {"o": p.org_id, "i": job_id})).first()
    if j is None:
        raise AppError(404, "not_found", "Import job not found")
    return dict(j._mapping)
