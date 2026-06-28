"""CC&R / governing-document extraction package.

Extracts assessment-allocation policy from scanned CC&R and Declaration
PDFs using Gemini Vision, converging on the shared DRESetupExtraction
domain shape so promotion, Review Workbench, approval, and assessment
mapping require zero changes.

Module layout mirrors dre_extraction/:
  page_classification  — legal-oriented page-type labels + filter
  wire_schemas         — Gemini constrained-decoding schema (CCR-specific)
  wire_to_domain       — adapter: wire → shared DRESetupExtraction domain
  pipeline             — orchestrator: classify → filter → extract → record
  prompts/             — ccr_policy_extractor.{txt,py}
"""
