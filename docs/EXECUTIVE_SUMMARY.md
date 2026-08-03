# FitGap — Executive Summary

**AI-assisted fit-gap analysis for Dynamics 365 & Power Platform consulting, with every capability claim verified against Microsoft's own documentation.**

---

## The problem it solves

Fit-gap analysis — deciding which client requirements Dynamics 365 meets out of the box, which need configuration or extension, and which are genuine gaps — is foundational to every engagement, but it is slow, manual, and risky. Requirements arrive scattered across Word documents, Excel backlogs, Azure DevOps, and workshop recordings. Worse, a fit-gap register that claims a product capability that doesn't actually exist can undermine an entire engagement.

## What FitGap does

FitGap automates the first 80% of the legwork, in four steps:

1. **Ingest** — pulls requirements from Word documents, Excel backlogs, Azure DevOps, and workshop transcripts (where it extracts *implied* requirements from the conversation, each traced to speaker and timestamp), then merges near-duplicates automatically.
2. **Classify** — assesses every requirement against Microsoft's own Solution Architect fit-gap methodology: Fit (out of the box), Fit (configuration), Extend (Power Platform), Gap (ISV solution), Gap (custom build), or unclear — each with a proposed approach, effort estimate, and a self-declared confidence rating.
3. **Verify** — this is the differentiator: every claimed product capability is checked against **live Microsoft Learn documentation at analysis time**. Each citation URL is then independently re-checked by plain code. Anything that cannot be proven is flagged **"UNCONFIRMED — validate manually"** — a fabricated capability claim can never reach the deliverable. Features in preview or being deprecated are flagged too.
4. **Report** — produces a client-ready Excel fit-gap register: classifications, approaches, live documentation links, effort profile, assumptions, and a blank *Consultant review* column on every row.

## What makes it trustworthy

- **The consultant makes the final call.** The register is explicitly a draft: low-confidence rows are highlighted red, unverified claims amber, and every assumption the AI made is listed for review.
- **Client data is protected.** A configurable anonymisation pass strips client names, people, emails, and project codenames *before* any text leaves the machine — and every substitution is logged.
- **The AI provider is a per-engagement compliance decision.** Anthropic is the default; OpenAI, DeepSeek, Kimi, Google Gemini, Mistral, and xAI are supported. One command switches provider, and every run states exactly which provider received data — so it always matches what the client contract permits.
- **Quality is gated, not assumed.** A built-in evaluation runs the pipeline against 25 hand-verified test cases; the tool is not considered engagement-ready unless it achieves ≥90% classification accuracy and 100% of its cited documentation links resolve live.

## Cost and efficiency

A typical engagement run costs single-digit dollars in AI usage, with the exact cost reported after every run. Duplicate claims are verified once and reused, prompts are cached, and a built-in benchmark compares cost-saving settings against accuracy — so cost is cut only where fidelity provably holds.

## The bottom line

FitGap turns days of manual requirements analysis into hours, and replaces "the consultant believes this is possible" with "here is the live Microsoft documentation that proves it" — while keeping the consultant, not the AI, in charge of the final deliverable.
