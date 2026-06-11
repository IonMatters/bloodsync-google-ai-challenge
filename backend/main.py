"""
BloodSync Emergency Transfusion Agent — FastAPI HTTP layer

Calls the ADK multi-agent pipeline (bloodsync_agent/agent.py) via InMemoryRunner.
Falls back to direct Gemini call if ADK runner fails.
"""

import asyncio
import os
import sys
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# ADK runner (preferred path)
# ---------------------------------------------------------------------------

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from google.adk.runners import InMemoryRunner
    from google.genai.types import Content, Part
    from bloodsync_agent.agent import (
        root_agent,
        check_abo_rh_compatibility,
        assess_transfusion_risk,
    )
    ADK_AVAILABLE = True
except Exception as _adk_err:
    print(f"[BloodSync] ADK import warning: {_adk_err} — falling back to direct Gemini")
    ADK_AVAILABLE = False

# ---------------------------------------------------------------------------
# Direct Gemini fallback (always available)
# ---------------------------------------------------------------------------

import google.generativeai as genai
from pymongo import MongoClient

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_GENAI_API_KEY", ""))
MONGODB_URI = os.getenv("MONGODB_URI", "")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    _gemini_model = genai.GenerativeModel("gemini-2.5-flash")
else:
    _gemini_model = None

_memory_store: list[dict] = []


def _get_collection():
    if MONGODB_URI:
        client = MongoClient(MONGODB_URI)
        return client["bloodsync"]["reports"]
    return None


# ---------------------------------------------------------------------------
# Shared compatibility/risk logic (mirrors bloodsync_agent/agent.py)
# These are called directly by the fallback path
# ---------------------------------------------------------------------------

ABO_COMPATIBILITY: dict[str, set[str]] = {
    "O":  {"O", "A", "B", "AB"},
    "A":  {"A", "AB"},
    "B":  {"B", "AB"},
    "AB": {"AB"},
}


def _parse_blood_type(s: str):
    s = s.strip()
    rh = None
    if s.endswith("+") or "positive" in s.lower():
        rh = "+"
        s = s.rstrip("+").lower().replace("positive", "").strip().upper()
    elif s.endswith("-") or "negative" in s.lower():
        rh = "-"
        s = s.rstrip("-").lower().replace("negative", "").strip().upper()
    return (s if s in {"O", "A", "B", "AB"} else None), rh


def _check_compat(patient_abo, patient_rh, donor_types):
    compatible, incompatible, emergency_fallback = [], [], []
    for donor in donor_types:
        d_abo, d_rh = _parse_blood_type(donor)
        if not d_abo:
            continue
        abo_ok = patient_abo.upper() in ABO_COMPATIBILITY.get(d_abo, set())
        rh_ok = (patient_rh == "+") or (d_rh == "-")
        if abo_ok and rh_ok:
            compatible.append(donor)
        elif d_abo == "O" and d_rh == "-":
            emergency_fallback.append(donor)
        else:
            incompatible.append(donor)
    return {
        "patient_blood_type": f"{patient_abo.upper()}{patient_rh}",
        "compatible": compatible,
        "incompatible": incompatible,
        "emergency_fallback": emergency_fallback,
    }


def _assess_risk(hb, scenario, compat):
    if hb < 6.0:
        band = "critical"
    elif hb < 8.0:
        band = "high"
    elif hb < 10.0:
        band = "moderate"
    else:
        band = "low"
    urgent = scenario.lower() in {"maternal haemorrhage", "trauma", "field emergency"}
    no_match = len(compat["compatible"]) == 0
    if band == "critical" or (urgent and band in {"high", "critical"}):
        level = "critical"
    elif band == "high" or (urgent and no_match):
        level = "high"
    elif band == "moderate":
        level = "moderate"
    else:
        level = "low"
    return {"risk_level": level, "hb_band": band}


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------

class TransfusionRequest(BaseModel):
    age: int
    sex: str
    scenario: str
    blood_group: str
    rh_factor: str
    hemoglobin: float
    donor_types: list[str]
    notes: Optional[str] = ""


