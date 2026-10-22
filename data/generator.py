"""Synthetic data generator for Breach72.

Every value produced here is SYNTHETIC. The organisations ("Gmina Przykladowo",
a fictional municipality, and "Przykladowo Serwis sp. z o.o.", a fictional SME)
do not exist. No real names, no real PESEL numbers, no real e-mail domains
(only RFC 2606 example domains).

Four logical source systems, as they are exposed through Zetaris:
  1. incident system (PostgreSQL): incidents, incident_updates, access_logs, export_items
  2. record of processing activities (CSV): ropa
  3. asset and encryption register (CSV): assets
  4. processor contracts (CSV): processor_contracts

Run:  python data/generator.py            (writes data/synthetic/*.csv and seed_postgres.sql)
The output is deterministic (fixed seed), so recordings stay valid.
"""
from __future__ import annotations

import csv
import hashlib
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent / "synthetic"
SEED = 72

INCIDENTS = [
    # incident_id, case_no, reported_at, aware_at, system_id, category, summary, reporter_role
    ("INC-2026-001", 1, "2026-10-19T08:10:00+02:00", "2026-10-19T08:30:00+02:00", "SYS-LAPTOP-114",
     "lost_device", "Employee reports laptop left on a train.", "employee"),
    ("INC-2026-002", 2, "2026-10-20T09:05:00+02:00", "2026-10-20T09:15:00+02:00", "SYS-SOCIAL-REG",
     "misdirected_email", "Spreadsheet with beneficiary list e-mailed to a wrong external address.", "case_officer"),
    ("INC-2026-003", 3, "2026-10-21T06:40:00+02:00", "2026-10-21T07:05:00+02:00", "SYS-HR-SRV",
     "ransomware", "HR file server encrypted, ransom note found.", "it_admin"),
    ("INC-2026-004", 4, "2026-10-21T15:00:00+02:00", "2026-10-21T15:00:00+02:00", "SYS-PAYROLL-EXT",
     "processor_breach", "Payroll processor reports unauthorised access at its side.", "processor"),
    ("INC-2026-005", 5, "2026-10-22T10:00:00+02:00", "", "SYS-WEB-FORMS",
     "misconfiguration", "Contact form submissions found publicly listable; discovery date not recorded.", "citizen"),
]

UPDATES = [
    # update_id, incident_id, ts, source, code, detail
    ("UPD-001", "INC-2026-001", "2026-10-19T09:00:00+02:00", "it_report", "REMOTE_WIPE_SENT", "Remote wipe command queued."),
    ("UPD-002", "INC-2026-002", "2026-10-20T16:40:00+02:00", "case_officer", "RECIPIENT_IDENTIFIED",
     "Recipient identified as a private person; contact attempted."),
    ("UPD-003", "INC-2026-002", "2026-10-21T11:20:00+02:00", "case_officer", "RECIPIENT_DELETION_CONFIRMED",
     "Recipient confirmed in writing that the file was deleted and not shared."),
    ("UPD-004", "INC-2026-003", "2026-10-21T13:00:00+02:00", "it_report", "IT_REPORT_NO_EXFIL",
     "Initial IT report: no evidence of data exfiltration."),
    ("UPD-005", "INC-2026-003", "2026-10-21T14:30:00+02:00", "it_report", "BACKUP_RESTORE_STARTED",
     "Restore from offline backup started."),
    ("UPD-006", "INC-2026-004", "2026-10-21T15:00:00+02:00", "processor", "PROCESSOR_NOTICE",
     "Processor states it detected the breach on its side 50 hours earlier."),
    ("UPD-007", "INC-2026-004", "2026-10-19T13:00:00+02:00", "processor", "PROCESSOR_DETECTED",
     "Detection time at the processor, as stated in the processor notice."),
]

ROPA = [
    # processing_id, name, system_id, data_categories, special_categories, subject_types, subjects_estimate, processor_id
    ("PR-01", "Staff IT equipment", "SYS-LAPTOP-114", "name;email;work_documents", "no", "employees", 40, ""),
    ("PR-07", "Social assistance beneficiaries register", "SYS-SOCIAL-REG", "name;pesel;address;benefit_type", "no",
     "beneficiaries", 1450, ""),
    ("PR-03", "Personnel files", "SYS-HR-SRV", "name;pesel;address;salary;sick_leave_certificates", "yes",
     "employees", 310, ""),
    ("PR-04", "Payroll", "SYS-PAYROLL-EXT", "name;pesel;bank_account;salary", "no", "employees", 310, "PROC-01"),
    ("PR-09", "Citizen contact form", "SYS-WEB-FORMS", "name;email;message_text", "no", "citizens", 2600, "PROC-02"),
]

ASSETS = [
    # system_id, name, asset_type, encryption_at_rest, key_location, backup, backup_offline
    ("SYS-LAPTOP-114", "Laptop L-114", "laptop", "full_disk_aes256", "separate_tpm_pin", "daily", "yes"),
    ("SYS-SOCIAL-REG", "Social register DB", "database", "none", "none", "daily", "yes"),
    ("SYS-HR-SRV", "HR file server", "file_server", "none", "none", "daily", "yes"),
    ("SYS-PAYROLL-EXT", "External payroll platform", "saas", "provider_managed", "provider", "provider", "unknown"),
    ("SYS-WEB-FORMS", "Website forms module", "web_app", "none", "none", "weekly", "no"),
]

