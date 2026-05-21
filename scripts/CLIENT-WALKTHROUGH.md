# DRE-Driven Disclosure Package System — Full Walkthrough for Bob

This document explains everything we have planned, in plain English, with the reasoning behind every decision. You do not need to read the technical files (`proposal.md`, `design.md`, etc.) to understand this — those files exist for the developers. This document exists for you.

If anyone reading this is not familiar with HOAs (homeowners associations) at all, the next section explains every term used. If you already know HOAs, skip Section 2.

> **How to use this doc**:
> 1. Read sections 1–6 to understand what we're building.
> 2. Read section 7 to see every decision we made and why.
> 3. Read section 10 to see what we still need you to confirm.
> 4. Use section 12 as a glossary if any term is unclear.
>
> If anything sounds wrong, surprising, or different from how you actually do this work — please flag it. This document is the moment to fix misunderstandings before the developers start building.

---

## Table of Contents

1. [What this document is](#1-what-this-document-is)
2. [HOA concepts in plain English](#2-hoa-concepts-in-plain-english)
3. [What we are building](#3-what-we-are-building)
4. [Your portfolio: three kinds of HOAs](#4-your-portfolio-three-kinds-of-hoas)
5. [The three data sources](#5-the-three-data-sources)
6. [End-to-end workflow](#6-end-to-end-workflow)
7. [Every decision we made and why](#7-every-decision-we-made-and-why)
8. [What each page in the generated PDF does](#8-what-each-page-in-the-generated-pdf-does)
9. [What stays the same, what changes](#9-what-stays-the-same-what-changes)
10. [Open questions for you](#10-open-questions-for-you)
11. [Implementation timeline](#11-implementation-timeline)
12. [Glossary](#12-glossary)

---

## 1. What this document is

You manage 75 HOAs through Tri-State Enterprises. Every year, each HOA needs a **disclosure package PDF** — a long document (often 50–100+ pages) that the law requires you to send to homeowners. It includes things like:

- A cover letter explaining what's in the package
- A budget for the upcoming fiscal year
- A reserve study summary showing how the HOA is saving for future repairs
- A 30-year financial projection
- An assessment schedule showing what each owner will pay each month
- Legal appendices (insurance disclosures, election rules, dispute resolution, etc.)

Today, you put these together by hand or with an outside accountant. We are building a system that takes the inputs you already have (the annual budget, the reserve study, and a one-time DRE document) and generates the entire disclosure package automatically — same layout for every HOA, with only the assessment math customized per HOA.

This document explains:
- How the system handles HOAs that all charge the same amount (Old Mill)
- How the system handles HOAs grouped by category (Esprit Park, Rylan Muse, 1207 Indiana)
- How the system handles HOAs with per-unit values (800 High)
- How we feed it your DRE PDFs once, review what AI extracted, and then run new budgets through it every year without re-analyzing the DRE
- How the system protects you from common errors (wrong unit, wrong year, math drift, stale documents)

If anything sounds off, please tell us before we write code.

---

## 2. HOA concepts in plain English

If you already know HOAs, skip this section. If you're new to HOAs (e.g. a developer reading this to understand the problem), here are the terms.

**HOA (Homeowners Association)** — A non-profit entity that manages shared property in a residential community: roofs of condo buildings, hallways, pools, parking, landscaping, etc. Every unit owner pays the HOA a monthly fee to maintain those shared things.

**Unit** — One residence in the HOA. A condo, a townhouse, a single-family home in a planned community. The HOA may have anywhere from 10 to several hundred units.

**Assessment** (or "HOA fee", "monthly dues") — The monthly amount each owner pays the HOA. The total of all assessments must cover the HOA's annual budget.

**Special assessment** — A one-off charge on top of regular monthly assessments, used when the HOA has a big expense (e.g. roof replacement) and the reserves aren't enough.

**Budget** — The HOA's plan for next year's income and expenses. Income mostly comes from assessments. Expenses include things like landscaping, insurance, management fees, utilities, and contributions to the reserve fund.

**Reserve fund** — A savings account the HOA contributes to every year, so that when long-life things (like a roof every 30 years) need replacement, the money is already there. Without a reserve fund, the HOA has to issue a special assessment.

**Reserve study** — A professional report (refreshed every 3 years in California, per Civil Code § 5550) listing every replaceable component of the HOA (roofs, paint, elevators, asphalt, etc.) with:
- *Useful life* (how long it lasts before needing replacement)
- *Remaining life* (years until next replacement)
- *Replacement cost* (today's dollars)

This drives the long-term financial planning.

**Reserve study expert** — The professional who produces the reserve study (in your portfolio: SMA Reserves of San Jose).

**Disclosure package** — The annual legal document sent to all owners. California Civil Code § 5300 requires it. It contains the budget, reserve information, assessment amounts, and a stack of legally-required disclosures (insurance, dispute resolution, election rules, etc.).

**Pro forma budget** — The forward-looking version of the budget (next year's plan, not last year's actuals).

**DRE (Department of Real Estate)** — California's regulator for new HOAs. When an HOA is first formed (typically by a developer), they file a DRE budget/proration document that establishes how assessments are split between unit owners forever after. The DRE document is a legal source-of-truth: even if its math has small errors, the formulas and totals it lists are the official rules. The DRE is filed once when the HOA is formed; it rarely changes (only when the CC&Rs are amended).

**CC&Rs (Covenants, Conditions, and Restrictions)** — The legal contract every unit owner agrees to when they buy. The CC&Rs spell out how the HOA works.

**Management company** — The firm hired to run the HOA day-to-day. Tri-State Enterprises is the management company for the 75 HOAs in this scope.

**CPA firm** — The accountant who prepares the audited or compiled financial statements (in your portfolio: Levy, Erlanger & Company LLP).

**Fixed vs variable assessment**:
- *Fixed*: every unit pays the same amount (Old Mill: $605/month for all 279 units)
- *Variable*: units pay different amounts based on size, ownership percentage, category, etc.

That's enough terminology to follow the rest of this document.

---

## 3. What we are building

### The one-sentence version

A web app that takes three inputs — annual budget, reserve study, and a one-time DRE PDF — and produces a complete, ready-to-send disclosure package PDF for any HOA in your portfolio.

### The slightly longer version

```
   ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
   │ Annual budget   │    │ Reserve study   │    │  DRE PDF        │
   │ (income         │    │ (refreshed      │    │ (one-time per   │
   │  statement)     │    │  every 3 yrs)   │    │  HOA, forever)  │
   │                 │    │                 │    │                 │
   │ Uploaded EVERY  │    │ Uploaded every  │    │ Uploaded ONCE   │
   │ YEAR per HOA    │    │ ~3 years        │    │ when HOA is     │
   │                 │    │                 │    │ first onboarded │
   └────────┬────────┘    └────────┬────────┘    └────────┬────────┘
            │                      │                      │
            └──────────────────────┼──────────────────────┘
                                   │
                                   ▼
                       ┌──────────────────────┐
                       │ The system extracts, │
                       │ reviews, calculates, │
                       │ and renders          │
                       └──────────┬───────────┘
                                  │
                                  ▼
                       ┌──────────────────────┐
                       │ Final disclosure     │
                       │ package PDF —        │
                       │ same layout for      │
                       │ every HOA,           │
                       │ assessment math      │
                       │ customized           │
                       └──────────────────────┘
```

### What problem this solves for you

Today, when budget season comes around, you (or your accounting firm) have to:

1. Look at last year's package as a template
2. Pull in this year's budget numbers
3. Pull in the latest reserve study
4. Hand-calculate the new assessment for each unit
5. Re-type the numbers into a Word/PDF template
6. Attach all the legal appendices
7. Send it out

For 75 HOAs, this is a huge amount of work, and small mistakes are common (typos, stale insurance disclosure from last year, missed special assessment, wrong unit count, math that doesn't quite reconcile).

The new system:

1. Stores your one-time DRE setup forever (so you only review the DRE math once per HOA)
2. Takes this year's budget upload and reserve study refresh
3. Calculates every assessment automatically
4. Lets you review the draft, fix anything wrong, then click Approve
5. Generates the full PDF with the appendices already attached
6. Catches stale appendices (e.g. a 2025 insurance disclosure being reused in 2026)
7. Saves everything as an audit-quality record for legal/board purposes

### What this system does NOT do

So you don't get the wrong expectation:

- It does NOT replace your accounting software (Tri-State's HOA software still collects payments, issues bills, etc.)
- It does NOT do bookkeeping
- It does NOT communicate with owners directly (you still send the PDF the way you do now)
- It does NOT auto-correct DRE math errors (the DRE is the legal source of truth; we flag mismatches, you decide)
- It does NOT change the legal disclosure language (we use the same boilerplate your accountant uses today)
- It does NOT export to your accounting software (this could be added later if you want)

---

## 4. Your portfolio: three kinds of HOAs

After looking at the 9 sample DRE PDFs and 75 disclosure packages in `2026/`, plus your May 13 walkthrough, we identified that your portfolio has **three patterns** for how assessments are calculated and shown.

> **Important**: even though your portfolio has 75 HOAs, we are building only one PDF template. The assessment section is the only thing that changes between the three patterns. Everything else (cover letter, reserve study summary, notes, 30-year projection, appendices) is the same layout for all 75 HOAs.

### Pattern A — Fixed assessment

**Example: Old Mill — 279 units, $605/month for everyone**

```
   ┌─────────────────────────────────────────────────┐
   │  All 279 units pay $605 per month.              │
   │                                                  │
   │  PDF shows:                                      │
   │    "Each unit's monthly assessment: $605.00"    │
   └─────────────────────────────────────────────────┘
```

- No DRE math needed (everyone pays the same)
- Easiest case to onboard
- Many HOAs in your portfolio fall here

### Pattern B — Grouped assessment

**Examples: Esprit Park (13 groups, 142 units), Rylan Muse (~9 groups, 167 units), 1207 Indiana (multi-category, base + variable)**

```
   ┌──────────────────────────────────────────────────────┐
   │  Units are organized into groups by size or type.   │
   │  Each group has its own monthly amount.              │
   │                                                       │
   │  PDF shows a table like:                             │
   │                                                       │
   │  Group  Units  Sqft   Base    Variable   Total      │
   │  ───────────────────────────────────────────────     │
   │  1      4      793   $227.10  $8.39    $235.49      │
   │  2      16     834   $227.10  $8.83    $235.93      │
   │  3      ...                                          │
   │                                                       │
   │  Math:                                               │
   │  - Base assessment = (total monthly budget          │
   │     − variable budget lines) / total units          │
   │  - Variable factor = variable budget total /        │
   │     DRE-stated square-footage denominator           │
   │  - Each group's variable assessment =                │
   │     group's avg sqft × variable factor              │
   │  - Total per group = Base + Variable                 │
   └──────────────────────────────────────────────────────┘
```

- Needs DRE math once (variable factor formula, group structure, denominator)
- After DRE setup is approved, every future year only needs the new budget
- The DRE denominator (e.g. Esprit Park's 157,536 sqft) stays the same forever, even if our recalculation from the rows comes out slightly different

### Pattern C — Per-unit assessment

**Example: 800 High (~90 units, every unit has different amounts from multiple pools)**

```
   ┌────────────────────────────────────────────────────────────────┐
   │  Every unit has its own row. Multiple "pools" of cost          │
   │  (general common, residential common, parking) each contribute │
   │  to each unit's monthly total.                                 │
   │                                                                 │
   │  PDF shows a table like:                                       │
   │                                                                 │
   │  Unit   General  Residential  Parking   Total                  │
   │         Common   Common                                        │
   │  ────────────────────────────────────────────                  │
   │  101    $145.00  $89.50       $25.00   $259.50                 │
   │  102    $145.00  $89.50       $25.00   $259.50                 │
   │  103    $228.00  $0.00        $0.00    $228.00   ← commercial  │
   │  ...                                                            │
   └────────────────────────────────────────────────────────────────┘
```

- Most complex pattern; the DRE has multiple cost "pools" with different allocation rules
- Some units may not participate in all pools (commercial unit doesn't pay residential common; non-parking unit doesn't pay parking)
- DRE math runs every year against new budget; output is one row per unit

### What "multi-pool" means and why we treat it as a variation of B or C

When you reviewed our earlier draft, you pushed back on a "fourth pattern" called Multi-Pool. You said it's just a variation of square-footage allocation. We agree. So in our system:

- Multi-pool is an **internal calculation detail**, not a separate user-visible category
- It can present as either Grouped (one row per group, even if multiple pools fed in) or Per-unit (one row per unit, with pool columns), depending on what the DRE actually shows

The operator reviewing the DRE decides whether to render as Grouped or Per-unit. Internally, the math engine handles arbitrary numbers of pools regardless.

---

## 5. The three data sources

The system runs on three pieces of data. Each comes from a different place and has a different lifecycle.

### Source 1: Annual budget (income statement)

**What it is**: Your finalized budget for the upcoming fiscal year, as a list of line items (Management Fees, Landscaping, Insurance, Reserves Contribution, etc.) with each line's annual dollar amount.

**How it gets in**: Your existing budget pipeline. You upload an Excel file or PDF; the system parses it; you review and finalize it. This part already works today.

**Lifecycle**: Once per year per HOA.

**What we're adding**: the parser is locked to promote ONLY the "Annual Budget" column from each income statement. Every real example we examined (Mathilda.pdf, Esprit Park 401 Income Statement, Old Mill packages) has a clearly labeled "Annual Budget" column. Other columns (YTD, Current Period) are still extracted and shown on the review screen for context, but they never feed the math engine. The engine derives monthly numbers inline as annual ÷ 12. Every promoted line records the source column it came from, so you can always confirm in the review screen that the correct column was picked.

### Source 2: Reserve study

**What it is**: The professional reserve study PDF — a list of components with their useful life, remaining life, and replacement cost.

**How it gets in**: Your existing reserve study parser uploads the PDF, AI extracts the component table, you review.

**Lifecycle**: Refreshed every ~3 years per HOA (California Civil Code § 5550). The system warns if the reserve study is more than 3 years old for the package year, and blocks generation if it's older than that.

**What we're adding**: an age check tied to the package's fiscal year (not today's calendar year — important when you generate a 2026 package in November 2025 vs February 2026, the staleness check is consistent either way).

### Source 3: DRE (this is the new part)

**What it is**: The HOA's Department of Real Estate filing — the legal document, typically from when the HOA was formed (often 1990s–2010s, often scanned).

**How it gets in**: You upload the DRE PDF once per HOA. The system uses Google's Gemini AI (vision model, no OCR — it looks at the page images directly) to extract:
- The HOA's basic info (name, unit count, location)
- The assessment structure (fixed vs grouped vs per-unit)
- The groups or units, with their square footage and other attributes
- The cost pools and how they're allocated
- The formulas the DRE uses
- Math validation checks

Then a human (you or the admin) reviews everything Gemini extracted, fixes anything wrong, and clicks Approve. After approval, the system saves the setup. **You never have to do this again for that HOA** unless the HOA amends its CC&Rs.

**Lifecycle**: One-time per HOA, at onboarding. Re-extraction is possible (after Gemini model upgrades, for example) but operator-initiated, never automatic.

**For fixed HOAs (Pattern A)**: no DRE upload required. You just mark the HOA as `assessment_model = fixed` and enter the monthly amount.

---

## 6. End-to-end workflow

Two workflows: onboarding a new HOA, and generating a package each year.

### 6.1 Onboarding a variable HOA (one-time per HOA)

```
   Step 1.  Create Association Profile
            ──────────────────────────
            Enter: HOA legal name, display name, address, unit count,
            management company. Save.

   Step 2.  Upload DRE PDF
            ─────────────
            Go to Association > Assessment Setup > Upload DRE.
            Enter DRE title (e.g. "Esprit Park DRE — 2013-10-16").
            Upload the PDF file. The system stores it in the DRE vault.

   Step 3.  AI extraction runs
            ──────────────────
            For DREs up to ~50 pages: one Gemini call, all pages at once.
            For very large DREs (e.g. Pacifica Mariners, 270 pages):
            the system batches the pages and processes them in groups
            of 10–20, then merges the results.

            Gemini identifies:
              • What kind of HOA this is (fixed / grouped / per-unit /
                "needs human review")
              • Which pages contain the assessment math
              • The groups (or units) and their attributes
              • The cost pools
              • The formulas

            Every value Gemini extracts is tagged with the page number
            it came from, so when you review you can see the source.

   Step 4.  Review Workbench
            ────────────────
            You see a screen with:
              • Extracted association name + unit count (prominently
                displayed so you catch if the wrong DRE was uploaded)
              • Each extracted group/unit with editable fields
              • Each cost pool with editable fields
              • Warnings: e.g. "DRE denominator 157,536 differs from
                recalculated 159,442 — DRE value will be used"
              • Source page image clickable beside every extracted value

            You edit anything wrong. If a unit's value is missing in
            the DRE (e.g. 800 High's commercial unit showing $0),
            you fill it in here. If you're unsure about something,
            you can save it as a draft and come back later.

   Step 5.  Approve
            ───────
            When everything looks right, you click Approve. The system:
              • Creates a permanent AssessmentSetup record
              • Marks the extraction run as "promoted"
              • Logs every edit you made (for audit)
              • Makes the HOA ready for annual packages

   Step 6.  Upload appendix documents (one-time per HOA)
            ────────────────────────────────────────────
            Go to Settings > Appendices. Upload each boilerplate PDF
            (insurance disclosure, ADR/IDR policy, election rules,
            collection policy, pool rules, etc.) with an operator-
            entered display title. Mark each as either:
              • Persistent (same PDF reused every year)
              • Annual (must be replaced each year — insurance!)
              • One-time

            These persist across years. Future annual packages
            automatically include them in saved order.
```

### 6.2 Generating the annual package (every year, after onboarding)

```
   Step 1.  Upload this year's budget (income statement)
            ────────────────────────────────────────────
            Same as you do today. The budget extractor parses
            and promotes the "Annual Budget" column for each line;
            you review the full extracted table (including YTD and
            Current Period values for context) and finalize.

   Step 2.  Upload latest reserve study if needed
            ─────────────────────────────────────
            If the current reserve study is from more than 3 years
            ago, the system blocks generation until you upload a
            refreshed one.

   Step 3.  Open the package draft
            ──────────────────────
            The system runs the assessment engine:
              • Reads the budget
              • Reads the saved DRE setup
              • For each pool: divides among its recipients per the
                allocation method (equal / by square footage / by
                ownership percentage / by specified per-unit value)
              • For each recipient: sums all pool contributions,
                ROUNDS ONCE to the nearest cent, computes annual total
              • Reconciles: are the per-recipient totals × 12 close
                to the assessment income from the budget?

   Step 4.  Review the draft in the UI
            ─────────────────────────────
            You see:
              • The calculated assessments (per-unit or per-group)
              • The reserve component schedule
              • The 30-year projection
              • A list of any unmapped budget lines (new line items
                the system has never seen before — you assign them
                to a pool, approve once, mapping saved forever)
              • Warnings (e.g. "pool_sum differs from budget income
                by 0.7% — please check")
              • Special assessment status (none / approved / disclosure-only)
              • The appendix manifest (uploaded once, this year's
                annual ones flagged if missing)
              • Any board-approved override (e.g. board approved
                $605/mo even though math says $361/mo — operator
                enters this in the UI as an override)

   Step 5.  Approve the draft
            ────────────────
            When numbers look right, click Approve. The system:
              • Freezes the approved revenue figure
              • Sets package status to "approved"
              • Logs you and the timestamp

   Step 6.  Generate the PDF
            ────────────────
            One click. The system renders:
              • Cover letter
              • Annual budget report
              • Forecasted financial statement
              • Notes 1–8
              • Reserve component schedule
              • 30-year reserve funding study
              • Assessment schedule (Pattern A / B / C as appropriate)
              • Inserts every saved appendix in saved order

            Outputs: final PDF + audit JSON.

   Step 7.  Finalize
            ────────
            When the PDF is final and sent, click Finalize. The system
            takes a permanent snapshot of all the inputs (assessment
            setup, budget, reserve study, appendix manifest). After
            this, if you re-render the same package in 2028, it will
            produce byte-identical output. If you need to update
            something, you create a regeneration (a new package row
            linked to the original) rather than editing the finalized one.
```

### 6.3 What happens if you regenerate mid-cycle

You generate the 2026 package in November 2025. Then in February 2026, the insurance number changes and you need to redo it.

- You don't edit the November package. The November package's snapshots are frozen.
- You create a **regeneration** — a new package row, linked to the November package by `regen_of_package_id`.
- The system asks: do you want to start from a copy of the November snapshots, or pull in current live state?
- The new February package goes through its own preflight, review, approval, finalization cycle.
- Both packages exist forever; the audit trail shows which was sent to owners.

---

## 7. Every decision we made and why

This is the long section. We list every meaningful decision the planning made, and explain why we picked that option over alternatives. This is the section you should read most carefully, because anything you disagree with should be flagged now.

### 7.1 One PDF template family, not 75

**Decision**: All 75 HOAs share one standard disclosure package template. Only the assessment-schedule page varies (3 variants: fixed / grouped / per-unit).

**Why**: When you walked us through Old Mill on May 4, then through Rylan Muse and 1207 Indiana on May 11, you said directly: *"why do they need to be different... I think at most we might have three templates."* Building 75 separate templates would be unmaintainable, and the disclosure-package format is standardized — only the assessment section legitimately varies.

**Alternative we rejected**: per-HOA template families. Too much maintenance, no actual value, would force a code deploy for every new HOA.

### 7.2 Three assessment display modes (fixed / grouped / per-unit), not four

**Decision**: The user-visible assessment options are exactly three modes.

**Why**: On May 13 you showed your accounting software's three options on screen: fixed (Old Mill), category/group (Esprit Park), and specified value (800 High). When Rohit floated a fourth ("multi-pool / combination"), you said *"that's kind of just a variation of the other one... a variation of square footage."* Internally, the math engine still handles multi-pool — it's just not a separate display option.

**Alternative we rejected**: four modes including multi-pool. Adds complexity without matching your actual workflow.

### 7.3 DRE is uploaded once, not parsed every year

**Decision**: The DRE PDF is uploaded one time when you onboard a non-fixed HOA. After it's extracted and approved, every future annual package reuses that saved setup.

**Why**: On May 13 you were emphatic about this: *"You did it one time. It's a one-time thing to set it up. And then you just plug the numbers in and then it magically works."* The DRE doesn't change unless the HOA amends its CC&Rs (which is rare). Re-parsing it every year would be wasteful and risks introducing variation from one AI run to the next.

**What still runs every year**: the annual budget mapping + assessment math. Only the DRE *extraction* is one-time.

### 7.4 DRE values are the source of truth, even when the math is wrong

**Decision**: When the DRE states a value (denominator, factor, group total, etc.) that doesn't match what we'd calculate from the visible rows, we use the DRE value. We show a warning, but we never auto-correct.

**Why**: On May 13 you said Esprit Park's denominator (157,536 sqft) doesn't match the recalculated total from the rows (~159,442), but you use the DRE value because *"the DRE is the law."* This is correct legally: the DRE is the recorded document that defines the HOA's assessment rules. Auto-correcting it would expose you to liability ("you charged me a different amount than my CC&Rs say").

**How we surface mismatches**: warnings in the Review Workbench when you first approve the DRE, plus warnings in the package audit log every time the engine runs.

**Alternative we rejected**: auto-correct the math. Liability risk and contradicts your stated workflow.

### 7.5 Human review before the AI's extraction becomes "production"

**Decision**: No DRE extraction directly drives a generated PDF. You always review and click Approve first.

**Why**: DREs are scanned PDFs from the 1990s–2010s. AI can misread numbers, mis-classify the assessment type, or hallucinate. The reviewer-approval gate is the firewall. Gemini surfaces uncertainty (every field has a confidence score; low-confidence values flag for explicit confirmation), and you make the call.

**Edits during review are logged**: every change you make in the Review Workbench writes an audit row (field, old AI value, new operator value, your name, timestamp, optional reason). When someone asks "why is this number different from the DRE?" later, the audit trail answers.

### 7.6 No spreadsheet upload path for DRE supplementing

**Decision**: All DREs in the system are PDFs. There is no upload-an-Excel-supplement path.

**Why**: You clarified on May 14 that the Excel file in the reference corpus (the Esprit Park calculations spreadsheet) was a one-time verification artifact — not how DREs come in to production. When a DRE has gaps (e.g. 800 High's commercial unit showing $0), the operator fills the missing values directly in the Review Workbench during the one-time approval step. No separate file ingestion path.

**Alternative we rejected**: Excel supplement upload with diff/conflict workflow. More complex than needed; no production HOAs use this path.

### 7.7 Three input sources, joined by the assessment engine

**Decision**: The system pulls together three independent inputs — budget (annual), reserve study (~every 3 years), and DRE (one-time) — at compile time. Each input has its own pipeline; they only meet inside the assessment engine.

**Why**: Each input has a different lifecycle, different source-of-truth ownership, and different update cadence. Coupling them tightly would mean a reserve study change forces a budget re-upload, or vice versa. Keeping them independent means each pipeline can fail or succeed alone.

```
   Budget   ──┐
              │
   Reserves ──┼──► Assessment Engine ──► Final PDF
              │
   DRE      ──┘
```

### 7.8 Operator-approved draft is the source of truth for assessment revenue

**Decision**: The total assessment revenue figure the engine reconciles against comes from the budget draft you approved — period. We do not run a priority chain trying to figure out the "correct" total from multiple sources.

**Why**: The previous draft of this plan had a priority chain (board-approved → budget line → pool sum → manual override). On reflection, this was wrong: the operator-approval step IS the resolution. The human in the loop decides what the right number is. Adding a "priority chain" inside the engine creates a second authority that can disagree with what the operator approved.

**What this means in practice**: when you approve a draft, the system freezes `approved_assessment_revenue_annual` and uses that as the target. The engine computes `pool_sum_annual` as a sanity check; if it differs from your approved value by more than 0.5%, you see a warning — but the warning is for your benefit, not a blocker.

### 7.9 Pool sum is diagnostic only, never the target

**Decision**: The sum of all pool annual totals (`pool_sum_annual`) is calculated as a sanity check, but it's never used as the reconciliation target. The target always comes from the operator-approved revenue value.

**Why**: If the engine used pool_sum as the target, reconciliation would always succeed tautologically (the engine would be comparing itself to itself). We want reconciliation to be a real check that catches mapping errors or omissions.

### 7.10 Board-approved override stays internal, not visible to homeowners

**Decision**: When the board approves a monthly assessment that differs from what the budget math implies (you mentioned a "$605 vs $361" Old Mill scenario), the override is recorded in the internal audit log only. The homeowner-visible PDF shows only the final approved amount — no "calculated vs approved" footnote.

**Why**: You said on May 15 that you don't want the homeowner PDF to include any internal calculation language. Showing "calculated value was $361" to owners would invite questions ("why am I paying $605 if the math says $361?") and could create legal/operational headaches. The board has the authority to set the monthly; the calculation methodology is internal accounting.

**What stays visible to whom**:

| Audience | Sees |
|---|---|
| Homeowner (rendered PDF) | Only the final approved monthly amount |
| Operator (your UI + audit JSON) | Full audit trail (calculated value, override amount, delta, your reason, your name, timestamp) |
| Legal (audit JSON on demand) | Same as operator |

### 7.11 Rounding happens ONCE per recipient, never per pool

**Decision**: When a recipient receives money from multiple pools (e.g. 800 High Unit 101 gets a piece from General Common, Residential Common, and Parking pools), each pool's contribution stays as full-precision Decimal until the very end. We sum the components, THEN round the recipient's monthly total to the nearest cent.

**Why**: Rounding each pool separately would compound small rounding errors and produce a different total than rounding the sum. Example: pool A contributes $50.005, pool B contributes $25.005. Round-then-sum: $50.01 + $25.01 = $75.02. Sum-then-round: $50.005 + $25.005 = $75.01. The sum-then-round version is the legally correct one (each owner is charged what the sum actually is).

The engine stores BOTH:
- Pool-level components (unrounded, for table column display)
- Recipient totals (rounded, for the bottom-line "what this unit owes")

Reconciliation uses recipient totals only.

### 7.12 Special assessment is added once per recipient, not once per pool

**Decision**: When a special assessment is "included in the regular monthly", it gets added to each applicable recipient's monthly total exactly once — regardless of how many pools that recipient participates in.

**Why**: If we added it per-pool, a unit participating in 3 pools would get charged the special assessment 3 times. We treat the special assessment as a pseudo-pool component on the recipient: one row per applicable recipient, source = `special_assessment`.

### 7.13 Annual appendix replacement (insurance disclosures don't reuse)

**Decision**: Each uploaded appendix declares its cadence: `persistent` (same PDF every year, e.g. ADR/IDR policy), `annual` (must be replaced each year, e.g. insurance disclosure), or `one_time` (only attached to one package).

**Why**: You mentioned that the file name often doesn't match the document title (e.g. `farmers_2025.pdf` is the "Annual Insurance Disclosure"). More importantly, insurance disclosures legally expire — you can't legally use a 2025 insurance disclosure in a 2027 package. The cadence tag, plus a `valid_through_year` field, lets the system block package generation when an annual appendix is expired or missing for the package year.

**Migration**: existing appendices default to `persistent`. The system flags ones that look annual (filename or title matches `/insurance/i`) for your review before enforcing the new cadence rule.

### 7.14 Persistent appendix manifest (uploaded once via Settings tab)

**Decision**: Boilerplate appendices (ADR, election rules, pool rules, etc.) are uploaded once per HOA via a Settings tab. Each carries an operator-entered display title (not the filename — `farmers_2025.pdf` becomes "Annual Insurance Disclosure"). Every new annual package automatically includes these in saved order; per-package overrides let you exclude or reorder per-year without losing the saved manifest.

**Why**: On May 4 you said *"maybe it would remember from the previous year. Who knows?"* and on May 11 you confirmed display titles should be operator-entered, not derived from filenames. This makes onboarding a new HOA easier (upload once, never re-upload), keeps year-to-year consistency, and gives you per-package flexibility for special cases.

### 7.15 No homeowner-visible audit appendix in the PDF

**Decision**: The internal audit log (warnings, overrides, AI-edits) lives only in operator UI + a separate audit JSON file accessible to operator/legal on demand. None of it is rendered into the homeowner PDF.

**Why**: Audit information could confuse owners or invite challenges. Auditors and legal can access the JSON when needed. Owners see only the legally-required, board-approved disclosure content.

### 7.16 Parser promotes the "Annual Budget" column; engine treats every line as annual by invariant

**Decision**: The income statement extractor promotes only the "Annual Budget" column from each source document (Excel or PDF) into the saved `BudgetDraft.line_items.amount`. The other columns (Current Period, YTD) are extracted to the review screen for operator inspection but never reach the math engine. The engine treats every line as annual by invariant; when it needs a monthly figure, it divides by 12.

**Why**: An earlier draft of this plan added a per-row `amount_frequency` tag plus normalized columns. After looking at the real income statements you sent (Mathilda.pdf, Esprit Park 401 Income Statement, Old Mill packages), this turned out to be over-engineering — every real income statement has a clearly labeled "Annual Budget" column, and within one document every row uses the same column convention. The silent-mixing risk lives at the parser column-picker level, not at the row level. The fix lives where the bug lives: lock the extractor to the Annual Budget column, and add `source_column` provenance to every line for audit.

**What we preserve**: every line records which source column it was promoted from (`source_column`) and where in the source document that value lived (`source_page_or_cell`), so reviewers can confirm at any time that the correct column was picked.

**Negative case**: if a future income statement lacks an "Annual Budget" column, the extractor rejects it with an actionable error (`IncomeStatementMissingAnnualColumn`) rather than silently promoting the wrong column.

### 7.17 New budget lines need operator approval the first time

**Decision**: When a new annual budget contains a line that's never been seen for this HOA, the system uses Gemini to suggest a pool mapping, but you have to approve it once. After approval, the mapping is saved forever and the line auto-maps in future years.

**Why**: You said on May 15: *"the AI can suggest a pool, but admin/Bob must approve the mapping once. After approval, the mapping is saved and reused in future years."* This is exactly the human-in-the-loop pattern for everything else in the system: AI proposes, you confirm, system remembers.

### 7.18 Mapping uses a stable pool key, not a database ID

**Decision**: Budget-line-to-pool mappings reference pools by stable `pool_key` strings (`"equal_costs"`, `"variable_costs"`, `"general_common"`, etc.) rather than internal database IDs.

**Why**: If you later re-extract a DRE (after a Gemini model upgrade, for example), the new AssessmentSetup gets new pool database IDs. We want your saved budget-line mappings to still work. Using stable string keys means a `"variable_costs"` mapping still resolves to whatever the new `"variable_costs"` pool is.

### 7.19 Mapping key includes section / category / fund type

**Decision**: Budget-line mappings disambiguate using multiple fields, not just the label:

```
(property_id, assessment_setup_id, normalized_label,
 section, category, fund_type [, account_code])
```

**Why**: Real budgets have the same label appearing in different sections — "Insurance — operating" and "Insurance — reserve" are very different lines. Mapping on label alone would let them collide. By including section, category, and fund type, we keep the mapping correct.

### 7.20 The DRE math is one big AI call (for small DREs) or batched (for big ones)

**Decision**: For DREs up to ~50 pages, one Gemini call processes everything. For very large DREs (Pacifica Mariners is 270 pages), the first pass (page classification) is batched into groups of 10–20 images, then merged, then the relevant pages go through the full extraction.

**Why**: Sending 270 pages in a single Gemini call exceeds practical input limits. Batching prevents the system from failing on large DREs.

### 7.21 The prompt to Gemini contains no HOA names

**Decision**: The actual text we send to Gemini contains no HOA names, no project names, no page-number assumptions. It is designed to handle any future DRE we haven't seen before.

**Why**: If the prompt said "look for Esprit-style structures", Gemini might project Esprit's structure onto a different HOA. By keeping the prompt strictly universal — "this is a California DRE, classify it from what you see on these page images, never assume prior knowledge" — we reduce the chance of hallucination.

### 7.22 Pool key returned by Gemini is editable in review

**Decision**: When Gemini extracts a DRE, it generates pool keys like `variable_costs` based on the pool name. You can edit these in the Review Workbench before approving.

**Why**: Pool keys become the long-term identifier for the pool across re-extractions and budget line mappings. The operator should be able to normalize them (e.g. force the same key style across HOAs) before they're locked in.

### 7.23 Engine uses 4 allocation methods only

**Decision**: Internally, the engine supports exactly four allocation methods: `equal`, `square_footage`, `ownership_percentage`, `specified_value`. The Gemini prompt is allowed to report more (`category`, `parking_space`, `custom_factor`, `unknown`), but the extraction adapter normalizes them to one of the four.

**Why**: Every observed DRE pattern can be expressed as one of the four methods combined with a recipient scope (all_units / residential_only / commercial_only / parking_users / category_group / custom). Adding flat enum values would create overlapping ways to say the same thing.

**Mapping examples**:
- "parking_space" → equal allocation across parking users
- "custom_factor" → square_footage allocation with manually-set denominator
- "category" → ownership percentage across category group

### 7.24 Multi-pool per-unit storage as a child table

**Decision**: For per-unit HOAs with multiple pools (like 800 High), the system stores one row per (unit, pool) combination in a child table `AssessmentUnitPoolAllocation`. Each row has its own dollar amount and provenance (DRE-extracted or operator-filled).

**Why**: A single per-unit total field can't express "Unit 101 owes $145 from General Common, $89.50 from Residential Common, $25 from Parking" in a way the per-unit PDF template can render as columns. The child table makes the column breakdown explicit.

### 7.25 Snapshot finalization for audit reproducibility

**Decision**: When you finalize a package, the system freezes JSON snapshots of all the inputs (assessment setup, budget, reserve study, appendix manifest) onto the package row. Future regenerations or re-renders of that finalized package use the snapshots, not the current live state.

**Why**: Two years from now, you might need to regenerate a 2026 package as it was originally sent. Between then and now, you may have edited the HOA's setup. The snapshot guarantees the regenerated PDF reproduces the original logic, not the current state. This is critical for legal/audit defense.

### 7.26 Mid-cycle regenerations are new packages, not edits

**Decision**: If you generate a 2026 package in November 2025, then need to redo it in February 2026 with updated insurance, the February package is a new database row linked to the November package by `regen_of_package_id`. Each retains its own snapshot.

**Why**: Edit-in-place would corrupt the audit trail. Creating a new package preserves history and lets the operator choose whether to start fresh or copy the original snapshots.

### 7.27 DRE re-extraction is operator-initiated, never automatic

**Decision**: After a DRE is extracted and approved, the system never re-extracts it on its own. The operator can manually request re-extraction (e.g. after a Gemini model upgrade, or if you found a better extraction prompt). Even then, re-extraction produces a new draft; the existing approved AssessmentSetup stays live until you approve the re-extraction.

**Why**: AI behavior changes over time. We don't want a prompt update to silently change how Old Mill is calculated. Operator-initiated re-extraction with explicit approval is the safe pattern.

### 7.28 Internal status fields are separated

**Decision**: The DRE extraction has two separate status concepts:
- `DREExtractionRun.status` ∈ {running, succeeded, failed, superseded} — what the extraction job did
- `DREExtractionRun.review_status` ∈ {unreviewed, reviewed, promoted} — where the human is in the review cycle
- `AssessmentSetup.status` ∈ {draft, approved, superseded} — whether this setup is the live one for the HOA

**Why**: Earlier drafts mixed these up ("status=approved"). Keeping them separate makes it clear that extraction success ≠ human approval ≠ active-setup.

### 7.29 Reserve study staleness uses package year, not today's calendar year

**Decision**: When checking if a reserve study is stale, we use the package's fiscal year (e.g. 2026 for the 2026 package), not the current calendar year.

**Why**: You might generate the 2026 package in November 2025, or in February 2026. The reserve study age check should be the same in both cases. Tying it to the fiscal year keeps it consistent.

### 7.30 The PDF page count and layout match your golden Old Mill output

**Decision**: After the refactor, Old Mill's 2026 disclosure package must remain byte-identical (or pixel-identical, within a small tolerance) to the version we generated before the refactor. A raster-diff regression test enforces this.

**Why**: Old Mill is our regression baseline. If the new system can produce something different for the same inputs, we've broken something. The raster-diff test catches accidental drift.

### 7.31 Tenant ID column reserved for future multi-tenancy

**Decision**: We add a `tenant_id` column to the relevant tables now, defaulting to 1 (Tri-State). The system is single-tenant for v1, but the column is ready if you decide later to sell this software to other management companies.

**Why**: You said on May 4: *"when we, in the future, sell this to other management companies, this information would change."* Adding a column now is cheap; retrofitting it later is expensive.

### 7.32 Concurrent edit protection

**Decision**: Settings rows, packages, and DRE approvals carry a `version_int` field. When two operators try to edit the same thing simultaneously, the second save gets HTTP 409 ("settings were updated by another user; reload to see latest").

**Why**: Without this, two simultaneous edits would silently overwrite each other. Even in a small team, budget season has high contention windows.

### 7.33 Universal preflight (one check that catches issues before generation)

**Decision**: Before any package is generated, the universal preflight runs and returns two lists:
- **Blocking errors** — must be fixed before generation
- **Warnings** — surface in operator UI + audit log, but generation can proceed

**Blocking errors include**:
- Variable HOA without an approved AssessmentSetup
- Income statement extractor could not identify an "Annual Budget" column (rejected at extraction; promotion never happens)
- Unmapped budget line (not yet assigned to a pool)
- Reserve study missing OR older than 3 years
- Required appendix missing
- Annual-cadence appendix expired for the package year
- Special assessment status=approved but missing amount or due date
- Special assessment status=disclosure-only but missing display language
- HOA needs an unsupported allocation method (Maravilla-style category-specific reserve allocation)

**Warnings include**:
- DRE denominator mismatch
- Group ownership percentages don't sum to 100%
- pool_sum_annual differs from approved revenue by >0.5%
- Rounding delta non-zero
- AI mapping confidence below 0.8
- Reserve study 2 years old (refresh due next year)

**Why**: Catching errors centrally is more reliable than checking at every step. The blocking-vs-warning split lets you proceed with known small issues while preventing major ones.

---

## 8. What each page in the generated PDF does

The full disclosure package has roughly 20 generated pages plus uploaded appendices. Here's what each generated page is and where its data comes from. (You've seen these before in your existing packages — this is just for clarity on what's automated vs uploaded.)

| # | Page | Generated or Uploaded | Data sources |
|---|---|---|---|
| 1 | Cover letter | Generated | HOA settings + computed assessment-change phrase |
| 2 | Annual Budget Report — title | Generated | HOA name, fiscal year |
| 3 | Annual Budget Report — TOC | Generated | Appendix manifest |
| 4 | §5570 Assessment & Reserve Funding Disclosure Summary | Generated | 30-year plan + assessment setup + special assessments |
| 5 | Forecasted Statement — title | Generated | HOA name |
| 6 | Forecasted Statement — TOC | Generated | — |
| 7 | Accountants' Compilation Report | Generated | CPA firm name + report date |
| 8 | Forecasted Income Statement | Generated | Budget line items (grouped by section) |
| 9 | Notes 1–3 (the Association, forecast period, basis of presentation) | Generated | HOA entity type, incorporation year, etc. |
| 10 | Notes 4–5 | Generated | Reserve components |
| 11 | Note 6 — Funding Plan | Generated | Monthly replacement contribution |
| 12 | Note 7 | Generated | Special assessments list |
| 13 | Note 8 — Outstanding Loans | Generated | Outstanding loan data (or "no loans" boilerplate) |
| 14 | Reserve Component Schedule — title | Generated | — |
| 15 | Reserve Component Schedule | Generated | Reserve components from reserve study |
| 16 | Insurance Disclosure Cover | Generated | — |
| 17 | 30-Year Study — title | Generated | — |
| 18 | 30-Year Study — Compilation Report | Generated | CPA firm name + reserve funding plan date |
| 19 | 30-Year Cash Flow Panels (3 panels: years 1–10, 11–20, 21–30) | Generated | Reserve study + budget + assessment escalation schedule |
| 20 | Major Component Schedule (3 panels: per-component expenditures by year) | Generated | Reserve components |
| 21 | **Assessment Schedule** (one of: fixed.html / grouped.html / per_unit.html) | Generated | Output of the assessment engine |
| 22+ | Static appendices | Uploaded | AppendixDocument manifest (operator-uploaded once per HOA) |

Total page count varies by HOA (Old Mill is 109 pages). The system never embeds an internal audit appendix in the PDF — audit lives separately in JSON.

---

## 9. What stays the same, what changes

### Things that stay the same as today

- The disclosure package's overall layout and legal language
- Your existing budget upload pipeline (Excel + PDF parsing)
- Your existing reserve study upload pipeline
- Boilerplate text in the cover letter, notes, and disclosure summary
- The roles: you (or another admin) review and approve everything
- Bob's accounting software (this system does not replace it)
- Your CPA firm relationship (the compilation report still references Levy, Erlanger)
- Your reserve study expert (SMA Reserves of San Jose) — name appears in the notes

### Things that change

- One template family replaces what would have been 75 per-HOA templates
- New: DRE PDF upload + AI extraction + review workflow (per HOA, once)
- New: a saved AssessmentSetup record per HOA that drives every future year's math
- New: a per-HOA appendix vault (upload once, used every year)
- New: parser locks to the "Annual Budget" column at extraction; engine treats every line as annual by invariant; monthly derived inline as annual ÷ 12; `source_column` recorded per line for audit
- New: a draft-approval step before PDF generation (you approve the numbers; system uses your approved value verbatim)
- New: snapshots on finalize so old packages can be reproduced byte-identically
- New: audit trail for every DRE edit, every override, every AI mapping suggestion
- New: preflight checks that catch missing data, stale reserve studies, expired appendices, unmapped budget lines, before PDF generation

### Things we are explicitly NOT building (this phase)

- Export to your accounting software (CSV/XLSX feed of calculated assessments) — possible follow-up
- Reserve study allocation by category (Maravilla-style SFH-vs-TH split) — deferred; preflight blocks generation for HOAs needing it
- Authentication or RBAC changes (single operator role assumed)
- Mobile-responsive UI (desktop-first)
- Automatic prompt versioning (you re-extract when you want to)
- A homeowner-visible audit appendix (audit stays internal)
- Memory of which appendices you uploaded "around this time of year" (no AI for appendix suggestions)

---

## 10. Open questions for you

Most decisions are settled, but a few benefit from your explicit confirmation:

### Q1. New budget line items the system has never seen

When a 2027 budget arrives with a new line (e.g. "EV Charger Maintenance") that wasn't in the 2026 budget, our plan is:
1. AI suggests the closest pool
2. You approve the mapping once
3. The mapping is saved and used in all future years

**Confirm**: is this the right flow, or do you want stricter (e.g. always require explicit operator entry, no AI suggestion)?

### Q2. Re-extraction triggers

We trigger DRE re-extraction only when you click a button. After Gemini model upgrades, the system can show a banner ("AI updated; consider re-extracting") but doesn't auto-rerun.

**Confirm**: is operator-only the right control? Or do you want a periodic auto-rerun (e.g. once a year)?

### Q3. Pool key naming convention

When Gemini extracts a DRE, it generates pool keys like `variable_costs` from the pool name. You can edit these in the Review Workbench.

**Confirm**: do you have preferred naming conventions across the portfolio (e.g. always `general_common` vs `general_costs`)? If you do, the AI can be instructed to use them; if you don't, free-form is fine.

### Q4. Maravilla-style SFH-vs-TH reserve allocation

Maravilla's DRE allocates some reserve components only to single-family homes vs only to townhouses. Our current plan blocks generation for HOAs needing this with a "feature not yet supported" preflight error.

**Confirm**: how many HOAs in your 75 need this? If it's just a few, we can defer until later. If it's common, we should prioritize.

### Q5. Special assessment 3rd state ("possible / disclosure-only")

A special assessment can be in one of three states:
- None
- Approved and scheduled (with amount and due dates)
- Possible — disclosure language only, no amount

**Confirm**: is the third state common enough to need first-class support? Or does it occur rarely enough that we could ask you to write the language as a "purpose" note on a regular special assessment entry instead?

### Q6. Override audit visibility for the board

The board-approved-vs-calculated audit lives in the operator UI + audit JSON. It is NOT in the homeowner PDF.

**Confirm**: should the board have a separate, board-only view of the audit? (We've assumed yes via the existing audit JSON. If you want a dedicated "board report" page, that's a separate template we'd add.)

### Q7. Insurance disclosure annual handling

Insurance disclosures are flagged as annual cadence — you must upload a new one each year. The system blocks generation if last year's is being reused for this year's package.

**Confirm**: this is correct for all your HOAs, right? No HOA has a multi-year insurance disclosure?

---

## 11. Implementation timeline

The work splits into 5 phases. Each phase produces a working, testable artifact.

### Phase 1 — Templates rename + budget normalization (~1 week)

What ships: templates renamed from `old_mill/` to `standard/`, parametrized for any HOA; budget pipeline tags every line with annual/monthly; assessment-schedule slot scaffolded but not yet inserted into Old Mill (preserves the regression baseline byte-identically).

What's testable: Old Mill 2026 package still 109 pages with no visible changes; every budget line in the system has explicit frequency.

### Phase 2 — Assessment engine (~1 week)

What ships: the math engine that takes (AssessmentSetup + Budget + Reserve study) → per-recipient assessments. Pool math for all four allocation methods. Reconciliation logic. No UI yet.

What's testable: engine produces correct dollar amounts for all 6 fixture patterns (Old Mill, Esprit-style, Rylan-style, 800-High-style, denominator-mismatch case, special-assessment case).

### Phase 3 — DRE extraction pipeline (~2 weeks)

What ships: DRE PDF upload, page rendering, Gemini extraction (single-call or batched), JSON parsing + retry, extraction storage.

What's testable: every DRE in the test corpus extracts successfully; warnings surface where math doesn't reconcile; field-level source page evidence is recorded.

### Phase 4 — Review Workbench + approval flow (~1.5 weeks)

What ships: the UI for reviewing an extracted DRE; approving it; editing values; logging DRE-review-edits; budget-line-pool mapping UI for new lines.

What's testable: you can onboard a brand-new HOA (e.g. Esprit Park) end-to-end through the UI: upload DRE → review → approve → upload budget → review draft → approve → generate PDF.

### Phase 5 — Universal preflight + onboarding + appendix manifest (~1 week)

What ships: preflight implementation with blocking + warning layers; new-HOA onboarding wizard; persistent appendix manifest UI; cleanup of legacy hoa_settings columns.

What's testable: a third HOA (one we haven't manually configured) can be onboarded end-to-end without any code changes.

### Total: ~6–7 weeks

This is from "start coding" to "all 5 phases shipped". Bob's stated August 2026 deadline (for the 2027 budget season) gives roughly 10–14 weeks from today, so there's slack — we can absorb a delay or two.

---

## 12. Glossary

**AppendixDocument** — A boilerplate PDF (insurance, ADR, election rules, etc.) uploaded once per HOA and reused across years. Has a cadence (persistent / annual / one-time) and an operator-entered display title.

**AllocationPool** — A bucket of costs in the DRE with an allocation rule (e.g. "Variable Costs — allocate by square footage among all residential units"). Stored permanently per HOA with a stable pool_key.

**AnnualPackage** — One year's disclosure package for one HOA. Has a fiscal year, status (draft → approved → rendered → finalized), and snapshots of its inputs once finalized.

**AssessmentSetup** — The saved DRE-derived rules for one HOA: setup type (fixed / grouped / per_unit), allocation pools, groups or units. Stays in place until you re-extract or supersede.

**AssessmentPoolAllocationResult** — One row per (recipient, pool), stores the unrounded component value. Powers per-pool table columns in the PDF.

**AssessmentRecipientTotalResult** — One row per recipient, stores the final ROUNDED total. Used for reconciliation.

**AssessmentUnit** — One unit in a per-unit HOA. Has square footage, ownership percent, parking spaces, category, and per-pool dollar amounts in a child table.

**AssessmentUnitPoolAllocation** — Per-unit-per-pool dollar amounts. Each row tied to a unit + pool with its own source (DRE-extracted or operator-filled).

**BudgetDraft** — The annual budget the operator finalized for a fiscal year. Each line item's `amount` is always the annual amount (promoted from the source's "Annual Budget" column by invariant). Also carries `source_column`, `source_page_or_cell`, section, category, fund_type.

**BudgetLinePoolMapping** — A saved decision that "this budget line belongs in this pool". Approved once per (HOA, label-context), reused every future year.

**Cadence** — How often an appendix needs to be replaced. Three values: persistent, annual, one_time.

**CalcResultSet** — The output of one engine run: a list of pool allocations + recipient totals + reconciliation deltas + warnings.

**DRE (Department of Real Estate)** — California's regulator for new HOAs. The DRE budget/proration document is the legal source of truth for how assessments are allocated.

**DREDocument** — One uploaded DRE PDF. Stored in the DRE vault with status (uploaded / extracted / archived / superseded).

**DREExtractionRun** — One AI-extraction job against a DRE document. Has extraction status (running/succeeded/failed/superseded) and review status (unreviewed/reviewed/promoted).

**DREReviewEdit** — One operator-made edit to an extracted DRE field. Records old value, new value, who, when, why.

**ExtractedFieldSource** — Records the page number (and optionally bounding box) for each extracted DRE value, so the Review Workbench can show the source page beside every number.

**Finalize** — The operator action that locks an approved package into its final state with frozen snapshots of all inputs.

**hoa_settings** — Existing table that holds per-HOA configurable values (CPA firm, management company contact, reserve study expert, reserve cash balance, monthly assessment, special assessments JSON, etc.). Extended in this change but not replaced.

**Pool key** — Stable string identifier for a pool (`variable_costs`, `general_common`, `parking_garage`). Survives AssessmentSetup supersessions.

**Preflight** — The validation step that runs before package generation. Returns blocking errors (halt) and warnings (proceed but surface).

**Promote** — The operator action that converts a draft extracted setup into the live AssessmentSetup. Sets DREExtractionRun.review_status='promoted'.

**Recipient** — The thing the engine charges money to. A unit (per-unit HOAs) or a group (grouped HOAs) or every unit (fixed HOAs).

**Recipient scope** — Which recipients a pool applies to: all_units / residential_only / commercial_only / parking_users / custom_unit_list / category_group.

**Re-render** — Regenerating the PDF for an already-finalized package using its frozen snapshots. Always produces the same output.

**Regeneration** — Creating a new package linked to an original (e.g. November 2025 package + February 2026 regeneration with updated insurance). Each has its own snapshot when finalized.

**Reserve study** — The professional report on what needs replacement, when, and at what cost. Refreshed every ~3 years.

**ReserveStudySnapshot** — The data structure the engine consumes from a parsed reserve study: components with useful life, remaining life, replacement cost.

**Review Workbench** — The UI screen where operators review an AI-extracted DRE and approve it.

**Rounding delta** — The difference between the engine's calculated total assessment revenue and the operator-approved revenue. Stored in three units (annual, monthly, percent).

**Special assessment** — A one-off charge separate from regular monthly assessments. Three states: none / approved_scheduled / possible_disclosure_only.

**Standard package** — The one PDF template family used for all 75 HOAs. Only the assessment-schedule page varies (3 variants).

**Tenant ID** — Future-multi-tenant column reserved on relevant tables. Defaults to 1 (Tri-State) in v1.

**Universal preflight** — One validation function that runs before every package generation, returning blocking errors + warnings.

---

## End of walkthrough

If anything is unclear, surprising, or different from how you actually do this work, please flag it now. After your sign-off, the developers start building.

Specific things to read carefully:

1. Section 4 (your three patterns) — does this match your portfolio?
2. Section 7 (decisions + reasoning) — especially 7.10 (board override stays internal), 7.13 (annual appendix replacement), 7.15 (no homeowner audit appendix), and 7.30 (Old Mill regression baseline). These shape your homeowner-facing PDF and operator workflow.
3. Section 10 (open questions) — 7 specific items we'd like your confirmation on before final implementation.
