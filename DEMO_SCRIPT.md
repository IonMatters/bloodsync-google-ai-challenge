# BloodSync Demo Video Script
**Target length: 2:30–3:00 minutes**
**Track: MongoDB | Theme: Healthcare & Life Sciences**

---

## [0:00–0:20] Hook — The Problem

> *Show: plain slide or open with the web UI visible but not yet filled in*

**SPEAK:**
"Every year, hundreds of thousands of people die in situations where a timely blood transfusion could have saved their life.
In trauma, maternal haemorrhage, and rural emergencies, the bottleneck isn't always blood supply — it's the time it takes to assess compatibility, determine urgency, and communicate that decision to the next clinician.
BloodSync is an AI agent built to close that gap."

---

## [0:20–0:50] The Agent Architecture — Quick Overview

> *Show: the README Agent Workflow section or a simple slide of the six agents*

**SPEAK:**
"BloodSync is a multi-agent system with six specialised agents working in sequence.

The **Intake Agent** collects patient data.
The **Compatibility Agent** runs deterministic ABO/Rh rules — no AI, pure medical logic.
The **Risk Assessment Agent** evaluates haemoglobin and clinical urgency.
The **Clinical Reasoning Agent**, powered by Gemini, synthesises all of this into an explainable recommendation.
The **Handoff Agent** generates a structured report the receiving hospital can act on immediately.
And the **Audit Agent** stores the full session in MongoDB Atlas for review and accountability.

Every agent is independently scoped. The safety-critical logic stays deterministic — Gemini handles reasoning and communication."

---

## [0:50–1:50] Live Demo — Maternal Haemorrhage Scenario

> *Show: the BloodSync web UI in browser*

**SPEAK:**
"Let me show you a real scenario. A 32-year-old postpartum patient is experiencing severe haemorrhage."

> *Click **Load Demo** — walk through the pre-filled form out loud:*

"She's O-negative. Haemoglobin is 6.8 — well below the critical threshold of 8.
Available donor blood includes O-negative, A-positive, and B-positive.
We're in a maternal haemorrhage scenario."

> *Click **Run BloodSync Agent***

"The Compatibility Agent immediately identifies O-negative as the only compatible donor.
A-positive and B-positive are flagged as incompatible — an Rh mismatch for this patient.
The Risk Agent scores this as **Critical** — low haemoglobin combined with active haemorrhage.

Now watch what Gemini does with this."

> *Scroll to recommendation and handoff report*

"The Clinical Reasoning Agent produces a plain-language escalation recommendation — explaining the compatibility concern and the urgency.
And the Handoff Agent generates a structured transfer report: patient summary, blood type, risk level, donor suitability, and recommended next steps — ready to send to the receiving team."

---

## [1:50–2:20] Code & Architecture — Brief Repository View

> *Switch to GitHub repo*

**SPEAK:**
"The backend is a FastAPI service deployed on Google Cloud Run.
The frontend is hosted on Firebase.
MongoDB Atlas stores every generated report — giving us a full audit trail of agent decisions.

The compatibility rules are in plain Python — readable, testable, safe.
Gemini only receives structured data — it reasons and writes, it doesn't make safety-critical decisions.

The repo is fully open source under MIT licence."

---

## [2:20–3:00] Vision — Where BloodSync Goes Next

> *Show: roadmap slide or final README section*

**SPEAK:**
"Today, BloodSync runs as a web agent. But this is the foundation for something larger.

The next step is integration with point-of-care cartridge devices — where BloodSync uses **Gemini Vision** to interpret cartridge images and detect agglutination reactions automatically, replacing manual visual inspection in field settings.

From there: haemoglobin monitoring, infectious disease screening, FHIR-compatible records, and offline operation for ambulances and humanitarian teams.

BloodSync's mission is simple: reduce preventable deaths from delayed transfusion decisions — anywhere in the world.

Thank you."

---

## Recording Tips

- Use **QuickTime → New Screen Recording** (macOS) — no extra software needed
- Record at 1080p, keep cursor movements slow and deliberate
- Load the demo before recording so the page is ready — no waiting for loads on camera
- If Gemini call is slow, pause narration and say "the agent is running" — that's fine
- Upload to **YouTube as Unlisted** — paste the link straight into Devpost
- Do not add music or heavy editing — judges watch dozens of these; clean and clear wins

---

## Devpost Submission Description (copy-paste ready)

BloodSync Emergency Transfusion Agent is a multi-agent AI system that supports emergency blood compatibility assessment, transfusion decision support, and clinician handoff communication. The platform combines deterministic ABO/Rh compatibility logic with Gemini-powered clinical reasoning to help responders rapidly identify suitable donor blood, assess transfusion urgency, and generate structured handoff reports.

Designed for trauma care, maternal haemorrhage, rural healthcare, humanitarian operations, and emergency transport, BloodSync demonstrates how AI agents can support faster and safer transfusion workflows while keeping clinicians in control of final decisions.

Built using Gemini 2.0 Flash, Google Cloud Run, Firebase Hosting, MongoDB Atlas, and designed for future integration with Google ADK and multimodal Gemini Vision capabilities.

**Technologies:** Gemini 2.0 Flash · Google Cloud Run · Firebase Hosting · Vertex AI · Google ADK · MongoDB Atlas · FastAPI · Python
