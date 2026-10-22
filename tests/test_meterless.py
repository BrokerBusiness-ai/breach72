"""Meterless World Model and H-MEM (Python implementations of the reference architecture)."""
import json
import math

import pytest

from app.adapters.meterless_hmem import HMem, embed, cosine, jaccard, tokenize
from app.adapters.meterless_world import (ContextBuilder, ProvenanceError, WorldModel, canonical_json,
                                          entity_id_from_name, fact_id, normalize_name)
from app.orchestration.graph import run_case

SRC = lambda at, by="t": {"kind": "agent", "by": by, "at": at}
CASE2 = {"incident_id": "INC-2026-002", "as_of": "2026-10-20T12:00:00+02:00", "organisation": "Org A"}
CASE2B = dict(CASE2, as_of="2026-10-21T12:00:00+02:00")


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)


# ---- World Model ---------------------------------------------------------
def test_stable_ids_follow_reference_grammar():
    assert normalize_name("  José  García​ ") == "jose garcia"
    assert entity_id_from_name("org", "Acme Corp") != entity_id_from_name("org", "Acme Corporation")
    assert entity_id_from_name("org", "ACME  corp") == entity_id_from_name("org", "acme corp")
    assert canonical_json({"b": 1, "a": [2, {"d": None, "c": 3}]}) == '{"a":[2,{"c":3}],"b":1}'
    assert fact_id("x", "p", {"a": 1, "b": 2}, "t", "agent") == fact_id("x", "p", {"b": 2, "a": 1}, "t", "agent")


def test_no_provenance_no_write():
    w = WorldModel("t")
    with pytest.raises(ProvenanceError):
        w.assert_fact("incident:x:1", "p", 1, source={"kind": "agent"})
    with pytest.raises(ProvenanceError):
        w.upsert_entity("incident", source=None, external_key=("x", "1"))
    assert w.events == []


def test_most_recent_fact_wins_and_old_one_is_kept():
    w = WorldModel("t")
    f1 = w.assert_fact("incident:x:1", "affected_count", 1200, SRC("2026-10-22T10:00:00+00:00", "investigator"))
    f2 = w.assert_fact("incident:x:1", "affected_count", 1450, SRC("2026-10-22T10:00:05+00:00", "evidence_auditor"))
    assert w.current("incident:x:1", "affected_count")["id"] == f2["id"]
    assert w.facts[f1["id"]]["supersededBy"] == f2["id"]
    assert [f["value"] for f in w.history("incident:x:1", "affected_count")] == [1200, 1450]
    assert w.entities["incident:x:1"]["attrs"]["affected_count"] == 1450
    # identical assertion is a content-addressed no-op
    n = len(w.events)
    w.assert_fact("incident:x:1", "affected_count", 1450, SRC("2026-10-22T10:00:05+00:00", "evidence_auditor"))
    assert len(w.events) == n
    # time travel: the state before the auditor's correction
    assert w.fold_until("2026-10-22T10:00:01+00:00").current("incident:x:1", "affected_count")["value"] == 1200


def test_local_store_replays_to_identical_state(tmp_path):
    w = WorldModel("ns", storage="local", directory=tmp_path)
    w.upsert_entity("system", SRC("2026-10-22T10:00:00+00:00"), ("breach72", "SYS-1"), name="SYS-1")
    w.relate("incident:breach72:I1", "affects", "system:breach72:SYS-1", SRC("2026-10-22T10:00:01+00:00"))
    w.assert_fact("incident:breach72:I1", "disclosure", {"status": "verified"}, SRC("2026-10-22T10:00:02+00:00"))
    again = WorldModel("ns", storage="local", directory=tmp_path)
    assert again.stats() == w.stats()
    assert canonical_json(again.facts) == canonical_json(w.facts)
    assert [e["id"] for e in again.neighbors("system:breach72:SYS-1")] == ["incident:breach72:I1"]


# ---- H-MEM -----------------------------------------------------------------
def test_hmem_rejects_record_without_source():
    h = HMem(None, lambda: 0)
    with pytest.raises(ValueError):
        h.store.add("fact", source=" ")


