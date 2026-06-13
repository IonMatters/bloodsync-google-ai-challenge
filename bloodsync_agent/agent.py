"""
BloodSync Emergency Transfusion Agent — ADK Multi-Agent Implementation

Architecture:
  root_agent (SequentialAgent)
    ├── intake_agent              — validates inputs, generates report ID
    ├── parallel_assessment       — ParallelAgent (runs simultaneously):
    │     ├── compatibility_agent —   deterministic ABO/Rh check
    │     └── risk_agent          —   haemoglobin + scenario risk scoring
    ├── reasoning_agent           — Gemini clinical synthesis + confidence score
    ├── review_agent              — hallucination guardrail (checks AI vs rules)
    ├── escalation_router         — autonomous routing by risk level
    ├── handoff_agent             — structured clinical transfer report
    └── audit_agent               — persists session to MongoDB via direct pymongo

Agent communication: output_key session state (not conversational recall).
Temperature: 0.0 for tool-calling agents, 0.2 for synthesis agents.
"""

import os
import uuid
from datetime import datetime, timezone

from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent
from google.genai import types

# ---------------------------------------------------------------------------
# ABO / Rh compatibility rules (deterministic — no AI involvement)
# ---------------------------------------------------------------------------

ABO_COMPATIBILITY: dict[str, set[str]] = {
    "O":  {"O", "A", "B", "AB"},
    "A":  {"A", "AB"},
    "B":  {"B", "AB"},
    "AB": {"AB"},
}


def _parse_blood_type(s: str) -> tuple[str | None, str | None]:
    s = s.strip()
    rh = None
    if s.endswith("+") or "positive" in s.lower():
        rh = "+"
        s = s.rstrip("+").lower().replace("positive", "").strip().upper()
    elif s.endswith("-") or "negative" in s.lower():
        rh = "-"
        s = s.rstrip("-").lower().replace("negative", "").strip().upper()
    return (s if s in {"O", "A", "B", "AB"} else None), rh


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------


def validate_patient_inputs(
    age: int,
    sex: str,
    blood_group: str,
    rh_factor: str,
    hemoglobin: float,
    clinical_scenario: str,
    donor_types: list[str],
) -> dict:
    """Strictly validate all patient input fields before the pipeline runs.

    Args:
        age: Patient age in years.
        sex: Patient sex — Male or Female.
        blood_group: ABO group — O, A, B, or AB.
        rh_factor: Rh factor — '+' or '-' (or 'positive'/'negative').
        hemoglobin: Haemoglobin level in g/dL.
        clinical_scenario: Clinical context string.
        donor_types: List of donor blood type strings.

    Returns:
        dict with keys: valid (bool), errors (list[str]), normalised_inputs (dict).
    """
    errors = []

    if not isinstance(age, int) or not (0 < age < 130):
        errors.append(f"Age '{age}' is invalid — must be an integer between 1 and 129.")

    if sex.strip().lower() not in {"male", "female", "m", "f"}:
        errors.append(f"Sex '{sex}' is not recognised — expected Male or Female.")

    normalised_abo = blood_group.strip().upper()
    if normalised_abo not in {"O", "A", "B", "AB"}:
        errors.append(f"Blood group '{blood_group}' is invalid — must be O, A, B, or AB.")

    rh_lower = rh_factor.strip().lower()
    if rh_lower in {"+", "positive"}:
        normalised_rh = "+"
    elif rh_lower in {"-", "negative"}:
        normalised_rh = "-"
    else:
        normalised_rh = None
        errors.append(f"Rh factor '{rh_factor}' is invalid — must be '+' or '-'.")

    if not isinstance(hemoglobin, (int, float)) or not (0.5 <= float(hemoglobin) <= 25.0):
        errors.append(
            f"Haemoglobin '{hemoglobin}' is out of physiological range — expected 0.5–25.0 g/dL."
        )

    if not clinical_scenario or len(clinical_scenario.strip()) < 3:
        errors.append("Clinical scenario is missing or too vague.")

    if not donor_types:
        errors.append("At least one donor blood type must be provided.")
    else:
        for donor in donor_types:
            abo, rh = _parse_blood_type(donor)
            if abo is None or rh is None:
                errors.append(
                    f"Donor type '{donor}' could not be parsed — use format like 'O negative' or 'A positive'."
                )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "normalised_inputs": {
            "blood_group": normalised_abo,
            "rh_factor": normalised_rh,
            "hemoglobin_gdl": float(hemoglobin),
        } if not errors else {},
    }


