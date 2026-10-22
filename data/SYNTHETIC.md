# SYNTHETIC DATA

Every record in `data/synthetic/` is synthetic and was generated during the build window by
`data/generator.py` (fixed seed 72, deterministic).

- Organisations are fictional: "Gmina Przykladowo" (a municipality) and "Przykladowo Serwis sp. z o.o." (an SME).
- There are no real names, no real PESEL numbers and no real contact details. Data subjects appear only as
  opaque keys (`subj_<hash>` of a counter). E-mail domains use RFC 2606 `.example` / `example.net`;
  IP addresses use RFC 5737 documentation ranges (198.51.100.0/24, 203.0.113.0/24).

## Source systems (as exposed through Zetaris)

| Source | Format | Tables |
|---|---|---|
| Incident system | PostgreSQL (`seed_postgres.sql`) | incidents, incident_updates, access_logs, export_items |
| Record of processing activities | CSV | ropa |
| Asset and encryption register | CSV | assets |
| Processor contracts | CSV | processor_contracts |

## Test cases

| Case | Incident | Scenario |
|---|---|---|
| 1 | INC-2026-001 | Lost laptop, full-disk encryption, key separate, backup available |
| 2 | INC-2026-002 | Beneficiary list (1 200 people, name, PESEL, address) e-mailed to an unknown external address; later the recipient confirms deletion |
| 3 | INC-2026-003 | Ransomware on the HR file server, offline backup; the access log shows a 2 GB outbound transfer, the IT report says no exfiltration |
| 4 | INC-2026-004 | Breach at the payroll processor, reported to the controller 50 hours after detection |
| 5 | INC-2026-005 | Contact form submissions publicly listable, discovery time not recorded |
