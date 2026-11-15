# Privacy and retention
- Location data (SOS coordinates, live tracking points) is collected only when the effective switch is on, and only in the foreground. SOS location rows carry a retention window (`p_days` in `rp.upsert_sos_location`). Confirm values and add a scheduled purge job before production; no purge scheduler is included.
- Audit logs are append-only. Notification payloads may contain phone numbers/emails; limit access to staff roles.
- Employees can only read their own data (RLS). Org data is isolated per tenant.
- Import files are processed in memory; confirm no raw uploads are persisted in your deployment.
- Indian DPDP Act 2023 obligations (notice, consent, deletion, grievance officer) need legal review; this repo does not provide legal compliance.
