# BloodSync Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    BLOODSYNC EMERGENCY TRANSFUSION AGENT                 │
│                    Ion Matters HealthTech Solutions Ltd                   │
│                    Google for Startups AI Agents Challenge — Track 1     │
└─────────────────────────────────────────────────────────────────────────┘

 ┌──────────────────────────┐
 │   Clinical User           │  Paramedic / Nurse / Emergency Responder
 │   (Web Browser)          │
 └──────────┬───────────────┘
            │ HTTPS POST /api/assess/stream  (Server-Sent Events)
            ▼
 ┌──────────────────────────┐
 │   Firebase Hosting        │  Static frontend (frontend/index.html)
 │   frontend/index.html     │  Patient form · live pipeline panel · result
 └──────────┬───────────────┘
            │ EventSource → Cloud Run
            ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │                     Google Cloud Run                                  │
 │                     FastAPI HTTP Layer (backend/main.py)              │
 │                                                                       │
 │   POST /api/assess/stream → StreamingResponse (text/event-stream)    │
 │   Events: agent_start · agent_complete · complete · error             │
 │                                                                       │
 │   POST /api/assess        → JSON (non-streaming fallback)             │
 │   GET  /api/reports       → last 20 audit records from MongoDB        │
 └──────────────────────────────┬───────────────────────────────────────┘
                                │ InMemoryRunner.run_async()
                                ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │               GOOGLE ADK — SequentialAgent Orchestrator               │
 │               bloodsync_agent/agent.py                                │
 │                                                                       │
 │   bloodsync_orchestrator (SequentialAgent)                            │
 │        │                                                              │
 │        ├─▶ [1] intake_agent (LlmAgent)                                │
 │        │       Model: gemini-2.5-flash  · temp: 0.0                  │
 │        │       Tools: validate_patient_inputs(), generate_report_id() │
 │        │       Role:  Validate & structure all patient inputs          │
 │        │       State: output_key="intake_summary"                     │
 │        │                                                              │
 │        ├─▶ [2] parallel_assessment (ParallelAgent) ⚡                 │
 │        │        │                                                     │
 │        │        ├─▶ compatibility_agent (LlmAgent)                    │
 │        │        │       Model: gemini-2.5-flash  · temp: 0.0          │
 │        │        │       Tool:  check_abo_rh_compatibility() ◀─ DETERM │
 │        │        │       State: output_key="compatibility_result"       │
 │        │        │                                                     │
 │        │        └─▶ risk_agent (LlmAgent)                             │
 │        │                Model: gemini-2.5-flash  · temp: 0.0          │
 │        │                Tool:  assess_transfusion_risk()    ◀─ DETERM │
 │        │                State: output_key="risk_assessment"           │
 │        │                                                              │
 │        ├─▶ [3] reasoning_agent (LlmAgent)                             │
 │        │       Model: gemini-2.5-flash  · temp: 0.2                  │
 │        │       Tool:  calculate_confidence_score()                    │
 │        │       Role:  Clinical synthesis + confidence scoring (0–100) │
 │        │       State: output_key="clinical_recommendation"            │
 │        │                                                              │
 │        ├─▶ [4] review_agent (LlmAgent)                                │
 │        │       Model: gemini-2.5-flash  · temp: 0.0                  │
 │        │       Tool:  check_recommendation_consistency() ◀─ GUARDRAIL│
 │        │       Role:  Detect AI hallucinations vs deterministic data  │
 │        │       State: output_key="safety_review"                      │
 │        │                                                              │
 │        ├─▶ [5] escalation_router (LlmAgent)                           │
 │        │       Model: gemini-2.5-flash  · temp: 0.0                  │
 │        │       Tool:  trigger_escalation()                            │
 │        │       Role:  Autonomous routing by risk level                │
 │        │       State: output_key="escalation_decision"                │
 │        │                                                              │
 │        ├─▶ [6] handoff_agent (LlmAgent)                               │
 │        │       Model: gemini-2.5-flash  · temp: 0.2                  │
 │        │       Tools: none (structured report generation)             │
 │        │       Role:  Full clinical handoff report (8 sections)       │
 │        │       State: output_key="handoff_report"                     │
 │        │                                                              │
 │        └─▶ [7] audit_agent (LlmAgent)                                 │
 │                Model: gemini-2.5-flash  · temp: 0.0                  │
 │                Tool:  save_audit_record()  ◀── direct pymongo         │
 │                Role:  Persist complete session to MongoDB Atlas        │
 │                State: output_key="audit_record"                       │
 └──────────────────────────────┬───────────────────────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    │                       │
                    ▼                       ▼
 ┌──────────────────────────┐   ┌──────────────────────────┐
 │   Gemini 2.5 Flash        │   │   MongoDB Atlas           │
 │   (Vertex AI / AI Studio) │   │   bloodsync.reports       │
 │                           │   │                           │
 │   All 7 agents use Gemini │   │   Stores: report_id,      │
 │   Deterministic tools run │   │   patient inputs, compat  │
 │   inside LlmAgent context │   │   result, risk, reasoning │
 │   temp=0 → tool calling   │   │   safety review, handoff  │
 │   temp=0.2 → synthesis    │   │   Full audit trail        │
 └──────────────────────────┘   └──────────────────────────┘
