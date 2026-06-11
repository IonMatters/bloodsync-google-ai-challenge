# BloodSync Architecture Diagram

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
            │ HTTPS POST /api/assess
            ▼
 ┌──────────────────────────┐
 │   Firebase Hosting        │  Static frontend (index.html)
 │   frontend/index.html     │  Patient form + result panel
 └──────────┬───────────────┘
            │ fetch() → Cloud Run
            ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │                     Google Cloud Run                                  │
 │                     FastAPI HTTP Layer (backend/main.py)              │
 │                                                                       │
 │   POST /api/assess → InMemoryRunner.run_async()                       │
 └──────────────────────────────┬───────────────────────────────────────┘
                                │
                                ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │               GOOGLE ADK — SequentialAgent Orchestrator               │
 │               bloodsync_agent/agent.py                                │
 │                                                                       │
 │   bloodsync_orchestrator (SequentialAgent)                            │
 │        │                                                              │
 │        ├─▶ [1] intake_agent (LlmAgent)                                │
 │        │       Model: gemini-2.0-flash                                │
 │        │       Tool:  generate_report_id()                            │
 │        │       Role:  Validate & structure patient inputs              │
 │        │                                                              │
 │        ├─▶ [2] compatibility_agent (LlmAgent)                         │
 │        │       Model: gemini-2.0-flash                                │
 │        │       Tool:  check_abo_rh_compatibility()  ◀── DETERMINISTIC │
 │        │       Role:  ABO/Rh rule-based compatibility check           │
 │        │                                                              │
 │        ├─▶ [3] risk_agent (LlmAgent)                                  │
 │        │       Model: gemini-2.0-flash                                │
 │        │       Tool:  assess_transfusion_risk()     ◀── DETERMINISTIC │
 │        │       Role:  Hb threshold + scenario urgency scoring         │
 │        │                                                              │
 │        ├─▶ [4] reasoning_agent (LlmAgent)                             │
 │        │       Model: gemini-2.0-flash                                │
 │        │       Tools: none (pure Gemini synthesis)                    │
 │        │       Role:  Clinical recommendation — context-aware reasoning│
 │        │                                                              │
 │        ├─▶ [5] handoff_agent (LlmAgent)                               │
 │        │       Model: gemini-2.0-flash                                │
 │        │       Tools: none (structured report generation)             │
 │        │       Role:  Formatted clinical handoff report               │
 │        │                                                              │
 │        └─▶ [6] audit_agent (LlmAgent)                                 │
 │                Model: gemini-2.0-flash                                │
 │                Tools: McpToolset → MongoDB MCP Server  ◀── MCP        │
 │                Role:  Persist session to MongoDB Atlas                │
 └──────────────────────────────┬───────────────────────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    │                       │
                    ▼                       ▼
 ┌──────────────────────────┐   ┌──────────────────────────┐
 │   Gemini 2.0 Flash        │   │   MongoDB MCP Server      │
 │   (Vertex AI / AI Studio) │   │   (npx mongodb-mcp-server)│
 │                           │   │                           │
 │   Agents 1–5 use Gemini   │   │   Tool: insertOne         │
 │   for reasoning, report   │   │   Collection:             │
 │   generation, and         │   │   bloodsync.reports       │
 │   clinical synthesis      │   │                           │
 └──────────────────────────┘   └──────────┬───────────────┘
                                            │
                                            ▼
                                ┌──────────────────────────┐
                                │   MongoDB Atlas           │
                                │   (Partner MCP Track)     │
                                │                           │
                                │   Audit trail of all      │
                                │   agent sessions,         │
                                │   decisions, and reports  │
                                └──────────────────────────┘
```

## Agent Pipeline Detail

```
Input: TransfusionRequest
  ↓
[1] Intake Agent
    Validates: age, sex, ABO, Rh, Hb, donor_types, scenario
    Generates: report_id, timestamp
  ↓
[2] Compatibility Agent                     ← DETERMINISTIC SAFETY LAYER
    Tool: check_abo_rh_compatibility()
    Logic: ABO compatibility matrix + Rh rule
    Output: compatible[], incompatible[], emergency_fallback[]
  ↓
[3] Risk Assessment Agent                   ← DETERMINISTIC SAFETY LAYER
    Tool: assess_transfusion_risk()
    Logic: Hb threshold bands + scenario urgency matrix
    Output: risk_level (critical/high/moderate/low), rationale
  ↓
[4] Clinical Reasoning Agent                ← GEMINI AI REASONING
    Input: structured output from agents 1–3
    Output: context-aware clinical recommendation (3–5 sentences)
  ↓
[5] Handoff Agent                           ← GEMINI REPORT GENERATION
    Input: full session context
    Output: structured handoff report (8 sections, clinician-ready)
  ↓
[6] Audit Agent                             ← MCP TOOL INVOCATION
    Tool: MongoDB MCP insertOne
    Stores: complete session record to bloodsync.reports
    Output: confirmation with report_id
  ↓
Output: TransfusionResponse (JSON) → Frontend → Clinician
```

## Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| Agent Framework | **Google ADK** (SequentialAgent + LlmAgent) | Multi-agent orchestration |
| AI Model | **Gemini 2.0 Flash** (Vertex AI) | Clinical reasoning + report generation |
| MCP Integration | **MongoDB MCP Server** (npx) | Secure tool-based persistence |
| Database | **MongoDB Atlas** | Audit trail and report storage |
| API | **FastAPI** on Google Cloud Run | HTTP interface |
| Frontend | **Firebase Hosting** | Static web UI |
| Safety Layer | Deterministic Python rules | ABO/Rh compatibility (no AI) |

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
 │                                                       │
 │  Clinician retains:                                   │
 │  ✓ Final transfusion decision                        │
 │  ✓ Override authority                                 │
 │  ✓ Protocol responsibility                            │
 └─────────────────────────────────────────────────────┘
```