def test_hmem_retrieval_uses_canonical_formula():
    now = 10 * 86_400_000
    h = HMem(None, lambda: now)
    m = h.store.add("Asset security register for system SYS-HR-SRV: encryption_at_rest=none; backup=daily",
                    source="s", type_="factual", tags=["system:SYS-HR-SRV"], confidence=0.7)
    q = "SYS-HR-SRV asset security encryption backup"
    qt = tokenize(q)[:8]
    expected = (0.35 * max(0.0, min(1.0, cosine(embed(q), m["embedding"]))) + 0.2 * jaccard(qt, tokenize(m["content"]))
                + 0.1 * jaccard(qt, ["system:sys-hr-srv"]) + 0.1 * 1.0
                + 0.15 * jaccard(h.store.extract_entities(q), m["entities"]) + 0.05 * 0.8 + 0.05 * math.exp(0))
    res = h.retrieval.query(q, threshold=0.0)
    assert res["memories"][0]["relevance"] == round(min(1.0, expected) * 0.7, 4)
    # superseded records are penalised by 0.20 before the confidence multiplier, not removed
    h.store.update(m["id"], {"supersededBy": "mem-99"})
    assert h.retrieval.query(q, threshold=0.0)["memories"][0]["relevance"] < res["memories"][0]["relevance"]


def test_hmem_feedback_deltas_and_ledger(tmp_path):
    h = HMem(tmp_path, lambda: 1)
    m = h.store.add("x fact", source="s", confidence=0.7)
    h.retrieval.feedback(m["id"], "helpful")
    assert h.store.get(m["id"])["confidence"] == pytest.approx(0.75)
    h.retrieval.feedback(m["id"], "wrong")
    assert h.store.get(m["id"])["confidence"] == pytest.approx(0.55) and "review" in h.store.get(m["id"])["tags"]
    lines = [json.loads(x) for x in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert [e["action"] for e in lines] == ["create", "feedback", "feedback"]
    assert HMem(tmp_path, lambda: 1).store.get(m["id"])["confidence"] == pytest.approx(0.55)


# ---- pipeline integration --------------------------------------------------
def test_findings_live_in_world_model(tmp_path):
    out = run_case(CASE2, backend="dev", log_dir=tmp_path)
    wm = out["world_model"]
    assert wm["facts"] >= len(out["findings"]) and wm["facts_superseded"] >= 1   # auditor status updates
    saved = WorldModel(out["run_id"], storage="local", directory=tmp_path)
    cur = saved.current("incident:breach72:INC-2026-002", "affected_count")
    assert cur["value"]["status"] == "verified" and cur["value"]["verified_by"] == "T04_EXPORT_ITEMS_COUNT"
    assert cur["source"]["by"] == "evidence_auditor"
    first = saved.history("incident:breach72:INC-2026-002", "affected_count")[0]
    assert first["value"]["status"] == "proposed" and first["source"]["by"] == "investigator"
    assert "system:breach72:SYS-SOCIAL-REG" in saved.entities and "processing:breach72:PR-07" in saved.entities
    ctx = ContextBuilder(saved).build("incident:breach72:INC-2026-002")["text"]
    assert "T04_EXPORT_ITEMS_COUNT" in ctx and "affected_count [verified]" in ctx


def test_org_memory_is_learned_then_recalled_and_reverified(tmp_path, monkeypatch):
    monkeypatch.setenv("HMEM_DIR", str(tmp_path / "mem"))
    first = run_case(CASE2, backend="dev", log_dir=tmp_path)
    assert first["org_memory"]["recalled"] == [] and len(first["org_memory"]["learned"]) >= 2
    second = run_case(CASE2B, backend="dev", log_dir=tmp_path)
    om = second["org_memory"]
    assert om["recalled"] and all(r["outcome"] == "confirmed" for r in om["reverified"])
    assert om["learned"] == []                       # nothing new: confirmed facts are not duplicated
    assert any(f["claim"].startswith("org_memory:") and f["evidence"] for f in second["findings"])
    assert second["decision"] == "notify_authority"  # memory never changes the rule outcome
    h = HMem(tmp_path / "mem" / "org-a", lambda: 0)
    assert all(h.store.get(r["memory"])["layer"] == "long_term" for r in om["reverified"])


def test_outdated_memory_is_corrected_from_source(tmp_path, monkeypatch):
    monkeypatch.setenv("HMEM_DIR", str(tmp_path / "mem"))
    run_case(CASE2, backend="dev", log_dir=tmp_path)
    state = tmp_path / "mem" / "org-a" / "state.json"
    data = json.loads(state.read_text())
    victim = next(r for r in data["records"] if "template:T06_ASSET_SECURITY" in r["tags"])
    victim["provenance"]["refs"][2] = "0" * 16       # simulate the register having changed since
    state.write_text(json.dumps(data))
    out = run_case(CASE2B, backend="dev", log_dir=tmp_path)
    rv = next(r for r in out["org_memory"]["reverified"] if r["memory"] == victim["id"])
    assert rv["outcome"] == "outdated" and rv["replacement"]
    h = HMem(tmp_path / "mem" / "org-a", lambda: 0)
    old = h.store.get(victim["id"])
    assert old["supersededBy"] == rv["replacement"] and old["confidence"] == pytest.approx(0.5)
    assert "conflict_resolved" in h.ledger.stats()