```

## Agent Pipeline Detail

```
Input: TransfusionRequest (age, sex, scenario, blood_group, rh_factor,
                           hemoglobin, donor_types, notes)
  ↓
[1] Intake Agent                            ← INPUT VALIDATION
    Tools: validate_patient_inputs()
           generate_report_id()
    Validates all fields; stops pipeline on error
    Generates: report_id (8-char UUID), UTC timestamp
    output_key: intake_summary
  ↓
[2] Parallel Assessment ⚡                  ← DETERMINISTIC SAFETY LAYER
  ├─ Compatibility Agent
  │   Tool: check_abo_rh_compatibility()
  │   Logic: ABO compatibility matrix + Rh donor/recipient rule
  │   Output: compatible[], incompatible[], emergency_fallback[]
  │   output_key: compatibility_result
  │
  └─ Risk Assessment Agent
      Tool: assess_transfusion_risk()
      Logic: Hb band (critical <6 / high <8 / moderate <10 / low ≥10)
             + high-urgency scenario modifier
      Output: risk_level (critical/high/moderate/low), rationale
      output_key: risk_assessment
  ↓
[3] Clinical Reasoning Agent                ← GEMINI AI SYNTHESIS
    Tool: calculate_confidence_score()
    Reads: compatibility_result + risk_assessment from session state
    Output: 3–5 sentence clinical recommendation + confidence score
    Guardrails: references ONLY values from prior agents' outputs
    output_key: clinical_recommendation
  ↓
[4] Safety Review Agent                     ← AI HALLUCINATION GUARDRAIL
    Tool: check_recommendation_consistency()
    Checks: incompatible donors not recommended without negation
            compatible donors present in recommendation (normalised match)
    Window: ±100 chars around each donor mention
    Verdict: SAFETY CLEARED or SAFETY ADVISORY
    output_key: safety_review
  ↓
[5] Escalation Router                       ← AUTONOMOUS ROUTING
    Tool: trigger_escalation()
    critical → IMMEDIATE_ESCALATION (priority 1, emergency_pager)
    high     → URGENT_NOTIFICATION  (priority 2, urgent_queue, 30-min SLA)
    moderate → STANDARD_HANDOFF     (priority 3, standard_queue)
    low      → ROUTINE_LOG          (priority 4, routine_log)
    output_key: escalation_decision
  ↓
[6] Handoff Agent                           ← GEMINI REPORT GENERATION
    Tools: none
    Reads all prior output_keys from session state
    Produces: full 8-section structured clinical handoff report
    Always generates report; safety advisory shown as a warning section
    output_key: handoff_report
  ↓
[7] Audit Agent                             ← PERSISTENCE
    Tool: save_audit_record() — direct pymongo (no MCP/npx dependency)
    Stores: all 7 output keys + patient inputs to bloodsync.reports
    Falls back gracefully if MONGODB_URI not set
    UI confirms: "💾 Audit saved to MongoDB Atlas" badge
    output_key: audit_record
  ↓
Output: SSE complete event → renderResult() → Clinical Report UI
```

## Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| Agent Framework | **Google ADK 2.2** (`SequentialAgent` + `ParallelAgent` + `LlmAgent`) | Eight-agent pipeline orchestration |
| AI Model | **Gemini 2.5 Flash** | Clinical reasoning, synthesis, report generation |
| Streaming | **FastAPI SSE** (`StreamingResponse`) | Real-time agent progress to browser |
| Database | **MongoDB Atlas** (direct pymongo) | Audit trail and report storage |
| API | **FastAPI** on Google Cloud Run | HTTP + SSE interface |
| Frontend | **Firebase Hosting** | Static web UI with live pipeline panel |
| Safety — deterministic | Python rule functions | ABO/Rh compatibility, risk scoring |
| Safety — AI guardrail | `check_recommendation_consistency` | Hallucination detection against rule outputs |

## Session State Flow

Agents communicate via ADK `output_key` session state — not conversational recall.
Each agent writes its structured output to a named key; the next agent reads it directly.

```
intake_summary  ──▶  compatibility_result  ──▶  clinical_recommendation
                      risk_assessment       ──▶  safety_review
                                            ──▶  escalation_decision
                                            ──▶  handoff_report
                                            ──▶  audit_record
```

This prevents context drift: a downstream agent cannot misremember an upstream value.

## Safety Architecture

```
 ┌─────────────────────────────────────────────────────┐
 │              HUMAN-IN-THE-LOOP DESIGN                │
 │                                                       │
 │  AI (Gemini) handles:          Rules handle:         │
 │  ✓ Reasoning & synthesis       ✓ ABO compatibility   │
 │  ✓ Report generation           ✓ Rh matching         │
 │  ✓ Clinical communication      ✓ Risk thresholds     │
 │  ✓ Escalation guidance         ✓ Fallback logic      │
 │  ✓ Confidence scoring          ✓ Safety consistency  │
 │                                                       │
 │  Clinician retains:                                   │
 │  ✓ Final transfusion decision                        │
 │  ✓ Override authority                                 │
 │  ✓ Protocol responsibility                            │
 └─────────────────────────────────────────────────────┘
```

Deterministic tools (`check_abo_rh_compatibility`, `assess_transfusion_risk`,
`check_recommendation_consistency`) run inside the LlmAgent context but contain
**no AI logic** — their outputs are fixed given the same inputs, making them
safe to use as guardrails against Gemini's probabilistic outputs.