PROCESSORS = [
    # processor_id, name, art28_contract_signed, breach_notice_hours, contract_ref
    ("PROC-01", "Przykladowo Payroll Services (fictional)", "yes", 24, "UP-2024-011"),
    ("PROC-02", "Przykladowo Web Hosting (fictional)", "yes", 48, "UP-2023-004"),
]


def _access_logs(rng: random.Random) -> list[tuple]:
    logs = [
        # log_id, incident_id, system_id, ts, actor_role, action, bytes, destination, records_count, attachment_encrypted
        ("LOG-0001", "INC-2026-001", "SYS-LAPTOP-114", "2026-10-19T07:55:00+02:00", "employee", "logoff", 0, "", 0, ""),
        ("LOG-0002", "INC-2026-002", "SYS-SOCIAL-REG", "2026-10-20T08:58:00+02:00", "case_officer", "export", 412_000,
         "local_workstation", 1200, "no"),
        ("LOG-0003", "INC-2026-002", "SYS-SOCIAL-REG", "2026-10-20T09:01:00+02:00", "case_officer", "email_send",
         418_500, "unknown.recipient@example.net", 1200, "no"),
        ("LOG-0004", "INC-2026-003", "SYS-HR-SRV", "2026-10-21T03:12:00+02:00", "unknown", "outbound_transfer",
         2_147_483_648, "198.51.100.23", 0, ""),
        ("LOG-0005", "INC-2026-003", "SYS-HR-SRV", "2026-10-21T03:40:00+02:00", "unknown", "mass_encrypt", 0, "", 0, ""),
        ("LOG-0006", "INC-2026-005", "SYS-WEB-FORMS", "2026-10-22T09:40:00+02:00", "anonymous", "directory_listing",
         88_000, "203.0.113.50", 2600, "no"),
    ]
    # background noise: ordinary activity unrelated to incidents
    for i in range(7, 47):
        sys_id = rng.choice([a[0] for a in ASSETS])
        logs.append((f"LOG-{i:04d}", "", sys_id, f"2026-10-{rng.randint(10, 21):02d}T{rng.randint(7, 17):02d}:"
                     f"{rng.randint(0, 59):02d}:00+02:00", "staff", rng.choice(["login", "read", "update"]),
                     rng.randint(1_000, 90_000), "", 0, ""))
    return logs


def _export_items() -> list[tuple]:
    """One row per record in the INC-2026-002 export. Subject keys are opaque hashes, not identifiers."""
    rows = []
    for n in range(1, 1201):
        key = hashlib.sha256(f"SYNTHETIC-SUBJECT-{n}".encode()).hexdigest()[:16]
        rows.append(("LOG-0002", "INC-2026-002", f"subj_{key}", "name;pesel;address;benefit_type"))
    return rows


TABLES = {
    "incidents": (["incident_id", "case_no", "reported_at", "aware_at", "system_id", "category", "summary",
                   "reporter_role"], INCIDENTS),
    "incident_updates": (["update_id", "incident_id", "ts", "source", "code", "detail"], UPDATES),
    "ropa": (["processing_id", "name", "system_id", "data_categories", "special_categories", "subject_types",
              "subjects_estimate", "processor_id"], ROPA),
    "assets": (["system_id", "name", "asset_type", "encryption_at_rest", "key_location", "backup", "backup_offline"],
               ASSETS),
    "processor_contracts": (["processor_id", "name", "art28_contract_signed", "breach_notice_hours", "contract_ref"],
                            PROCESSORS),
}

# which logical source system holds which table (mirrors the Zetaris catalog layout)
SOURCE_OF_TABLE = {
    "incidents": "incident_db", "incident_updates": "incident_db", "access_logs": "incident_db",
    "export_items": "incident_db", "ropa": "ropa_csv", "assets": "asset_register_csv",
    "processor_contracts": "processor_contracts_csv",
}


def build_tables() -> dict[str, tuple[list[str], list[tuple]]]:
    rng = random.Random(SEED)
    tables = dict(TABLES)
    tables["access_logs"] = (["log_id", "incident_id", "system_id", "ts", "actor_role", "action", "bytes",
                              "destination", "records_count", "attachment_encrypted"], _access_logs(rng))
    tables["export_items"] = (["log_id", "incident_id", "subject_key", "data_categories"], _export_items())
    return tables


def _sql_literal(v) -> str:
    if isinstance(v, int):
        return str(v)
    if v == "":
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"


def write_all(out: Path = OUT) -> None:
    out.mkdir(parents=True, exist_ok=True)
    tables = build_tables()
    sql = ["-- SYNTHETIC DATA. Generated by data/generator.py. Load into the PostgreSQL source used by Zetaris."]
    for name, (cols, rows) in tables.items():
        with open(out / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            w.writerows(rows)
        if SOURCE_OF_TABLE[name] == "incident_db":
            sql.append(f"DROP TABLE IF EXISTS {name};")
            sql.append(f"CREATE TABLE {name} (" + ", ".join(
                f"{c} {'BIGINT' if c in ('bytes', 'records_count', 'case_no') else 'TEXT'}" for c in cols) + ");")
            for r in rows:
                sql.append(f"INSERT INTO {name} VALUES (" + ", ".join(_sql_literal(v) for v in r) + ");")
    (out / "seed_postgres.sql").write_text("\n".join(sql) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write_all()
    print(f"SYNTHETIC data written to {OUT}")
