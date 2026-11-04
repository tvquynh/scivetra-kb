"""S04J Journal DOI Harvesting: Crossref-first, OpenAlex-enriched, provenance-preserving journal inventory (ADR-JH-001).

Offline candidate: the only transport is the development simulator (``simulator.py``); live provider access is refused.
A journal inventory is discovery evidence only. It never creates GlobalPapers, SLR inclusions, screening decisions,
Reprovane corpus members, identity-confirmed PDFs or download entitlements.
"""