class TransfusionResponse(BaseModel):
    report_id: str
    timestamp: str
    patient_blood_type: str
    risk_level: str
    compatible_donors: list[str]
    incompatible_donors: list[str]
    emergency_fallback: list[str]
    recommendation: str
    handoff_report: str
    disclaimer: str
    engine: str  # "adk" | "gemini-direct"


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="BloodSync Emergency Transfusion Agent",
    description=(
        "Multi-agent AI system for emergency blood compatibility assessment "
        "and transfusion decision support. Powered by Google ADK + Gemini."
    ),
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "BloodSync Agent",
        "engine": "adk" if ADK_AVAILABLE else "gemini-direct",
        "mongodb": bool(MONGODB_URI),
    }


@app.post("/api/assess", response_model=TransfusionResponse)
async def assess(req: TransfusionRequest):
    # Normalise Rh factor
    rh = req.rh_factor.strip()
    if rh.lower() in {"positive", "pos"}:
        rh = "+"
    elif rh.lower() in {"negative", "neg"}:
        rh = "-"

    report_id = str(uuid.uuid4())[:8].upper()
    timestamp = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Path A — ADK multi-agent pipeline
    # ------------------------------------------------------------------
    if ADK_AVAILABLE:
        try:
            result = await _run_adk_pipeline(req, rh, report_id, timestamp)
            result["engine"] = "adk"
            _persist(result, req, rh)
            return result
        except Exception as adk_err:
            print(f"[BloodSync] ADK pipeline error: {adk_err} — using fallback")

    # ------------------------------------------------------------------
    # Path B — Direct Gemini fallback
    # ------------------------------------------------------------------
    result = await _run_gemini_fallback(req, rh, report_id, timestamp)
    result["engine"] = "gemini-direct"
    _persist(result, req, rh)
    return result


async def _run_adk_pipeline(req, rh, report_id, timestamp) -> dict:
    """Run the full ADK six-agent pipeline via InMemoryRunner."""
    runner = InMemoryRunner(agent=root_agent, app_name="bloodsync")
    session = await runner.session_service.create_session(
        app_name="bloodsync",
        user_id="clinician",
    )

    prompt = _build_adk_prompt(req, rh, report_id, timestamp)
    user_message = Content(parts=[Part(text=prompt)], role="user")

    agent_texts: dict[str, str] = {}
    async for event in runner.run_async(
        user_id="clinician",
        session_id=session.id,
        new_message=user_message,
    ):
        author = getattr(event, "author", "") or ""
        if author and author != "audit_agent" and event.content and event.content.parts:
            for part in event.content.parts:
                t = getattr(part, "text", None)
                if t:
                    agent_texts[author] = t

    final_text = (
        agent_texts.get("handoff_agent")
        or agent_texts.get("reasoning_agent")
        or next(iter(reversed(list(agent_texts.values()))), "")
    )

    # Run deterministic checks independently so we have structured data
    compat = _check_compat(req.blood_group, rh, req.donor_types)
    risk = _assess_risk(req.hemoglobin, req.scenario, compat)

    return {
        "report_id": report_id,
        "timestamp": timestamp,
        "patient_blood_type": compat["patient_blood_type"],
        "risk_level": risk["risk_level"],
        "compatible_donors": compat["compatible"],
        "incompatible_donors": compat["incompatible"],
        "emergency_fallback": compat["emergency_fallback"],
        "recommendation": _extract_section(final_text, "RECOMMENDED ACTION")
                          or final_text[:600],
        "handoff_report": final_text,
        "disclaimer": (
            "This report is generated by an AI decision-support agent. "
            "The treating clinician retains full responsibility for the transfusion decision."
        ),
    }