def generate_report_id() -> dict:
    """Generate a unique report identifier and UTC timestamp for the session.

    Returns:
        dict with keys: report_id, timestamp.
    """
    return {
        "report_id": str(uuid.uuid4())[:8].upper(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def check_abo_rh_compatibility(
    patient_abo: str,
    patient_rh: str,
    donor_types: list[str],
) -> dict:
    """Check ABO/Rh blood compatibility between a patient and available donor units.

    Args:
        patient_abo: Patient ABO group — one of: O, A, B, AB.
        patient_rh: Patient Rh factor — '+' for positive, '-' for negative.
        donor_types: List of available donor blood type strings, e.g. ['O negative', 'A positive'].

    Returns:
        dict with keys: patient_blood_type, compatible, incompatible, emergency_fallback.
    """
    compatible, incompatible, emergency_fallback = [], [], []

    for donor in donor_types:
        d_abo, d_rh = _parse_blood_type(donor)
        if d_abo is None:
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


def assess_transfusion_risk(
    hemoglobin_gdl: float,
    clinical_scenario: str,
    compatible_donor_count: int,
    emergency_fallback_available: bool,
) -> dict:
    """Assess the urgency and risk level of a transfusion decision.

    Args:
        hemoglobin_gdl: Patient haemoglobin in g/dL.
        clinical_scenario: Clinical context, e.g. 'Maternal Haemorrhage', 'Trauma'.
        compatible_donor_count: Number of fully compatible donor units available.
        emergency_fallback_available: Whether O-negative emergency fallback is available.

    Returns:
        dict with keys: risk_level (critical/high/moderate/low), hb_band, rationale.
    """
    if hemoglobin_gdl < 6.0:
        hb_band = "critical"
    elif hemoglobin_gdl < 8.0:
        hb_band = "high"
    elif hemoglobin_gdl < 10.0:
        hb_band = "moderate"
    else:
        hb_band = "low"

    high_urgency_scenarios = {
        "maternal haemorrhage", "trauma", "field emergency", "disaster"
    }
    scenario_urgent = clinical_scenario.lower() in high_urgency_scenarios
    no_ideal_match = compatible_donor_count == 0

    if hb_band == "critical" or (scenario_urgent and hb_band in {"high", "critical"}):
        risk_level = "critical"
    elif hb_band == "high" or (scenario_urgent and no_ideal_match):
        risk_level = "high"
    elif hb_band == "moderate":
        risk_level = "moderate"
    else:
        risk_level = "low"

    rationale = (
        f"Hb {hemoglobin_gdl} g/dL ({hb_band} band). "
        f"Scenario: {clinical_scenario}. "
        f"Compatible units available: {compatible_donor_count}. "
        f"O-neg fallback: {'yes' if emergency_fallback_available else 'no'}."
    )

    return {
        "risk_level": risk_level,
        "hb_band": hb_band,
        "rationale": rationale,
    }


def calculate_confidence_score(
    all_fields_present: bool,
    compatible_donor_count: int,
    risk_level: str,
    emergency_fallback_available: bool,
) -> dict:
    """Calculate a deterministic confidence score for the clinical recommendation.

    Args:
        all_fields_present: Whether all required patient fields were supplied.
        compatible_donor_count: Number of fully compatible donor units found.
        risk_level: Risk level string — critical, high, moderate, or low.
        emergency_fallback_available: Whether O-negative fallback is available.

    Returns:
        dict with keys: confidence_score (0-100), confidence_band, factors.
    """
    score = 0
    factors = []

    if all_fields_present:
        score += 25
        factors.append("Complete patient data provided (+25)")
    else:
        factors.append("Incomplete patient data — confidence reduced (+0)")

    if compatible_donor_count > 1:
        score += 30
        factors.append(f"{compatible_donor_count} compatible donor units identified (+30)")
    elif compatible_donor_count == 1:
        score += 22
        factors.append("1 compatible donor unit identified (+22)")
    elif emergency_fallback_available:
        score += 10
        factors.append("No ideal match — O-neg fallback available (+10)")
    else:
        factors.append("No compatible donors found — confidence critically low (+0)")

    if risk_level in {"critical", "high"}:
        score += 25
        factors.append(f"Risk level '{risk_level}' — unambiguous threshold crossed (+25)")
    elif risk_level == "moderate":
        score += 18
        factors.append("Risk level 'moderate' — within normal range (+18)")
    else:
        score += 12
        factors.append("Risk level 'low' (+12)")

    if compatible_donor_count > 0 or emergency_fallback_available:
        score += 20
        factors.append("Transfusion pathway is available (+20)")
    else:
        factors.append("No transfusion pathway available — human escalation mandatory (+0)")

    final_score = min(score, 100)
    if final_score >= 80:
        band = "HIGH"
    elif final_score >= 55:
        band = "MEDIUM"
    else:
        band = "LOW"

    return {
        "confidence_score": final_score,
        "confidence_band": band,
        "factors": factors,
    }


def check_recommendation_consistency(
    recommendation_text: str,
    compatible_donors: list[str],
    incompatible_donors: list[str],
) -> dict:
    """Safety check: detect if the clinical recommendation contradicts compatibility results.

    Flags any case where an incompatible donor appears to be positively recommended,
    or where compatible donors are entirely absent from the recommendation.

    Args:
        recommendation_text: The full text of the Gemini clinical recommendation.
        compatible_donors: List of donors confirmed safe by the compatibility tool.
        incompatible_donors: List of donors confirmed unsafe by the compatibility tool.

    Returns:
        dict with keys: safe (bool), conflicts (list[str]), verdict.
    """
    def _norm(s: str) -> str:
        # Normalise hyphen/space variants so "O-negative" == "O negative"
        return s.lower().replace("-", " ").replace("  ", " ").strip()

    conflicts = []
    rec_norm = _norm(recommendation_text)

    negation_terms = [
        "not", "must not", "do not", "avoid", "incompatible",
        "unsafe", "contraindicated", "should not", "cannot", "no ",
        "must avoid", "never", "prohibited",
    ]

    # Flag only if an incompatible donor appears with NO negation in a ±100-char window
    for donor in incompatible_donors:
        donor_norm = _norm(donor)
        if donor_norm in rec_norm:
            idx = rec_norm.find(donor_norm)
            window = rec_norm[max(0, idx - 100): idx + len(donor_norm) + 100]
            negated = any(term in window for term in negation_terms)
            if not negated:
                conflicts.append(
                    f"CONFLICT: Incompatible donor '{donor}' appears in recommendation "
                    f"without a clear negation — manual review required."
                )

    # Flag only if compatible donors exist but NONE appear (normalised) in the recommendation
    if compatible_donors:
        any_mentioned = any(_norm(d) in rec_norm for d in compatible_donors)
        if not any_mentioned:
            conflicts.append(
                f"CONFLICT: Compatible donors {compatible_donors} were identified but "
                f"none appear in the recommendation — recommendation may be incomplete."
            )

    safe = len(conflicts) == 0
    verdict = (
        "SAFETY CLEARED — recommendation is consistent with compatibility results."
        if safe else
        "SAFETY ADVISORY — potential inconsistency detected. Clinician must verify "
        "the recommendation against compatibility data before proceeding."
    )

    return {
        "safe": safe,
        "conflicts": conflicts,
        "verdict": verdict,
    }


def trigger_escalation(
    risk_level: str,
    report_id: str,
    patient_summary: str,
) -> dict:
    """Autonomously route the case based on risk level and trigger appropriate escalation.

    Args:
        risk_level: Risk level from the risk assessment — critical, high, moderate, or low.
        report_id: Unique session report identifier.
        patient_summary: Brief patient description for the alert message.

    Returns:
        dict with keys: escalation_action, alert_message, routing_channel, priority_level.
    """
    routing = {
        "critical": {
            "escalation_action": "IMMEDIATE_ESCALATION",
            "alert_message": (
                f"[CRITICAL ALERT] Report {report_id} — {patient_summary}. "
                "Activate rapid transfusion protocol NOW. "
                "Notify on-call haematologist immediately. "
                "Do not delay pending further assessment."
            ),
            "routing_channel": "emergency_pager",
            "priority_level": 1,
        },
        "high": {
            "escalation_action": "URGENT_NOTIFICATION",
            "alert_message": (
                f"[URGENT] Report {report_id} — {patient_summary}. "
                "High-risk transfusion case. Haematology consultation required within 30 minutes."
            ),
            "routing_channel": "urgent_queue",
            "priority_level": 2,
        },
        "moderate": {
            "escalation_action": "STANDARD_HANDOFF",
            "alert_message": (
                f"[STANDARD] Report {report_id} — {patient_summary}. "
                "Proceed with planned transfusion assessment. Clinician review required."
            ),
            "routing_channel": "standard_queue",
            "priority_level": 3,
        },
        "low": {
            "escalation_action": "ROUTINE_LOG",
            "alert_message": (
                f"[ROUTINE] Report {report_id} — {patient_summary}. "
                "Low urgency. Log for audit. Standard monitoring recommended."
            ),
            "routing_channel": "routine_log",
            "priority_level": 4,
        },
    }

    level = risk_level.strip().lower()
    result = routing.get(level, routing["moderate"])
    result["routed_at"] = datetime.now(timezone.utc).isoformat()
    return result


# ---------------------------------------------------------------------------
# MongoDB audit tool — direct pymongo (no MCP/npx dependency)
# ---------------------------------------------------------------------------

def save_audit_record(
    report_id: str,
    timestamp: str,
    patient_inputs: str,
    compatibility_result: str,
    risk_assessment: str,
    clinical_recommendation: str,
    safety_review: str,
    escalation_decision: str,
    handoff_report: str,
) -> dict:
    """Persist the complete session audit record to MongoDB Atlas.

    Args:
        report_id: Unique report identifier from the session.
        timestamp: UTC timestamp of the session.
        patient_inputs: Full patient data from the intake summary.
        compatibility_result: ABO/Rh compatibility output.
        risk_assessment: Risk level and rationale.
        clinical_recommendation: Gemini recommendation with confidence score.
        safety_review: Safety review verdict and any conflicts.
        escalation_decision: Escalation routing decision and alert.
        handoff_report: Full structured clinical handoff report.

    Returns:
        dict with keys: stored (bool), report_id, message.
    """
    mongodb_uri = os.getenv("MONGODB_URI", "")
    if not mongodb_uri:
        return {
            "stored": False,
            "report_id": report_id,
            "message": "MongoDB not configured — record preserved in session output only.",
        }

    try:
        from pymongo import MongoClient
        client = MongoClient(mongodb_uri, serverSelectionTimeoutMS=8000)
        collection = client["bloodsync"]["reports"]
        collection.insert_one({
            "report_id":              report_id,
            "timestamp":              timestamp,
            "patient_inputs":         patient_inputs,
            "compatibility_result":   compatibility_result,
            "risk_assessment":        risk_assessment,
            "clinical_recommendation": clinical_recommendation,
            "safety_review":          safety_review,
            "escalation_decision":    escalation_decision,
            "handoff_report":         handoff_report,
        })
        client.close()
        return {
            "stored": True,
            "report_id": report_id,
            "message": f"Audit record {report_id} saved to bloodsync.reports.",
        }
    except Exception as exc:
        return {
            "stored": False,
            "report_id": report_id,
            "message": f"MongoDB write failed: {exc}",
        }


# ---------------------------------------------------------------------------
# Shared generation configs
# ---------------------------------------------------------------------------

_DETERMINISTIC_CONFIG = types.GenerateContentConfig(temperature=0.0)
_SYNTHESIS_CONFIG = types.GenerateContentConfig(temperature=0.2)

_GEMINI_MODEL = "gemini-2.5-flash"

# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

# 1. Intake Agent
intake_agent = LlmAgent(
    name="intake_agent",
    model=_GEMINI_MODEL,
    description="Validates and structures patient demographic and clinical input.",
    instruction="""You are the BloodSync Intake Agent.

STEP 1 — Validation (mandatory first):
Call `validate_patient_inputs` with every field from the user message.
If `valid` is false, STOP and report each error. Do NOT proceed.

STEP 2 — Report ID (only if validation passes):
Call `generate_report_id`.

STEP 3 — Output this block exactly, filling values from the validated input and tools:

---
INTAKE CONFIRMED
Report ID: <report_id>
Timestamp: <timestamp>
Patient: <age>-year-old <sex>
Scenario: <clinical_scenario>
Blood Type: <blood_group> <rh_factor>
Haemoglobin: <hemoglobin> g/dL
Available Donors: <comma-separated donor list>
Notes: <notes or 'None'>
Status: READY FOR PARALLEL ASSESSMENT
---

Do not add clinical commentary or recommendations.""",
    tools=[validate_patient_inputs, generate_report_id],
    output_key="intake_summary",
    generate_content_config=_DETERMINISTIC_CONFIG,
)

# 2a. Compatibility Agent (runs in parallel with risk_agent)
compatibility_agent = LlmAgent(
    name="compatibility_agent",
    model=_GEMINI_MODEL,
    description="Runs deterministic ABO/Rh blood compatibility checks.",
    instruction="""You are the BloodSync Compatibility Agent.

You MUST call `check_abo_rh_compatibility` before producing any output.
Never state compatibility conclusions from your own knowledge.
Report the tool result verbatim — do NOT reclassify any donor.

Output format:
---
COMPATIBILITY RESULT
Patient Blood Type: <patient_blood_type>
Compatible Donors: <list or 'None'>
Incompatible Donors: <list or 'None — must NOT be used'>
Emergency Fallback (O-neg): <list or 'None available'>
---""",
    tools=[check_abo_rh_compatibility],
    output_key="compatibility_result",
    generate_content_config=_DETERMINISTIC_CONFIG,
)

# 2b. Risk Assessment Agent (runs in parallel with compatibility_agent)
risk_agent = LlmAgent(
    name="risk_agent",
    model=_GEMINI_MODEL,
    description="Evaluates haemoglobin level and clinical scenario to assign transfusion urgency.",
    instruction="""You are the BloodSync Risk Assessment Agent.

You MUST call `assess_transfusion_risk` before producing any output.
Never assign a risk level from your own judgement.
Report the risk_level and rationale EXACTLY as the tool returns them.
Do NOT soften, upgrade, or reinterpret the risk level.

Output format:
---
RISK ASSESSMENT
Risk Level: <CRITICAL / HIGH / MODERATE / LOW>
Haemoglobin Band: <hb_band>
Rationale: <rationale from tool>
---""",
    tools=[assess_transfusion_risk],
    output_key="risk_assessment",
    generate_content_config=_DETERMINISTIC_CONFIG,
)

# Parallel wrapper — runs compatibility and risk simultaneously
parallel_assessment = ParallelAgent(
    name="parallel_assessment",
    description="Runs ABO/Rh compatibility check and transfusion risk assessment simultaneously.",
    sub_agents=[compatibility_agent, risk_agent],
)

# 3. Clinical Reasoning Agent — synthesis + confidence scoring
reasoning_agent = LlmAgent(
    name="reasoning_agent",
    model=_GEMINI_MODEL,
    description="Synthesises compatibility and risk data into a clinical recommendation with confidence score.",
    instruction="""You are the BloodSync Clinical Reasoning Agent, powered by Gemini.

You have no tools available. Do not attempt to call any tools or functions.

Your inputs are the Compatibility Result and Risk Assessment from earlier in this session.
Produce a clinical recommendation of 3–5 sentences, then a confidence score.

GUARDRAILS:
- Reference ONLY values that appear in the Compatibility Result and Risk Assessment.
  Do not introduce blood type facts or clinical rules from your own knowledge.
- Do not invent, add, or reclassify any donor beyond those listed.
- Use the exact risk level from the Risk Assessment — do not soften or upgrade it.
- If uncertain about any clinical fact, do not state it.

Your recommendation must:
1. State the risk level and its immediate clinical significance.
2. Name exactly which donor(s) to use and which must NOT be used.
3. State the immediate next clinical action.
4. Note O-negative fallback availability if relevant.
5. Close with: "The final transfusion decision must be confirmed by the treating
   clinician or haematologist."

Then call `calculate_confidence_score` with:
- all_fields_present: true if intake had no validation errors
- compatible_donor_count: number of compatible donors from Compatibility Result
- risk_level: exact risk level from Risk Assessment
- emergency_fallback_available: true if emergency fallback list is non-empty

Append to your output:
---
CONFIDENCE SCORE: <score>/100 (<band>)
BASIS: <factors from tool, comma-separated>
---""",
    tools=[calculate_confidence_score],
    output_key="clinical_recommendation",
    generate_content_config=_SYNTHESIS_CONFIG,
)

# 4. Review Agent — disagreement detection / AI safety guardrail
review_agent = LlmAgent(
    name="review_agent",
    model=_GEMINI_MODEL,
    description="Safety guardrail: detects conflicts between Gemini recommendation and deterministic compatibility results.",
    instruction="""You are the BloodSync Safety Review Agent.

You have one job: call `check_recommendation_consistency` and report the result.

Extract from the session context:
- recommendation_text: the full clinical recommendation from the Reasoning Agent
- compatible_donors: the compatible donor list from the Compatibility Result
- incompatible_donors: the incompatible donor list from the Compatibility Result

Call the tool with these three values exactly as they appear — do not paraphrase.

Output format:
---
SAFETY REVIEW
Verdict: <verdict from tool>
Conflicts detected: <number>
<list each conflict if any, or 'None'>
Pipeline status: <CLEARED TO PROCEED or SAFETY ADVISORY — CLINICIAN VERIFICATION REQUIRED>
---""",
    tools=[check_recommendation_consistency],
    output_key="safety_review",
    generate_content_config=_DETERMINISTIC_CONFIG,
)

# 5. Escalation Router — autonomous routing based on risk level
escalation_router = LlmAgent(
    name="escalation_router",
    model=_GEMINI_MODEL,
    description="Autonomously routes the case and triggers the appropriate escalation action based on risk level.",
    instruction="""You are the BloodSync Escalation Router.

You have one job: call `trigger_escalation` and report the routing decision.

Extract from the session context:
- risk_level: the exact risk level from the Risk Assessment (critical/high/moderate/low)
- report_id: from the Intake Summary
- patient_summary: a one-line summary of the patient (age, sex, scenario, blood type)

Call `trigger_escalation` with these values. Do not modify or reinterpret the risk level.

Output format:
---
ESCALATION ROUTING
Action: <escalation_action>
Priority: <priority_level>
Channel: <routing_channel>
Alert: <alert_message>
Routed at: <routed_at>
---""",
    tools=[trigger_escalation],
    output_key="escalation_decision",
    generate_content_config=_DETERMINISTIC_CONFIG,
)

# 6. Handoff Agent — structured clinical transfer report
handoff_agent = LlmAgent(
    name="handoff_agent",
    model=_GEMINI_MODEL,
    description="Produces a structured clinical handoff report for the receiving clinician or lab team.",
    instruction="""You are the BloodSync Handoff Agent.

You have no tools available. Do not attempt to call any tools or functions.

Always produce the full report below — never withhold it. In an emergency, clinicians
need all available information. If the Safety Review shows a SAFETY ADVISORY, include
it prominently in the SAFETY REVIEW STATUS section so the clinician is aware.

Copy all values EXACTLY from prior agents.
Do not paraphrase clinical findings. Do not leave any section blank.

---
BLOODSYNC CLINICAL HANDOFF REPORT
==================================
Report ID: [from Intake Summary]
Timestamp: [from Intake Summary]
Generated by: BloodSync Emergency Transfusion Agent (Ion Matters HealthTech)

PATIENT SUMMARY
Age / Sex: [from Intake Summary]
Clinical Scenario: [from Intake Summary]
Blood Type: [from Intake Summary]
Haemoglobin: [from Intake Summary] g/dL
Notes: [from Intake Summary]

COMPATIBILITY ASSESSMENT
Compatible Donors: [exact list from Compatibility Result]
Incompatible Donors: [exact list — must NOT be used]
Emergency Fallback (O-neg): [from Compatibility Result]

RISK LEVEL: [exact level — CRITICAL / HIGH / MODERATE / LOW]
Risk Rationale: [exact rationale from Risk Assessment]

ESCALATION STATUS
Action: [from Escalation Routing]
Channel: [from Escalation Routing]

RECOMMENDED ACTION
[Clinical Recommendation — copy verbatim, including confidence score]

SAFETY REVIEW STATUS
[Verdict from Safety Review]

DISCLAIMER
This report is generated by an AI decision-support agent and is intended to assist,
not replace, clinical judgement. The treating clinician or haematologist retains full
responsibility for the final transfusion decision. Do not administer blood products
solely on the basis of this report.
==================================
---""",
    tools=[],
    output_key="handoff_report",
    generate_content_config=_SYNTHESIS_CONFIG,
)

# 7. Audit Agent — persists to MongoDB Atlas via direct pymongo tool
audit_agent = LlmAgent(
    name="audit_agent",
    model=_GEMINI_MODEL,
    description="Persists the complete agent session to MongoDB Atlas.",
    instruction="""You are the BloodSync Audit Agent.

Call `save_audit_record` with every field from the session. Extract values
from the prior agents' outputs in this session:

- report_id:                from Intake Summary
- timestamp:                from Intake Summary
- patient_inputs:           full text of Intake Summary
- compatibility_result:     full text of Compatibility Result
- risk_assessment:          full text of Risk Assessment
- clinical_recommendation:  full text of Clinical Recommendation (including confidence score)
- safety_review:            full text of Safety Review
- escalation_decision:      full text of Escalation Routing
- handoff_report:           full text of Clinical Handoff Report

After calling the tool, report the result:
- If stored=true:  "Audit record <report_id> saved to MongoDB Atlas (bloodsync.reports)."
- If stored=false: "Audit record preserved in session output. Reason: <message>"

Do not call any other tools.""",
    tools=[save_audit_record],
    output_key="audit_record",
    generate_content_config=_DETERMINISTIC_CONFIG,
)

# ---------------------------------------------------------------------------
# Root Orchestrator
# ---------------------------------------------------------------------------

root_agent = SequentialAgent(
    name="bloodsync_orchestrator",
    description=(
        "BloodSync Emergency Transfusion Agent — eight-agent pipeline with parallel "
        "assessment, AI safety review, autonomous escalation routing, confidence scoring, "
        "and MongoDB audit trail."
    ),
    sub_agents=[
        intake_agent,
        parallel_assessment,    # compatibility + risk run simultaneously
        reasoning_agent,        # Gemini synthesis + confidence score
        review_agent,           # disagreement detection — safety guardrail
        escalation_router,      # autonomous routing by risk level
        handoff_agent,          # structured clinical report
        audit_agent,            # persistence
    ],
)
