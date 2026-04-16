import asyncio
import hashlib
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import requests
from rapidfuzz import fuzz

from utils import _normalize_text

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_LLM_BASE_URL = "http://ollama:11434"
DEFAULT_LOCAL_LLM_MODEL = "qwen2.5:14b-instruct"
DEFAULT_LOCAL_LLM_TIMEOUT_SECONDS = 90
DEFAULT_LOCAL_LLM_MAX_CONCURRENCY = 2

HIGH_RISK_DATASET_PATTERNS = (
    "ofac",
    "united nations",
    "hm treasury",
    "ofsi",
)


def get_local_llm_config() -> Dict[str, Any]:
    return {
        "base_url": os.environ.get("LOCAL_LLM_BASE_URL", DEFAULT_LOCAL_LLM_BASE_URL).strip() or DEFAULT_LOCAL_LLM_BASE_URL,
        "model": os.environ.get("LOCAL_LLM_MODEL", DEFAULT_LOCAL_LLM_MODEL).strip() or DEFAULT_LOCAL_LLM_MODEL,
        "timeout_seconds": max(10, int(os.environ.get("LOCAL_LLM_TIMEOUT_SECONDS", str(DEFAULT_LOCAL_LLM_TIMEOUT_SECONDS)))),
        "max_concurrency": max(1, int(os.environ.get("LOCAL_LLM_MAX_CONCURRENCY", str(DEFAULT_LOCAL_LLM_MAX_CONCURRENCY)))),
        "runtime": "ollama",
    }


def confidence_band(value: Optional[float]) -> str:
    raw = float(value or 0)
    if raw >= 0.9:
        return "0.90+"
    if raw >= 0.8:
        return "0.80-0.89"
    if raw >= 0.7:
        return "0.70-0.79"
    return "<0.70"


