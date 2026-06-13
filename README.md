# BloodSync Emergency Transfusion Agent

> AI-powered emergency decision support for blood compatibility, transfusion urgency, and clinical handoff — powered by **Gemini** and **Google Cloud**.

**Mission:** Reduce preventable deaths caused by delayed transfusion decisions by providing AI-assisted blood compatibility and emergency transfusion support anywhere in the world.

[![License: MIT](https://img.shields.io/badge/License-MIT-red.svg)](LICENSE)
[![Google Cloud](https://img.shields.io/badge/Google%20Cloud-Gemini-blue)](https://cloud.google.com/)
[![MongoDB](https://img.shields.io/badge/Storage-MongoDB%20Atlas-green)](https://www.mongodb.com/atlas)

---

## Problem

In emergency and remote healthcare environments, blood transfusion decisions are often delayed by limited laboratory access, fragmented patient information, and the need for rapid compatibility checks. Delays can increase the risk of preventable deaths — especially in trauma, postpartum haemorrhage, and rural care.

## Solution

BloodSync is a **multi-agent AI system** that orchestrates eight specialised agents to support emergency transfusion workflows — combining deterministic medical safety rules with Gemini-powered clinical reasoning.

---

## Agent Workflow

BloodSync is architected as a pipeline of eight specialised agents, each with a clearly scoped responsibility:

```
1. Intake Agent
   └─ Validates patient demographics, clinical scenario, blood group,
      haemoglobin level, and available donor types
      Generates a unique report ID and UTC timestamp

2. Parallel Assessment  ⚡ (agents 2a and 2b run simultaneously)
   ├─ 2a. Compatibility Agent
   │     └─ Executes deterministic ABO/Rh compatibility rules
   │        Flags incompatible donors · Identifies O-negative universal fallback
   └─ 2b. Risk Assessment Agent
         └─ Evaluates haemoglobin threshold and clinical scenario urgency
            Produces: Low / Moderate / High / Critical risk level

3. Clinical Reasoning Agent  (Gemini 2.5 Flash)
   └─ Synthesises compatibility + risk data into a nuanced recommendation
      Calculates a deterministic confidence score (0–100)
      Explains clinical rationale in plain language

4. Safety Review Agent
   └─ Calls check_recommendation_consistency to detect AI hallucinations
      Flags incompatible donors incorrectly mentioned without negation
      Flags compatible donors missing from the recommendation
      Verdict: SAFETY CLEARED or SAFETY ADVISORY

5. Escalation Router
   └─ Autonomously routes the case based on risk level
      Critical → IMMEDIATE_ESCALATION (emergency pager)
      High     → URGENT_NOTIFICATION  (30-min haematology SLA)
      Moderate → STANDARD_HANDOFF
      Low      → ROUTINE_LOG

6. Handoff Agent
   └─ Produces the full structured clinical transfer report
      Includes: patient summary · compatibility · risk · escalation ·
      recommendation · safety verdict · disclaimer

7. Audit Agent
   └─ Persists the complete session — inputs, outputs, and report — to MongoDB Atlas
      Confirms storage with a "Saved to MongoDB Atlas" badge in the UI
      Falls back to in-memory store if MongoDB is not configured
```

Each agent is discrete and independently testable. Agents communicate via ADK session state (`output_key`) rather than conversational recall, preventing context drift across the pipeline.

---

## Architecture

```
Clinical User (Browser)
        │ HTTPS  (SSE stream — real-time agent progress events)
        ▼
Firebase Hosting (frontend/index.html)
        │ POST /api/assess/stream
        ▼
Cloud Run — FastAPI (backend/main.py)
        │ InMemoryRunner.run_async()  ──  StreamingResponse (text/event-stream)
        ▼
Google ADK — SequentialAgent Orchestrator (bloodsync_orchestrator)
        │
        ├─▶ [1] intake_agent          (LlmAgent + validate_patient_inputs, generate_report_id)
        │
        ├─▶ [2] parallel_assessment   (ParallelAgent)
        │         ├─▶ compatibility_agent  (LlmAgent + check_abo_rh_compatibility — DETERMINISTIC)
        │         └─▶ risk_agent           (LlmAgent + assess_transfusion_risk — DETERMINISTIC)
        │
        ├─▶ [3] reasoning_agent       (LlmAgent + calculate_confidence_score — Gemini synthesis)
        ├─▶ [4] review_agent          (LlmAgent + check_recommendation_consistency — safety guardrail)
        ├─▶ [5] escalation_router     (LlmAgent + trigger_escalation — autonomous routing)
        ├─▶ [6] handoff_agent         (LlmAgent — structured report generation)
        └─▶ [7] audit_agent           (LlmAgent + save_audit_record — direct pymongo)
                                                        │
                                                        ▼
                                              MongoDB Atlas
                                            (bloodsync.reports)
```

Agent outputs are passed via ADK `output_key` session state — each agent reads structured data from the previous step rather than relying on conversational recall.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full annotated system diagram.

---

## Why Gemini?

A rule engine alone can flag an incompatible blood type. Only Gemini can explain *why it matters* to a paramedic in a moving ambulance, and generate a structured handoff report the receiving hospital can act on immediately.

Gemini enables BloodSync to:

- **Context-aware emergency reasoning** — weighs haemoglobin level, clinical scenario, and compatibility data together to produce a nuanced recommendation, not just a flag
- **Structured clinical handoff generation** — formats outputs specifically for clinician-to-clinician transfer, reducing transcription errors under time pressure
- **Explainable recommendations** — every suggestion includes plain-language justification, essential for clinician trust and regulatory auditability
- **Multi-step workflow orchestration** — coordinates outputs across the eight-agent pipeline and synthesises them into a single coherent clinical summary
- **Future multimodal capability** — Gemini Vision will interpret BloodSync cartridge images and automatically detect agglutination reactions, replacing manual visual inspection in field settings

---

## Google Technologies

| Technology | Role |
|---|---|
| **Gemini 2.5 Flash** | Clinical Reasoning Agent and Handoff Agent — generates recommendations, confidence scores, and structured reports |
| **Vertex AI / AI Studio** | Gemini API endpoint |
| **Cloud Run** | Backend API deployment (containerised FastAPI + SSE streaming) |
| **Firebase Hosting** | Static frontend hosting |
| **Google Cloud IAM** | Secure service access |
| **Google ADK 2.2** | `SequentialAgent` + `ParallelAgent` + `LlmAgent` — eight-agent pipeline with parallel assessment |

## Partner Technology

| Technology | Role |
|---|---|
| **MongoDB Atlas** | Audit Agent — persistent storage of all agent sessions, inputs, outputs, and generated reports via direct pymongo |

---

## Quick Start (Local)

### Prerequisites
- Python 3.11+
- A [Gemini API key](https://aistudio.google.com/app/apikey)
- (Optional) MongoDB Atlas connection string

### 1. Clone and install

```bash
git clone https://github.com/IonMatters/bloodsync-google-ai-challenge.git
cd bloodsync-google-ai-challenge
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
```

### 2. Set environment variables

```bash
cp backend/.env.example backend/.env
# Edit .env and add your GEMINI_API_KEY
# MONGODB_URI is optional — omit it to use in-memory storage
```

### 3. Run the backend

```bash
# From the project root (not from backend/)
GEMINI_API_KEY=your_key uvicorn backend.main:app --reload --port 8080
```

You can also use the ADK dev UI to interact with the agent directly:
```bash
adk web .   # opens http://localhost:8000 — select bloodsync_agent
```

### 4. Open the frontend

Open `frontend/index.html` directly in your browser.  
Click **"Load Demo"** to try the built-in postpartum haemorrhage scenario.

---

## Demo Scenario

| Field | Value |
|---|---|
| Patient | 32-year-old female |
| Scenario | Maternal Haemorrhage |
| Blood type | O negative |
| Haemoglobin | 6.8 g/dL |
| Available donors | O negative, A positive, B positive |

**Expected output:**
- Risk level: **Critical**
- Compatible: O negative · Incompatible: A positive, B positive
- Confidence score: 72–80 / 100 (HIGH)
- Escalation: `IMMEDIATE_ESCALATION` → emergency pager
- Safety Review: SAFETY CLEARED
- Gemini generates: escalation recommendation + structured clinical handoff report
- Audit badge: 💾 Saved to MongoDB Atlas

---

## Deploy to Google Cloud

### Backend → Cloud Run

```bash
# Store credentials in a YAML file to avoid shell escaping issues with special chars
# See .cloudrun.env.yaml.example for the format
gcloud builds submit --tag gcr.io/YOUR_PROJECT/bloodsync-agent
gcloud run deploy bloodsync-agent \
  --image gcr.io/YOUR_PROJECT/bloodsync-agent \
  --platform managed \
  --region europe-west2 \
  --env-vars-file .cloudrun.env.yaml \
  --allow-unauthenticated
```

### Frontend → Firebase Hosting

```bash
npm install -g firebase-tools
firebase login
firebase init hosting   # set public directory to frontend/
firebase deploy
```

---

## API Reference

### `POST /api/assess`

Request body:
```json
{
  "age": 32,
  "sex": "Female",
  "scenario": "Maternal Haemorrhage",
  "blood_group": "O",
  "rh_factor": "-",
  "hemoglobin": 6.8,
  "donor_types": ["O negative", "A positive", "B positive"],
  "notes": "Active haemorrhage, hypotensive"
}
```

Response:
```json
{
  "report_id": "A1B2C3D4",
  "timestamp": "2026-06-11T20:00:00Z",
  "patient_blood_type": "O-",
  "risk_level": "critical",
  "compatible_donors": ["O negative"],
  "incompatible_donors": ["A positive", "B positive"],
  "emergency_fallback": [],
  "recommendation": "...",
  "handoff_report": "...",
  "disclaimer": "..."
}
```

### `GET /api/reports`

Returns last 20 generated reports from MongoDB (or in-memory store).

---

## Potential Impact

| Metric | Current State | With BloodSync |
|---|---|---|
| Compatibility assessment time | 20–45 min (lab-dependent) | **Under 2 minutes** |
| Handoff report preparation | Manual, error-prone | **Automated, structured** |
| Decision support in field | None | **Available offline-ready** |
| Audit trail | Paper-based or absent | **Persistent in MongoDB** |

BloodSync directly addresses:

- **Maternal haemorrhage** — a leading cause of preventable maternal death globally, where transfusion delay is a critical risk factor
- **Trauma care** — where seconds between assessment and action determine survival outcomes
- **Rural and humanitarian settings** — where laboratory infrastructure is limited or unavailable
- **Ambulance-to-hospital communication** — structured handoff reports reduce information loss at the point of transfer

Even modest reductions in compatibility assessment time have measurable impact at scale: in settings where postpartum haemorrhage affects 1 in 10 deliveries, faster triage translates directly to lives saved.

---

## Safety Design

- All compatibility checks are **deterministic rule-based logic** — not AI-generated
- Gemini is used only for **explainable summaries and handoff communication**
- Every report includes a **human-in-the-loop disclaimer**
- No real patient data is required for the demonstration

---

## Findings & Learnings

Building BloodSync highlighted that AI agents are most effective in emergency care when deterministic safety rules and explainable AI reasoning are combined. Compatibility logic must remain rule-based for safety and auditability, while Gemini excels at generating structured, readable summaries and escalation guidance that clinicians can act on rapidly.

---

## Innovation

BloodSync combines three capabilities that are rarely available together in a single clinical workflow:

1. **Deterministic blood compatibility assessment** — rule-based ABO/Rh logic that is safe, auditable, and explainable by design
2. **AI-powered emergency reasoning** — Gemini synthesises patient context, risk factors, and compatibility data into a nuanced clinical recommendation
3. **Automated clinician handoff generation** — structured reports produced in seconds, formatted for immediate use by the receiving team

Most existing solutions stop at laboratory testing and leave the interpretation, escalation, and communication steps to overstretched clinical staff. BloodSync extends the workflow through decision support and structured communication — helping clinicians move from result to action faster, with less cognitive load, in the moments that matter most.

---

## Why BloodSync Is Different

Unlike traditional blood typing systems that focus solely on laboratory testing, BloodSync combines rapid compatibility assessment, AI-powered clinical reasoning, and structured emergency communication within a single agentic workflow — deployable in environments where laboratory infrastructure may be absent entirely.

BloodSync is designed for settings where time, expertise, and equipment are constrained:

- Ambulances and emergency transport
- Rural and community clinics
- Military and humanitarian operations
- Maternal haemorrhage response teams
- Disaster relief and field hospitals

The long-term vision extends well beyond compatibility checking to a **portable AI-assisted diagnostic platform** capable of:

- Blood typing from cartridge inputs
- Haemoglobin assessment and trend monitoring
- Infectious disease screening
- Cartridge image interpretation via Gemini Vision
- FHIR-based interoperability with hospital records
- Offline operation in low-connectivity environments

This transforms BloodSync from a point assessment tool into an **intelligent emergency transfusion support platform** — one that travels with the patient from field to hospital, maintaining a continuous clinical picture throughout.

---

## Future Roadmap

- Integrate cartridge-based rapid blood typing results
- Add computer vision for agglutination detection
- FHIR-compatible emergency records export
- Pilot with NHS trauma and maternal care teams
- Validate against clinical transfusion protocols (SHOT, BCSH guidelines)
- Expand to infectious disease and haemoglobin screening
- **Pydantic Logfire observability** — instrument the full agent pipeline with distributed tracing, per-agent latency metrics, and Pydantic validation analytics via [Logfire](https://logfire.pydantic.dev). Each agent execution becomes a named span, tool calls are traced with inputs and outputs, and validation failures surface in a persistent dashboard — giving production-grade observability across the entire multi-agent workflow

---

## License

MIT — see [LICENSE](LICENSE)

> **Disclaimer:** BloodSync is a prototype for demonstration purposes only. It is not a certified medical device and must not be used as a substitute for professional clinical judgement.
