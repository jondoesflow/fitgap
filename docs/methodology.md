# Methodology

`fitgap` follows Microsoft's own Solution Architect fit-gap methodology — the *"Perform fit gap analysis"* module in the Power Platform + Dynamics 365 Solution Architect learning path: determine feasibility, categorise requirements, and evaluate against Dynamics 365 first-party apps and Power Platform **before** considering custom build.

## Classification taxonomy

In order of preference — the classifier is instructed to always propose the **lowest-effort viable option first**:

| # | Category | Meaning |
|---|---|---|
| 1 | **Fit — OOB** | Met by standard D365 CE / Power Platform features with no changes |
| 2 | **Fit — Configuration** | Met via supported configuration (settings, business rules, views, forms, security roles, SLAs, low-code settings) |
| 3 | **Extend — Power Platform** | Met via Power Automate, Power Fx, Power Pages, canvas apps, PCF controls, or Dataverse plugins within supported extensibility |
| 4 | **Gap — ISV** | Best met by a known AppSource ISV solution — the candidate is named |
| 5 | **Gap — Custom** | Requires pro-code custom development |
| 6 | **Out of scope / unclear** | Not a solvable requirement as written; needs clarification |

Every classification also carries:

- **Proposed approach** — 2–3 sentences a consultant could present to a client
- **Feature relied on** — the *specific* product + feature the claim rests on (this is what gets verified)
- **Effort** — S / M / L / XL t-shirt size, with the assumptions the estimate depends on
- **Confidence** — High / Medium / Low, self-assessed. The prompt explicitly rewards *"I'm not certain this feature exists"* answered as Low confidence with the uncertainty stated in assumptions.

## Verification policy

**The worst possible failure mode of this tool is a fabricated citation.** The policy is therefore enforced in code, not just in prompts:

1. Only rows classified *Fit — OOB*, *Fit — Configuration*, or *Extend — Power Platform* make a capability claim; only those rows are verified. *Gap* and *Out of scope* rows are marked `not_required`.
2. The model must **search Microsoft Learn live** (MCP server, or web-search restricted to `learn.microsoft.com`) and return the confirming page. It is instructed never to answer from memory and never to construct a URL it did not retrieve.
3. Whatever URL comes back is then **HTTP-checked by deterministic code**: it must be on host `learn.microsoft.com` and return HTTP 200 after redirects, at analysis time. The page `<title>` is captured as the citation label, and the retrieval date is stored.
4. **Any** failure — the model can't confirm, returns no URL, returns an off-domain URL, the page 404s, the API call errors — downgrades the row to **UNCONFIRMED — validate manually** (amber in the register). The failed URL is *never* stored as a citation.
5. Learn pages indicating **preview** or **deprecated** status set flags surfaced in their own register column — preview features get their own warning tag.

What verification does **not** claim: licensing entitlement, geographic/region availability, or the state of a specific tenant. It confirms only that Microsoft's documentation describes the capability as relied upon.

## Source reliability

| Reliability | Sources | Handling |
|---|---|---|
| `stated` | .docx, .xlsx, Azure DevOps | Requirement was written down explicitly |
| `inferred` | Workshop transcripts | Extracted from conversation by the model; tagged with the timestamp + speaker that implies it; flagged in the register notes ("Inferred from transcript — confirm with client") |

When deduplication finds a stated and an inferred version of the same requirement, the **stated one always survives**.

## Human-in-the-loop

The register is a draft, and its formatting enforces that posture:

- **Consultant review** column ships blank on every row — the deliverable is incomplete until a person fills it.
- **UNCONFIRMED rows are amber** — impossible to miss, listed in the Summary sheet count.
- **Low-confidence rows are bold red.**
- **Gap — Custom rows are highlighted** — the expensive ones deserve scrutiny.
- The **Assumptions & Limitations** sheet lists every assumption the model made, the redaction summary, and generation metadata, so the register is auditable end-to-end.

## Evaluation gates

`fitgap eval` scores the pipeline against `golden/golden_set.yaml` — 25 hand-verified requirements covering easy OOB cases, deceptive requirements that *sound* native but aren't (document generation, embedded telephony, record rollback), a deprecated-feature trap (outbound marketing), genuine gaps, and unclear requirements.

The tool is **not ready for real projects** until:

1. Classification accuracy **≥ 90%** (expected category, or a listed acceptable alternative), and
2. **100%** of asserted citations resolve live when re-checked.

Do not tune prompts against individual golden entries; the set exists to catch regressions, not to be memorised.
