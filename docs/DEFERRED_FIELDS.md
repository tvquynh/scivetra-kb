# Fields and entities deferred to S03+ (not in migration 0001)

S02 implements only what metadata enrichment and publication integrity need. These parts of S01 v03 `03` are deferred:

| S01 v03 entity / field | Status in S02 | Planned stage |
|---|---|---|
| WorkFamily, WorkFamilyMember | not created | S03 |
| DocumentVersion, PdfAsset, PaperAssetLink, RightsAssessment, ParseArtifact | not created | S03–S06 |
| ProjectPaperReference, WorkspaceRegistration (full) | replaced in S02 by `reprovane_export_import` + scoped `reprovane_*` IdentityAssertions | S03 |
| IdentityConflict, IdentityResolutionEvent, LinkCandidate, LinkDecision (link-policy/1, 03 §6.3) | not created; S02 never auto-links RV items to existing papers (each RV item mints one paper) | S03 |
| AcquisitionBatch, BridgeRow, AcquisitionAttempt, ImportEvent | not created | S04 |
| EvidenceRecord, AIProposal, Approval | not created | S07–S08 |
| GlobalPaper `merged_into_paper_id`, state transitions | column absent; S02 papers stay `PROVISIONAL` | S03 |
| IdentityAssertion `registration_agency` refresh after RA lookup | RV-imported DOI assertions keep `UNKNOWN`; the RA result lives in `ra_claim` (append-only design, no UPDATE) | S03 |
| ClaimConflict / ClaimDispute views | not created | S03 |
| `rkb upgrade`, backup/restore commands | not created; S02 refuses lower and higher schemas | S03 |

## Status after S03 (0.3.0.dev1)

| Entity | S03 status |
|---|---|
| WorkFamily, WorkFamilyMember | implemented (proposals + human confirmation) |
| DocumentVersion, PdfAsset, PaperAssetLink, RightsAssessment | implemented (synthetic intake only); ParseArtifact deferred to S06 |
| WorkspaceRegistration, ProjectPaperReference | implemented (library import) |
| IdentityConflict, IdentityResolutionEvent, LinkCandidate, LinkDecision | implemented; merge/split pending (ADR-S03-04) |
| ImportEvent | implemented for REPROVANE_EXPORT and MANUAL_FILE; AcquisitionBatch/BridgeRow/AcquisitionAttempt remain S04 |
| `merged_into_paper_id`, state transitions | projections (ADR-S03-02); merge not implemented |
| `rkb upgrade`, backup/restore | implemented |
| ClaimConflict / ClaimDispute views, FullTextCase lifecycle, rights-safe export | still deferred |