def screening_state_hash(result_json: Dict[str, Any]) -> str:
    payload = json.dumps(result_json or {}, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalized_similarity(left: str, right: str) -> int:
    ln = _normalize_text(left or "")
    rn = _normalize_text(right or "")
    if not ln or not rn:
        return 0
    return int(fuzz.token_set_ratio(ln, rn))


def _near_exact_match(left: str, right: str) -> bool:
    ln = _normalize_text(left or "")
    rn = _normalize_text(right or "")
    if not ln or not rn:
        return False
    if ln == rn:
        return True
    return fuzz.ratio(ln, rn) >= 95


def _is_high_risk_source(source_label: str) -> bool:
    source = (source_label or "").lower()
    return any(pattern in source for pattern in HIGH_RISK_DATASET_PATTERNS)


def _build_prompt(candidate: Dict[str, Any]) -> str:
    searched_type = candidate.get("entity_type") or "Unknown"
    matched_type = candidate.get("matched_entity_type") or "Unknown"
    context = {
        "searched_name": candidate.get("display_name"),
        "searched_entity_type": searched_type,
        "searched_date_of_birth": candidate.get("date_of_birth"),
        "searched_country": candidate.get("country_input"),
        "matched_name": candidate.get("matched_name"),
        "matched_entity_type": matched_type,
        "matched_birth_date": candidate.get("matched_birth_date"),
        "matched_country": candidate.get("matched_country"),
        "source": candidate.get("source_label"),
        "current_status": candidate.get("status"),
        "current_risk_level": candidate.get("risk_level"),
        "current_score": candidate.get("score"),
    }
    instructions = {
        "task": "Determine whether the searched entity and matched entity are likely the same real-world person or company.",
        "rules": [
            "Use common-sense entity resolution, not just token matching.",
            "Infer whether the searched entity looks more like a Person or Organization.",
            "If evidence is weak or ambiguous, return UNSURE.",
            "Return only JSON.",
        ],
        "output_schema": {
            "inferred_searched_entity_type": "Person | Organization",
            "same_entity_likelihood": "number between 0 and 1",
            "recommended_action": "CLEAR | INVESTIGATE | UNSURE",
            "confidence": "number between 0 and 1",
            "rationale_short": "short sentence",
            "reviewer_note": "short note for a human reviewer",
            "key_differences": ["short item", "short item"],
        },
    }
    return (
        "You are helping triage possible sanctions false positives.\n"
        f"Context:\n{json.dumps(context, ensure_ascii=True, sort_keys=True)}\n"
        f"Instructions:\n{json.dumps(instructions, ensure_ascii=True, sort_keys=True)}"
    )


def ollama_health() -> Dict[str, Any]:
    cfg = get_local_llm_config()
    try:
        response = requests.get(
            f"{cfg['base_url'].rstrip('/')}/api/tags",
            timeout=cfg["timeout_seconds"],
        )
        response.raise_for_status()
        data = response.json()
        models = [m.get("name") for m in data.get("models", []) if isinstance(m, dict)]
        return {
            "runtime": cfg["runtime"],
            "base_url": cfg["base_url"],
            "configured_model": cfg["model"],
            "reachable": True,
            "model_present": cfg["model"] in models,
            "available_models": models,
        }
    except Exception as e:
        return {
            "runtime": cfg["runtime"],
            "base_url": cfg["base_url"],
            "configured_model": cfg["model"],
            "reachable": False,
            "model_present": False,
            "available_models": [],
            "error": str(e),
        }


def _call_ollama_json(prompt: str) -> Dict[str, Any]:
    cfg = get_local_llm_config()
    response = requests.post(
        f"{cfg['base_url'].rstrip('/')}/api/generate",
        json={
            "model": cfg["model"],
            "prompt": prompt,
            "stream": False,
            "format": "json",
        },
        timeout=cfg["timeout_seconds"],
    )
    response.raise_for_status()
    payload = response.json()
    raw_response = payload.get("response") or "{}"
    data = json.loads(raw_response)
    if not isinstance(data, dict):
        raise ValueError("LLM returned non-object JSON")
    return data


def apply_guardrails(candidate: Dict[str, Any], raw_action: str) -> Tuple[str, bool, List[str]]:
    action = (raw_action or "UNSURE").strip().upper()
    reasons: List[str] = []
    if action not in {"CLEAR", "INVESTIGATE", "UNSURE"}:
        action = "UNSURE"

    searched_name = candidate.get("display_name") or ""
    matched_name = candidate.get("matched_name") or ""
    score = float(candidate.get("score") or 0)
    country = _normalize_text(candidate.get("country_input") or "")
    matched_country = _normalize_text(candidate.get("matched_country") or "")
    dob = str(candidate.get("date_of_birth") or "").strip()
    matched_dob = str(candidate.get("matched_birth_date") or "").strip()
    source = candidate.get("source_label") or ""
    similarity = _normalized_similarity(searched_name, matched_name)

    if action == "CLEAR":
        if score >= 88 and country and matched_country and country == matched_country:
            reasons.append("Strong name similarity combined with matching country")
        if dob and matched_dob and dob == matched_dob:
            reasons.append("Matching date of birth")
        if _near_exact_match(searched_name, matched_name):
            reasons.append("Exact or near-exact matched name")
        if _is_high_risk_source(source):
            reasons.append("Match originates from high-risk sanctions dataset")
        if score >= 92 or similarity >= 95:
            reasons.append("High-confidence identifier/name overlap")

    if reasons:
        return "INVESTIGATE", True, reasons
    return action, False, []


def triage_candidate_sync(candidate: Dict[str, Any]) -> Dict[str, Any]:
    cfg = get_local_llm_config()
    prompt = _build_prompt(candidate)
    raw_output = _call_ollama_json(prompt)
    raw_action = str(raw_output.get("recommended_action") or "UNSURE").strip().upper()
    raw_confidence = float(raw_output.get("confidence") or raw_output.get("same_entity_likelihood") or 0)
    effective_action, overridden, guardrail_reasons = apply_guardrails(candidate, raw_action)
    rationale = str(raw_output.get("rationale_short") or "").strip() or "No rationale provided."
    key_differences = raw_output.get("key_differences")
    if not isinstance(key_differences, list):
        key_differences = []
    reviewer_note = str(raw_output.get("reviewer_note") or "").strip()
    inferred_type = str(raw_output.get("inferred_searched_entity_type") or candidate.get("entity_type") or "Unknown").strip()
    return {
        "llm_runtime": cfg["runtime"],
        "llm_model": cfg["model"],
        "raw_recommended_action": raw_action if raw_action in {"CLEAR", "INVESTIGATE", "UNSURE"} else "UNSURE",
        "effective_recommended_action": effective_action,
        "ai_confidence_raw": raw_confidence,
        "ai_confidence_band": confidence_band(raw_confidence),
        "rationale_short": rationale[:500],
        "explanation_json": {
            "reviewer_note": reviewer_note[:1000],
            "key_differences": [str(item)[:300] for item in key_differences[:10]],
            "same_entity_likelihood": raw_output.get("same_entity_likelihood"),
            "inferred_searched_entity_type": inferred_type,
        },
        "guardrail_overridden": overridden,
        "guardrail_reasons": guardrail_reasons,
        "raw_output_json": raw_output,
    }


async def triage_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return await asyncio.to_thread(triage_candidate_sync, candidate)


async def run_ai_triage_batch(
    conn,
    *,
    screening_db_module,
    trigger_type: str,
    triggered_by: str,
    limit: int = 25,
) -> Dict[str, Any]:
    cfg = get_local_llm_config()
    run_id = await screening_db_module.create_ai_triage_run(
        conn,
        trigger_type=trigger_type,
        triggered_by=triggered_by,
        llm_runtime=cfg["runtime"],
        llm_model=cfg["model"],
        selected_count=0,
    )

    selected = 0
    created = 0
    skipped = 0
    superseded = 0
    error_count = 0
    errors: List[str] = []

    try:
        candidates = await screening_db_module.list_ai_triage_candidates(conn, limit=limit)
        selected = len(candidates)
        await screening_db_module.update_ai_triage_run_selected(conn, run_id=run_id, selected_count=selected)

        for candidate in candidates:
            screening_hash = screening_state_hash(candidate.get("result_json") or {})
            disposition = await screening_db_module.prepare_ai_triage_recommendation(
                conn,
                entity_key=str(candidate.get("entity_key") or ""),
                screening_state_hash=screening_hash,
            )
            if disposition == "skip":
                skipped += 1
                continue
            if disposition == "superseded":
                superseded += 1
            try:
                triage = await triage_candidate(candidate)
                await screening_db_module.insert_ai_triage_recommendation(
                    conn,
                    run_id=run_id,
                    entity_key=str(candidate.get("entity_key") or ""),
                    screening_state_hash=screening_hash,
                    candidate=candidate,
                    triage_result=triage,
                )
                created += 1
            except Exception as e:
                error_count += 1
                errors.append(f"{candidate.get('entity_key')}: {e}")
                logger.exception("AI triage failed entity_key=%s", candidate.get("entity_key"))
                await screening_db_module.insert_ai_triage_error(
                    conn,
                    run_id=run_id,
                    entity_key=str(candidate.get("entity_key") or ""),
                    screening_state_hash=screening_hash,
                    candidate=candidate,
                    error_message=str(e),
                    llm_runtime=cfg["runtime"],
                    llm_model=cfg["model"],
                )
        await screening_db_module.finalize_ai_triage_run(
            conn,
            run_id=run_id,
            status="completed",
            created_count=created,
            skipped_count=skipped,
            superseded_count=superseded,
            error_count=error_count,
            error_message="\n".join(errors[:20]) or None,
        )
        return {
            "run_id": run_id,
            "status": "completed",
            "selected_count": selected,
            "created_count": created,
            "skipped_count": skipped,
            "superseded_count": superseded,
            "error_count": error_count,
            "errors": errors[:20],
        }
    except Exception as e:
        await screening_db_module.finalize_ai_triage_run(
            conn,
            run_id=run_id,
            status="failed",
            created_count=created,
            skipped_count=skipped,
            superseded_count=superseded,
            error_count=max(1, error_count),
            error_message=str(e),
        )
        raise
