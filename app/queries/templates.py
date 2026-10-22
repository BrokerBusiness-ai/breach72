"""Vetted, parameterised SQL templates.

Breach72 never sends model-written SQL to a data source. Agents pick a template id
and supply typed parameters; the template text is fixed and reviewed.

Table names are logical ({incidents}, {ropa}, ...). The backend resolves them to
physical names (for Zetaris: catalog.schema.table, set in config/sources.json).
Each fact the Evidence Auditor re-verifies has a primary template and an
independent alternative template that reads a different table or column.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_PARAM = re.compile(r":([a-z_]+)")


@dataclass(frozen=True)
class Template:
    id: str
    purpose: str
    sql: str
    params: tuple[str, ...]
    tables: tuple[str, ...]
    verifies: tuple[str, ...] = field(default_factory=tuple)


TEMPLATES: dict[str, Template] = {t.id: t for t in [
    Template("T01_INCIDENT_CORE", "Core incident record and awareness time",
             "SELECT incident_id, case_no, reported_at, aware_at, system_id, category, summary, reporter_role "
             "FROM {incidents} WHERE incident_id = :incident_id",
             ("incident_id",), ("incidents",)),
    Template("T02_INCIDENT_UPDATES", "Status updates recorded up to the assessment time",
             "SELECT update_id, ts, source, code, detail FROM {incident_updates} "
             "WHERE incident_id = :incident_id AND ts <= :as_of ORDER BY ts",
             ("incident_id", "as_of"), ("incident_updates",)),
    Template("T03_TRANSFER_EVENTS", "Outbound events (export, e-mail, transfer) linked to the incident",
             "SELECT log_id, ts, action, bytes, destination, records_count, attachment_encrypted "
             "FROM {access_logs} WHERE incident_id = :incident_id "
             "AND action IN ('export', 'email_send', 'outbound_transfer', 'directory_listing') ORDER BY ts",
             ("incident_id",), ("access_logs",), ("affected_count", "exfiltration", "transfer_encrypted")),
    Template("T04_EXPORT_ITEMS_COUNT", "Independent count of records in the exported set",
             "SELECT COUNT(*) AS n_records, COUNT(DISTINCT subject_key) AS n_subjects "
             "FROM {export_items} WHERE incident_id = :incident_id",
             ("incident_id",), ("export_items",), ("affected_count",)),
    Template("T05_ROPA_FOR_SYSTEM", "Processing activities and data categories held in the system",
             "SELECT processing_id, name, data_categories, special_categories, subject_types, subjects_estimate, "
             "processor_id FROM {ropa} WHERE system_id = :system_id",
             ("system_id",), ("ropa",), ("data_categories",)),
    Template("T06_ASSET_SECURITY", "Encryption at rest, key location and backup of the system",
             "SELECT system_id, asset_type, encryption_at_rest, key_location, backup, backup_offline "
             "FROM {assets} WHERE system_id = :system_id",
             ("system_id",), ("assets",), ("encryption", "backup")),
    Template("T07_PROCESSOR_CONTRACT", "Processor contract (Art. 28) for the system's processing",
             "SELECT processor_id, name, art28_contract_signed, breach_notice_hours, contract_ref "
             "FROM {processor_contracts} WHERE processor_id = :processor_id",
             ("processor_id",), ("processor_contracts",)),
    Template("T08_EXPORT_CATEGORIES", "Data categories recorded on the exported items (independent of ROPA)",
             "SELECT DISTINCT data_categories FROM {export_items} WHERE incident_id = :incident_id",
             ("incident_id",), ("export_items",), ("data_categories",)),
]}


def render(template_id: str, params: dict, table_map: dict[str, str]) -> tuple[str, list]:
    """Return (sql_with_qmarks, ordered_values). Unknown params or tables raise."""
    t = TEMPLATES[template_id]
    missing = set(t.params) - set(params)
    if missing:
        raise ValueError(f"{template_id}: missing params {sorted(missing)}")
    extra = set(params) - set(t.params)
    if extra:
        raise ValueError(f"{template_id}: unexpected params {sorted(extra)}")
    sql = t.sql.format(**{name: table_map[name] for name in t.tables})
    values = [params[m.group(1)] for m in _PARAM.finditer(sql)]
    return _PARAM.sub("?", sql), values


def render_inline(template_id: str, params: dict, table_map: dict[str, str]) -> str:
    """SQL with quoted literals, for REST endpoints that take a plain SQL string."""
    sql, values = render(template_id, params, table_map)
    parts = sql.split("?")
    out = [parts[0]]
    for v, rest in zip(values, parts[1:]):
        if not isinstance(v, (str, int)) or isinstance(v, bool):
            raise TypeError("only str/int parameters are allowed")
        out.append(str(v) if isinstance(v, int) else "'" + v.replace("'", "''") + "'")
        out.append(rest)
    return "".join(out)
