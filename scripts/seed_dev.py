"""LOCAL DEVELOPMENT ONLY. Seeds a demo organization with users/drivers/vehicles/employees and prints short-lived dev JWTs
signed with SUPABASE_JWT_SECRET so you can call the API without a Supabase project. Refuses to run when APP_ENV is production/staging.

  python scripts/seed_dev.py
"""
import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api"))
os.chdir(Path(__file__).resolve().parent.parent / "services" / "api")

import jwt  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import dispose_engine, system_session  # noqa: E402


async def user(conn, org, role, name, email):
    uid = await conn.scalar(text("SELECT user_id FROM user_profiles WHERE lower(email)=lower(:e)"), {"e": email}) or uuid.uuid4()
    await conn.execute(text("INSERT INTO user_profiles (user_id,email,full_name) VALUES (:u,:e,:n) ON CONFLICT (user_id) DO NOTHING"), {"u": uid, "e": email, "n": name})
    await conn.execute(text("INSERT INTO organization_members (organization_id,user_id,role) VALUES (:o,:u,:r) ON CONFLICT DO NOTHING"), {"o": org, "u": uid, "r": role})
    return uid


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--employees", type=int, default=6, help="number of demo employees (load testing)")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--json", help="also write {org_id, tokens} to this file")
    args = ap.parse_args()
    s = get_settings()
    if s.app_env.lower() not in {"development", "dev", "test"}:
        raise SystemExit("seed_dev.py is for local development only (APP_ENV must be development).")
    if not s.supabase_jwt_secret:
        raise SystemExit("Set SUPABASE_JWT_SECRET in services/api/.env to mint dev tokens.")
    async with system_session() as conn:
        org = await conn.scalar(text("SELECT id FROM organizations WHERE slug='demo'"))
        if org is None:
            org = await conn.scalar(text("INSERT INTO organizations (name, slug, timezone) VALUES ('Demo Company','demo','Asia/Kolkata') RETURNING id"))
        await conn.execute(text("INSERT INTO organization_settings (organization_id) VALUES (:o) ON CONFLICT DO NOTHING"), {"o": org})
        office = await conn.scalar(text("SELECT id FROM locations WHERE organization_id=:o AND name='Demo HQ'"), {"o": org})
        if office is None:
            office = await conn.scalar(text("INSERT INTO locations (organization_id,kind,name,address,latitude,longitude,geocode_source) VALUES (:o,'branch','Demo HQ','OMR, Chennai',12.9516,80.2401,'user') RETURNING id"), {"o": org})
        shift = await conn.scalar(text("SELECT id FROM shifts WHERE organization_id=:o AND name='Morning'"), {"o": org})
        if shift is None:
            shift = await conn.scalar(text("INSERT INTO shifts (organization_id,name,start_time,end_time) VALUES (:o,'Morning','09:00','18:00') RETURNING id"), {"o": org})
        who = {"org_admin": await user(conn, org, "org_admin", "Demo Admin", "admin@demo.example.com"),
               "dispatcher": await user(conn, org, "dispatcher", "Demo Dispatcher", "dispatcher@demo.example.com")}
        for i in range(1, 4):
            uid = await user(conn, org, "driver", f"Driver {i}", f"driver{i}@demo.example.com")
            who[f"driver{i}"] = uid
            did = await conn.scalar(text("SELECT id FROM drivers WHERE organization_id=:o AND driver_code=:c"), {"o": org, "c": f"D{i:03d}"})
            if did is None:
                did = await conn.scalar(text("INSERT INTO drivers (organization_id,user_id,driver_code,name,phone,license_number,license_valid_until,eligible_shift_ids,local_trip_eligible) VALUES (:o,:u,:c,:n,:p,:l,current_date+365,ARRAY[CAST(:s AS uuid)],true) RETURNING id"),
                                        {"o": org, "u": uid, "c": f"D{i:03d}", "n": f"Driver {i}", "p": f"+9190000000{i:02d}", "l": f"TN00{i}2020000{i}", "s": shift})
            if not await conn.scalar(text("SELECT 1 FROM vehicles WHERE organization_id=:o AND vehicle_code=:c"), {"o": org, "c": f"V{i:03d}"}):
                await conn.execute(text("INSERT INTO vehicles (organization_id,vehicle_code,registration_number,capacity,default_driver_id,insurance_valid_until,fitness_valid_until) VALUES (:o,:c,:r,4,:d,current_date+365,current_date+365)"), {"o": org, "c": f"V{i:03d}", "r": f"TN01AB{1000 + i}", "d": did})
        base = [(12.9249, 80.1000), (12.9010, 80.2279), (13.0067, 80.2206), (12.9716, 80.2210), (12.8456, 80.2263), (13.0418, 80.2341)]
        spots = [(base[i % 6][0] + (i // 6) * 0.002, base[i % 6][1] + (i // 6) * 0.002) for i in range(args.employees)]
        for i, (la, lo) in enumerate(spots, 1):
            uid = await user(conn, org, "employee", f"Employee {i}", f"emp{i}@demo.example.com")
            who[f"employee{i}"] = uid
            if not await conn.scalar(text("SELECT 1 FROM employees WHERE organization_id=:o AND employee_code=:c"), {"o": org, "c": f"E{i:04d}"}):
                await conn.execute(text("""INSERT INTO employees (organization_id,user_id,employee_code,branch_id,name,work_email,phone,gender,shift_id,pickup_address,pickup_lat,pickup_lon,drop_address,drop_lat,drop_lon)
                    VALUES (:o,:u,:c,:b,:n,:e,:p,'female',:s,'Home',:la,:lo,'Home',:la,:lo)"""), {"o": org, "u": uid, "c": f"E{i:04d}", "b": office, "n": f"Employee {i}", "e": f"emp{i}@demo.example.com", "p": f"+91980{i:07d}", "s": shift, "la": la, "lo": lo})
    now = int(time.time())
    print(f"Demo organization id: {org}\nSend: Authorization: Bearer <token>   X-Organization-Id: {org}\n(dev tokens valid 8 hours)\n")
    tokens = {}
    for role, uid in who.items():
        tokens[role] = jwt.encode({"sub": str(uid), "aud": s.supabase_jwt_audience, "role": "authenticated", "iat": now, "exp": now + 8 * 3600}, s.supabase_jwt_secret, algorithm="HS256")
        if not args.quiet:
            print(f"{role:12s} {tokens[role]}")
    if args.json:
        Path(args.json).write_text(json.dumps({"org_id": str(org), "tokens": tokens}))
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