async def _run_gemini_fallback(req, rh, report_id, timestamp) -> dict:
    """Direct Gemini call when ADK runner is unavailable."""
    compat = _check_compat(req.blood_group, rh, req.donor_types)
    risk = _assess_risk(req.hemoglobin, req.scenario, compat)

    SYSTEM = (
        "You are BloodSync, an AI clinical decision-support agent for emergency transfusion safety. "
        "Return JSON with keys: recommendation, handoff_report, disclaimer."
    )
    prompt = (
        f"Patient: {req.age}yo {req.sex}, scenario: {req.scenario}. "
        f"Blood type: {compat['patient_blood_type']}, Hb: {req.hemoglobin} g/dL. "
        f"Notes: {req.notes or 'none'}. "
        f"Compatible donors: {compat['compatible'] or 'none'}. "
        f"Incompatible: {compat['incompatible'] or 'none'}. "
        f"Fallback: {compat['emergency_fallback'] or 'none'}. "
        f"Risk: {risk['risk_level'].upper()}."
    )

    gemini_data = {
        "recommendation": "Apply standard emergency transfusion protocol.",
        "handoff_report": f"Risk: {risk['risk_level'].upper()}. Compatible: {compat['compatible']}.",
        "disclaimer": "AI decision support only. Clinician retains final responsibility.",
    }

    if _gemini_model:
        try:
            response = _gemini_model.generate_content([SYSTEM + "\n\n" + prompt])
            raw = response.text.strip().lstrip("```json").lstrip("```").rstrip("```")
            gemini_data = json.loads(raw)
        except Exception:
            pass

    return {
        "report_id": report_id,
        "timestamp": timestamp,
        "patient_blood_type": compat["patient_blood_type"],
        "risk_level": risk["risk_level"],
        "compatible_donors": compat["compatible"],
        "incompatible_donors": compat["incompatible"],
        "emergency_fallback": compat["emergency_fallback"],
        "recommendation": gemini_data.get("recommendation", ""),
        "handoff_report": gemini_data.get("handoff_report", ""),
        "disclaimer": gemini_data.get("disclaimer", ""),
    }


def _build_adk_prompt(req, rh, report_id, timestamp) -> str:
    return f"""BloodSync Emergency Transfusion Assessment Request
Report ID: {report_id}
Timestamp: {timestamp}

PATIENT DATA:
- Age: {req.age}, Sex: {req.sex}
- Clinical Scenario: {req.scenario}
- Blood Group: {req.blood_group}, Rh Factor: {rh}
- Haemoglobin: {req.hemoglobin} g/dL
- Available Donor Types: {', '.join(req.donor_types)}
- Clinical Notes: {req.notes or 'None provided'}

Please run the full BloodSync agent pipeline:
1. Intake Agent: validate inputs and generate report ID
2. Parallel Assessment: run compatibility check and risk assessment simultaneously
3. Clinical Reasoning Agent: synthesise results and produce a confidence-scored recommendation
4. Safety Review Agent: verify the recommendation does not conflict with compatibility data
5. Escalation Router: route the case based on risk level
6. Handoff Agent: produce the full structured handoff report
7. Audit Agent: store the complete session record
"""


def _extract_section(text: str, heading: str) -> str:
    """Extract a named section from the handoff report text."""
    lines = text.split("\n")
    capturing = False
    out = []
    for line in lines:
        if heading in line:
            capturing = True
            continue
        if capturing:
            if line.strip().startswith("===") or (
                line.strip().isupper() and len(line.strip()) > 5 and line.strip() != line
            ):
                break
            out.append(line)
    return "\n".join(out).strip()


def _persist(result: dict, req, rh: str) -> str:
    record = {
        **result,
        "input": {
            "age": req.age,
            "sex": req.sex,
            "scenario": req.scenario,
            "blood_group": req.blood_group,
            "rh_factor": rh,
            "hemoglobin": req.hemoglobin,
            "donor_types": req.donor_types,
            "notes": req.notes,
        },
    }
    col = _get_collection()
    if col is not None:
        col.insert_one(record)
        return "mongodb"
    else:
        _memory_store.append(record)
        return "memory"


@app.get("/api/reports")
def list_reports():
    col = _get_collection()
    if col is not None:
        docs = list(col.find({}, {"_id": 0}).sort("timestamp", -1).limit(20))
    else:
        docs = list(reversed(_memory_store[-20:]))
    return {"reports": docs}


# ---------------------------------------------------------------------------
# Streaming SSE endpoint — emits agent progress events in real time
# ---------------------------------------------------------------------------

_AGENT_LABELS: dict[str, str | None] = {
    "intake_agent":           "Intake Agent",
    "compatibility_agent":    "Compatibility Check",
    "risk_agent":             "Risk Assessment",
    "parallel_assessment":    None,          # orchestrator wrapper — skip
    "reasoning_agent":        "Clinical Reasoning",
    "review_agent":           "Safety Review",
    "escalation_router":      "Escalation Router",
    "handoff_agent":          "Handoff Report",
    "audit_agent":            "Audit Agent",
    "bloodsync_orchestrator": None,          # root orchestrator — skip
}


