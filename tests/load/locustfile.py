"""Load profile for the RoutePilot API (read-heavy dispatcher/employee/driver traffic + SOS alerts with GPS disabled).

Prepare:  bash scripts/run_load.sh   (creates a scratch DB, seeds N employees, starts the API, runs this file headless, prints a summary)
Manual:   SEED_JSON=tests/load/.seed.json locust -f tests/load/locustfile.py --host http://localhost:8000
"""
import json
import os
import random
import uuid
from datetime import date, timedelta

from locust import HttpUser, between, task

SEED = json.load(open(os.environ.get("SEED_JSON", os.path.join(os.path.dirname(__file__), ".seed.json"))))
ORG = SEED["org_id"]
TOK = SEED["tokens"]
EMPLOYEES = [k for k in TOK if k.startswith("employee")]
DRIVERS = [k for k in TOK if k.startswith("driver")]
TOMORROW = str(date.today() + timedelta(days=1))


class Base(HttpUser):
    abstract = True
    wait_time = between(1, 3)
    role = ""

    def on_start(self):
        self.client.headers.update({"Authorization": f"Bearer {TOK[self.role]}", "X-Organization-Id": ORG})

    def api(self, method, path, name=None, ok=(), **kw):
        with self.client.request(method, f"/api/v1{path}", name=name or path, catch_response=True, **kw) as r:
            if r.status_code >= 400 and r.status_code not in ok:
                r.failure(f"{r.status_code} {r.text[:120]}")
            else:
                r.success()
            return r


class Dispatcher(Base):
    weight = 2
    role = "dispatcher"

    @task(5)
    def overview(self):
        self.api("GET", "/reports")

    @task(4)
    def employees(self):
        self.api("GET", f"/employees?page={random.randint(1, 5)}&page_size=25", name="/employees?page")

    @task(3)
    def allocations(self):
        self.api("GET", f"/allocations?service_date={TOMORROW}")

    @task(2)
    def board(self):
        self.api("GET", f"/vehicles-board?service_date={TOMORROW}", name="/vehicles-board")

    @task(1)
    def report(self):
        self.api("GET", "/reports/driver-workloads")

    @task(1)
    def dry_run_plan(self):
        shifts = self.client.get("/api/v1/shifts", name="/shifts").json()["items"]
        if shifts:
            self.api("POST", "/dispatch/allocate", name="/dispatch/allocate (dry run)",
                     json={"service_date": TOMORROW, "shift_id": shifts[0]["id"], "direction": "pickup", "generate_requests": False, "dry_run": True})


class Employee(Base):
    weight = 10

    def on_start(self):
        self.role = random.choice(EMPLOYEES)
        super().on_start()

    @task(5)
    def trips(self):
        self.api("GET", "/trips/mine")

    @task(3)
    def inbox(self):
        self.api("GET", "/notifications")

    @task(1)
    def request_ride(self):
        d = str(date.today() + timedelta(days=random.randint(1, 20)))
        self.api("POST", "/me/transport-requests", name="/me/transport-requests (create)", json={"service_date": d, "direction": random.choice(["pickup", "drop"])}, ok=(409,))

    @task(1)
    def sos(self):
        self.api("POST", "/sos", name="/sos", headers={"Idempotency-Key": uuid.uuid4().hex}, json={"message": "load test"})


class Driver(Base):
    weight = 2

    def on_start(self):
        self.role = random.choice(DRIVERS)
        super().on_start()

    @task(4)
    def trips(self):
        self.api("GET", "/trips/mine")

    @task(2)
    def inbox(self):
        self.api("GET", "/notifications")

    @task(1)
    def tracking_status(self):
        self.api("GET", "/tracking/status")
