# AI-Driven Cloud Cost Optimization — Proof of Concept

This is the proof-of-concept for our graduation project: an AI-powered tool that reads Terraform infrastructure code, reasons about cheaper AWS alternatives (including cross-architecture suggestions like EC2 → Lambda), and rewrites the Terraform code automatically.

**This is a PoC, not the full system.** It exists to demonstrate that the core mechanism — LLM reasoning grounded in pricing facts, applied back onto real Terraform code — actually works. See [Limitations](#limitations--whats-not-real-yet) below for what's intentionally out of scope at this stage.

## What's in this repo

| File | Purpose |
|---|---|
| `POC.ipynb` | The main notebook — parses Terraform, gets an LLM cost-optimization suggestion, and rewrites the Terraform code |
| `tf_json_convert.py` | Converts between Terraform (`.tf`) and a JSON schema, in both directions. Supports `aws_instance` (EC2) and `aws_lambda_function` (Lambda) resources |

## How it works (pipeline)

```
.tf file  →  tf_json_convert.py  →  resource dict (JSON)
                                          ↓
                            + placeholder monthly cost
                                          ↓
                              LLM (via Groq API) reasons
                              about a cheaper alternative
                                          ↓
                          suggestion applied back onto the
                              resource dict (EC2 resize or
                                  EC2 → Lambda switch)
                                          ↓
                            tf_json_convert.py  →  new .tf file
```

## How to run it (for the TA)

### 1. Requirements
- A Google account (to run the notebook in Google Colab — no local setup needed)
- A free [Groq API key](https://console.groq.com/keys) (the notebook uses Groq's free-tier LLM API — no billing required for a free-tier key)

### 2. Steps

1. **Open the notebook in Colab.**
   - Go to [Google Colab](https://colab.research.google.com/), choose "Upload," and select `POC.ipynb` from this repo — or, if the repo is public, use File → Open notebook → GitHub, paste the repo URL, and select `POC.ipynb`.

2. **Upload `tf_json_convert.py` into the Colab session.**
   - In Colab's left sidebar, click the folder icon → the upload icon → select `tf_json_convert.py` from this repo.
   - This step must be repeated any time the Colab runtime restarts, since uploaded files don't persist across sessions.

3. **Add your Groq API key as a Colab secret.**
   - In Colab's left sidebar, click the key icon ("Secrets").
   - Add a new secret named exactly `GROQ_API_KEY`, and paste your Groq API key as the value.
   - Toggle "Notebook access" on for this secret.

4. **Run all cells, top to bottom.**
   - Runtime menu → "Run all."
   - Running cells out of order, or after a runtime restart without re-running everything, will cause `NameError` (e.g. `client` or `MOCK_pricing_knowledge` not defined) — if that happens, just re-run from the top.

5. **Read the output as you go.**
   - The notebook prints the parsed resource, the LLM's raw suggestion, and (when applicable) the newly generated Terraform code.
   - Markdown cells throughout explain what each section proves and doesn't prove.

### 3. What to expect

- The notebook runs several test cases: a straightforward EC2 resize, a workload with `workload_characteristics` hints that nudge the LLM toward suggesting Lambda, and a full round-trip that writes real Terraform code for whichever suggestion comes back.
- **LLM output is not deterministic** — the same input can produce a different suggestion on different runs (e.g. one run might suggest resizing to `t3.medium`, another might suggest Lambda). This is expected and intentional to show; re-running a cell is a legitimate way to see different behavior, not a bug.

## Limitations / what's not real yet

This PoC intentionally does **not** include:
- **Real AWS billing data** — all costs in the notebook are hand-typed placeholder figures (`MOCK_pricing_knowledge`, `MOCK_INSTANCE_COSTS`), not pulled from AWS's actual pricing.
- **Retrieval (RAG)** — pricing facts are manually sorted by resource type, not automatically retrieved from a real knowledge base.
- **A validation engine** — the LLM's cost claims are not numerically double-checked against real data before being trusted.
- **Fargate support** — `tf_json_convert.py` doesn't support Fargate yet, since it isn't a single Terraform resource type (it's an ECS task definition + service pair). Suggestions involving Fargate will print a message and won't generate code.
- **Deployable Lambda code** — when a suggestion switches a resource to Lambda, the generated Terraform includes a `REPLACE_ME.zip` placeholder for the deployment package, and placeholder values for `role`, `runtime`, and `handler` that a human must review and replace before `terraform apply` would actually work.

These are documented as planned work for the full graduation project, not omissions we're unaware of.

## Team

Graduation project team — AI-Driven Multi-Cloud Cost Optimization Tool.
