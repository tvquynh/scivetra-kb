# Future OCR adapter contract `ocr-adapter/1` (NOT implemented in S06)

S06 detects pages without a text layer (`NO_TEXT_LAYER`, `IMAGE_ONLY_PAGE`, status `SCANNED_NO_TEXT` /
`PARSED_PARTIAL_TEXT`, flag `OCR_NOT_PERFORMED`). It runs no OCR, silently or otherwise. A later, owner-approved stage may add
OCR only under this contract:

1. **Input:** a `parse_artifact` (never raw CAS bytes outside the parse sandbox) plus the page numbers flagged `NO_TEXT_LAYER`.
   The same gates as `rkb parse` apply first (KB asset, link, current `local_process = ALLOW`, isolation).
2. **Local only:** the OCR engine runs locally in the same kind of bounded child; no cloud OCR, no upload (remote AI/OCR is not
   approved). Its name, version, licence, language packs and model hashes are pinned like the parser (`build_sha256`).
3. **Output:** a NEW, separate immutable artifact `ocr-artifact/1` that references the source `parse_artifact` and asset
   SHA-256. The original ParseArtifact is never rewritten. OCR text carries per-block confidence and the flag
   `OCR_TEXT_UNCERTAIN`; anchors follow the same code-point rule with the OCR artifact's own `artifact_sha256`.
4. **Provenance:** engine/config hash, page images' render parameters (DPI, colour), and every failure are audited like
   `parse_run`.
5. **Evidence:** OCR text is never evidence-ready by itself; a human must check quotes against the page image.
6. **Licensing:** AGPL or commercial engines need an explicit owner licence decision before they are embedded.