@app.post("/api/assess/stream")
async def assess_stream(req: TransfusionRequest):
    rh = req.rh_factor.strip()
    if rh.lower() in {"positive", "pos"}:
        rh = "+"
    elif rh.lower() in {"negative", "neg"}:
        rh = "-"

    report_id = str(uuid.uuid4())[:8].upper()
    timestamp = datetime.now(timezone.utc).isoformat()

    async def generate():
        def _sse(payload: dict) -> str:
            return f"data: {json.dumps(payload)}\n\n"

        current_agent: str | None = None
        last_text: dict[str, str] = {}

        try:
            if ADK_AVAILABLE:
                runner = InMemoryRunner(agent=root_agent, app_name="bloodsync")
                session = await runner.session_service.create_session(
                    app_name="bloodsync", user_id="clinician"
                )
                prompt = _build_adk_prompt(req, rh, report_id, timestamp)
                user_message = Content(parts=[Part(text=prompt)], role="user")

                final_text = ""

                async for event in runner.run_async(
                    user_id="clinician",
                    session_id=session.id,
                    new_message=user_message,
                ):
                    author = getattr(event, "author", None)
                    if not author:
                        continue
                    label = _AGENT_LABELS.get(author)
                    if label is None:
                        continue  # skip orchestrator wrappers

                    # Detect agent transition
                    if author != current_agent:
                        if current_agent and _AGENT_LABELS.get(current_agent):
                            yield _sse({
                                "type": "agent_complete",
                                "agent": current_agent,
                                "label": _AGENT_LABELS[current_agent],
                                "snippet": last_text.get(current_agent, "")[:140],
                            })
                        current_agent = author
                        yield _sse({"type": "agent_start", "agent": author, "label": label})

                    # Capture latest text from this agent
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            t = getattr(part, "text", None)
                            if t:
                                last_text[author] = t
                                if author != "audit_agent":
                                    final_text = t

                # Complete the last agent
                if current_agent and _AGENT_LABELS.get(current_agent):
                    yield _sse({
                        "type": "agent_complete",
                        "agent": current_agent,
                        "label": _AGENT_LABELS[current_agent],
                        "snippet": last_text.get(current_agent, "")[:140],
                    })

                compat = _check_compat(req.blood_group, rh, req.donor_types)
                risk   = _assess_risk(req.hemoglobin, req.scenario, compat)
                result = {
                    "report_id":          report_id,
                    "timestamp":          timestamp,
                    "patient_blood_type": compat["patient_blood_type"],
                    "risk_level":         risk["risk_level"],
                    "compatible_donors":  compat["compatible"],
                    "incompatible_donors": compat["incompatible"],
                    "emergency_fallback": compat["emergency_fallback"],
                    "recommendation":     _extract_section(final_text, "RECOMMENDED ACTION") or final_text[:600],
                    "handoff_report":     final_text,
                    "disclaimer": (
                        "This report is generated by an AI decision-support agent. "
                        "The treating clinician retains full responsibility for the transfusion decision."
                    ),
                    "engine": "adk",
                }
                storage = _persist(result, req, rh)
                result["db_saved"]   = (storage == "mongodb")
                result["db_storage"] = storage
                yield _sse({"type": "complete", "result": result})

            else:
                # No ADK — emit simulated step events then fallback result
                steps = list(k for k, v in _AGENT_LABELS.items() if v is not None)
                for step in steps:
                    yield _sse({"type": "agent_start",    "agent": step, "label": _AGENT_LABELS[step]})
                    await asyncio.sleep(0.2)
                    yield _sse({"type": "agent_complete", "agent": step, "label": _AGENT_LABELS[step], "snippet": ""})

                result = await _run_gemini_fallback(req, rh, report_id, timestamp)
                result["engine"] = "gemini-direct"
                storage = _persist(result, req, rh)
                result["db_saved"]   = (storage == "mongodb")
                result["db_storage"] = storage
                yield _sse({"type": "complete", "result": result})

        except Exception as exc:
            yield _sse({"type": "error", "message": str(exc)})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
