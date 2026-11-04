"""Generate the S04J synthetic provider scenarios (deterministic; no randomness, no network).

Writes ``tests/fixtures/journal_sim/<scenario>/journal_sim.json`` (schema ``jh-sim/1``) and the batch file:

* ``jisa_shaped``     one journal shaped like JISA: print ISSN 2214-2126, electronic ISSN 2214-2134, 2,226 Crossref
                      records (2,200 articles + other work types, notices and DOI edge cases) and an independent OpenAlex
                      inventory with deliberate overlaps, gaps, conflicts, duplicates, OA/licence variants and retractions.
                      Profiles: ``interrupt_once`` (crash at Crossref page 5), ``after_restart`` (all old cursors expired),
                      ``second_snapshot`` (a later provider state: changed records, a dropped retraction, a removed record).
* ``three_publishers``  three mock journals styled after three different publishers plus one invalid ISSN entry.

ALL DATA IS SYNTHETIC. DOIs use the Crossref test prefix 10.5555; titles, authors, URLs (``*.example``) and counts are
invented. The ISSNs 2214-2126/2214-2134 are used only to shape the JISA-like fixture as the handoff requires; nothing here
is JISA metadata, and the owner's historical count (2151) is NOT used as the fixture size or as an expected value.
The three mock journals use ISSNs in the 0000-00xx range; any coincidence with a real serial is accidental.
Usage:  python -I tools/s04j_make_fixtures.py [--check]
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "journal_sim"
SYNTHETIC_OPENALEX_TEST_KEY = "SYNTHETIC-OA-TEST-KEY-not-a-real-credential"
JISA_PRINT, JISA_E = "2214-2126", "2214-2134"
JISA_SOURCE, REPO_SOURCE, ARXIV_SOURCE = "S900000001", "S900000099", "S900000098"
JTITLE = "Synthetic JISA-shaped Journal (fixture)"


def dumps(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=None, separators=(",", ":")) + "\n").encode("utf-8")


def date_obj(y, m=None, d=None, with_time=False):
    parts = [p for p in (y, m, d) if p is not None]
    out = {"date-parts": [parts]}
    if with_time:
        out["date-time"] = f"{y:04d}-{m or 1:02d}-{d or 1:02d}T00:00:00Z"
        out["timestamp"] = 1_600_000_000_000 + y * 1000 + (m or 1) * 31 + (d or 1)
    return out


def cc_by(y, m, d, version="vor", uri="https://creativecommons.org/licenses/by/4.0/"):
    return {"URL": uri, "content-version": version, "delay-in-days": 0, "start": date_obj(y, m, d, with_time=True)}


def oa_profile(i):
    """(crossref licences, openalex OA spec) for article i. spec = dict(is_oa, pdf, version, status, repo)."""
    y, m, d = 2013 + i % 14, 1 + i % 12, 1 + i % 28
    closed = {"is_oa": False, "pdf": None, "version": None, "status": "closed", "repo": False}
    if i == 70:
        return [cc_by(y, m, d)], {"is_oa": True, "pdf": "http://127.1/paper.pdf", "version": "publishedVersion", "status": "gold", "repo": False}
    if i % 17 == 0:
        return [cc_by(2027, 1, 1)], {"is_oa": True, "pdf": f"https://publisher.example/pdf/{i}.pdf", "version": "publishedVersion", "status": "hybrid", "repo": False}
    if i % 13 == 0:
        return [cc_by(y, m, d, uri="https://creativecommons.org/licenses/by/4.0/legalcode?lang=en")], \
            {"is_oa": True, "pdf": f"https://publisher.example/pdf/{i}.pdf", "version": "publishedVersion", "status": "hybrid", "repo": False}
    if i % 11 == 0:
        return [{"URL": "https://publisher.example/licenses/standard-terms", "content-version": "vor", "delay-in-days": 0, "start": date_obj(y, m, d, True)}], \
            {"is_oa": True, "pdf": f"https://publisher.example/pdf/{i}.pdf", "version": "publishedVersion", "status": "bronze", "repo": False}
    if i % 20 == 0:
        return [cc_by(y, m, d, version="am")], {"is_oa": True, "pdf": f"https://repository.example/{i}/accepted.pdf", "version": "acceptedVersion",
                                                "status": "green", "repo": True}
    if i % 10 == 0:
        return [cc_by(y, m, d)], {"is_oa": True, "pdf": f"https://publisher.example/pdf/{i}.pdf", "version": "publishedVersion", "status": "gold", "repo": False}
    if i % 10 == 5:
        return [cc_by(y, m, d)], {"is_oa": True, "pdf": None, "version": "publishedVersion", "status": "gold", "repo": False}
    if i % 7 == 0:
        return [{"URL": "https://publisher.example/tdm/userlicense/1.0/", "content-version": "tdm", "delay-in-days": 0, "start": date_obj(y, m, d, True)}], closed
    return [], closed


def cr_article(i, doi=None, **extra):
    y, m, d = 2013 + i % 14, 1 + i % 12, 1 + i % 28
    doi = doi or f"10.5555/jisa-synthetic.{i:06d}"
    licences, _ = oa_profile(i)
    item = {"DOI": doi, "type": "journal-article", "title": [f"Synthetic security study number {i}"],
            "container-title": [JTITLE], "short-container-title": ["Synth JISA"], "ISSN": [JISA_PRINT, JISA_E],
            "issn-type": [{"value": JISA_PRINT, "type": "print"}, {"value": JISA_E, "type": "electronic"}],
            "publisher": "Synthetic Publisher One (fixture)", "prefix": "10.5555", "member": "99999",
            "volume": str(10 + i % 70), "issue": str(1 + i % 6) if i % 4 else None, "article-number": str(100000 + i),
            "published-online": date_obj(y, m, d), "published-print": date_obj(y, m), "issued": date_obj(y, m, d),
            "created": date_obj(y, m, d, True), "deposited": date_obj(2026, 1 + i % 9, 15, True), "indexed": date_obj(2026, 10, 1, True),
            "author": [{"given": "A.", "family": f"Synthetic{i}", "sequence": "first", "affiliation": [{"name": "Synthetic Institute"}]},
                       {"given": "B.", "family": "Fixture", "sequence": "additional", "affiliation": []}],
            "resource": {"primary": {"URL": f"https://publisher.example/article/{i}"}}}
    if item["issue"] is None:
        del item["issue"]
    if licences:
        item["license"] = licences
    item.update(extra)
    return item


_DEFAULT = object()


def oa_work(i, doi=_DEFAULT, *, wid=None, source=JISA_SOURCE, alt_sources=(), **extra):
    y, m, d = 2013 + i % 14, 1 + i % 12, 1 + i % 28
    doi = f"10.5555/jisa-synthetic.{i:06d}" if doi is _DEFAULT else doi
    _, spec = oa_profile(i)
    src = {"id": f"https://openalex.org/{source}", "display_name": {JISA_SOURCE: JTITLE, REPO_SOURCE: "Synthetic Repository",
                                                                   ARXIV_SOURCE: "Synthetic Preprint Server"}.get(source, "Synthetic Source"),
           "issn_l": JISA_PRINT if source == JISA_SOURCE else None, "issn": [JISA_PRINT, JISA_E] if source == JISA_SOURCE else None,
           "type": "journal" if source == JISA_SOURCE else "repository"}
    landing = f"https://doi.org/{doi}" if doi else f"https://publisher.example/article/nodoi-{i}"
    primary = {"source": src, "landing_page_url": landing, "is_oa": spec["is_oa"] and not spec["repo"],
               "pdf_url": spec["pdf"] if not spec["repo"] else None, "license": "cc-by" if spec["is_oa"] and not spec["repo"] else None,
               "version": spec["version"] if not spec["repo"] else "publishedVersion"}
    locations = [primary]
    best = primary if primary["is_oa"] else None
    if spec["repo"]:
        repo = {"source": {"id": f"https://openalex.org/{REPO_SOURCE}", "display_name": "Synthetic Repository", "issn_l": None, "issn": None,
                           "type": "repository"},
                "landing_page_url": f"https://repository.example/{i}", "is_oa": True, "pdf_url": spec["pdf"], "license": "cc-by",
                "version": spec["version"]}
        locations.append(repo)
        best = repo
    for alt in alt_sources:
        locations.append({"source": {"id": f"https://openalex.org/{alt}", "display_name": JTITLE if alt == JISA_SOURCE else "Synthetic Source",
                                     "issn_l": JISA_PRINT if alt == JISA_SOURCE else None,
                                     "issn": [JISA_PRINT, JISA_E] if alt == JISA_SOURCE else None, "type": "journal"},
                          "landing_page_url": f"https://publisher.example/article/{i}", "is_oa": False, "pdf_url": None, "license": None,
                          "version": "publishedVersion"})
    work = {"id": f"https://openalex.org/{wid or f'W{9000000000 + i}'}", "doi": f"https://doi.org/{doi}" if doi else None,
            "title": f"Synthetic security study number {i}", "display_name": f"Synthetic security study number {i}",
            "publication_year": y, "publication_date": f"{y:04d}-{m:02d}-{d:02d}", "type": "article",
            "primary_location": primary, "locations": locations, "best_oa_location": best,
            "open_access": {"is_oa": spec["is_oa"], "oa_status": spec["status"], "oa_url": spec["pdf"] or (landing if spec["is_oa"] else None)},
            "is_retracted": False,
            "authorships": [{"author": {"display_name": f"A. Synthetic{i}", "orcid": None}, "author_position": "first"},
                            {"author": {"display_name": "B. Fixture", "orcid": None}, "author_position": "last"}],
            "biblio": {"volume": str(10 + i % 70), "issue": str(1 + i % 6) if i % 4 else None, "first_page": str(100000 + i), "last_page": None},
            "updated_date": f"2026-{1 + i % 9:02d}-20T00:00:00", "created_date": f"{2013 + i % 14}-01-01"}
    work.update(extra)
    return work


def notice(kind, k, target_i, *, source="publisher"):
    target = f"10.5555/jisa-synthetic.{target_i:06d}"
    item = cr_article(5000 + k, doi=f"10.5555/jisa-synthetic.{kind}-{k}")
    item["title"] = [("Retraction notice to" if kind == "rn" else "Correction to") + f" synthetic security study number {target_i}"]
    item.pop("license", None)
    item["update-to"] = [{"DOI": target, "type": "retraction" if kind == "rn" else "correction", "source": source,
                          "label": "Retraction" if kind == "rn" else "Correction", "updated": date_obj(2024, 3, 1, True)}]
    return item


def jisa_shaped() -> dict:
    cr, oa = [], []
    for i in range(1, 2201):
        extra = {}
        if i in (10, 20, 30):
            extra["updated-by"] = [{"DOI": f"10.5555/jisa-synthetic.cn-{i // 10}", "type": "correction", "source": "publisher",
                                    "label": "Correction", "updated": date_obj(2024, 3, 1, True)}]
        if i == 40:
            extra["updated-by"] = [{"DOI": "10.5555/jisa-synthetic.rn-1", "type": "retraction", "source": "retraction-watch",
                                    "label": "Retraction", "updated": date_obj(2024, 5, 1, True), "record-id": 900001}]
        if i == 301:
            extra["relation"] = {"has-preprint": [{"id-type": "doi", "id": "10.48550/arxiv.2401.00001", "asserted-by": "subject"}]}
        cr.append(cr_article(i, **extra))
        if i > 2180:
            continue                                   # Crossref-only: no OpenAlex record
        oextra = {}
        if i == 100:
            work = oa_work(i, doi="10.5555/JISA-SYNTHETIC.000100")     # ASCII case variant of a Crossref DOI -> same key
        elif i == 300:
            work = oa_work(i, type="preprint")                         # type conflict: preprint vs journal-article
        elif i == 400:
            work = oa_work(i, title="A different synthetic title for study 400", display_name="A different synthetic title for study 400")
        elif i == 401:
            work = oa_work(i, publication_year=2013 + 401 % 14 + 1, publication_date=f"{2013 + 401 % 14 + 1}-01-01")
        elif i == 500:
            work = oa_work(i, source=REPO_SOURCE, alt_sources=(JISA_SOURCE,))  # venue elsewhere; journal only as an alternate location
        elif i == 40:
            work = oa_work(i, is_retracted=True)
        else:
            work = oa_work(i, **oextra)
        oa.append(work)
        if i == 200:
            oa.append(oa_work(i, wid="W8000000200", title="Synthetic security study number 200 (duplicate OpenAlex record)"))
    # Other Crossref work types and notices (kept in the inventory, never silently dropped)
    for k in range(1, 7):
        item = cr_article(6000 + k, doi=f"10.5555/jisa-synthetic.eb-{k}", type="other", title=[f"Editorial board {k}"])
        item.pop("license", None)
        cr.append(item)
        oa.append(oa_work(6000 + k, doi=f"10.5555/jisa-synthetic.eb-{k}", type="paratext", title=f"Editorial board {k}", display_name=f"Editorial board {k}"))
    for k in range(1, 5):
        item = cr_article(6100 + k, doi=f"10.5555/jisa-synthetic.sup-{k}", type="component", title=[f"Supplementary component {k}"])
        item.pop("license", None)
        cr.append(item)
    for k in range(1, 4):
        item = cr_article(6200 + k, doi=f"10.5555/jisa-synthetic.pr-{k}", type="peer-review", title=[f"Review report {k}"])
        item.pop("license", None)
        cr.append(item)
    for k in range(1, 3):
        item = cr_article(6300 + k, doi=f"10.5555/jisa-synthetic.issue-{k}", type="journal-issue", title=[f"Issue {k}"])
        item.pop("license", None)
        cr.append(item)
    for k, target in enumerate((10, 20, 30), start=1):
        cr.append(notice("cn", k, target))
    cr.append(notice("rn", 1, 40))
    cr.append(notice("rn", 2, 50))
    # DOI identity edge cases (S02 semantics)
    cr.append(cr_article(7001, doi="10.5555/jisa-synthetic.trail"))
    cr.append(cr_article(7002, doi="10.5555/jisa-synthetic.trail "))                 # trailing space: a different key, flagged
    cr.append(cr_article(7003, doi="10.5555/jisa-synthetic.Äx"))                  # upper-case A-umlaut
    oa.append(oa_work(7003, doi="10.5555/jisa-synthetic.%C3%A4x"))                     # lower-case a-umlaut: NOT the same DOI
    cr.append(cr_article(7004, doi="10.5555/jisa-synthetic.ét"))                  # decomposed e + combining acute
    oa.append(oa_work(7004, doi="10.5555/jisa-synthetic.%C3%A9t"))                     # precomposed: NFC variant, flagged, not merged
    cr.append(cr_article(7005, doi="10.5555/jisa-synthetic.a/b"))
    oa.append(oa_work(7005, doi="10.5555/jisa-synthetic.a%2Fb"))                       # percent-encoded slash: same DOI
    cr.append({"DOI": "10.5555/jisa-synthetic.malformed", "type": "journal-article", "title": "not-a-list"})  # malformed record
    # OpenAlex-only DOIs, records without DOI, an ambiguous DOI, and a preprint seen only as an alternate location
    for j in range(1, 16):
        oa.append(oa_work(8000 + j, doi=f"10.5555/jisa-synthetic.oa{j:03d}"))
    for j in range(1, 6):
        oa.append(oa_work(8100 + j, doi=None, wid=f"W{7000000000 + j}", title=f"Synthetic record without DOI {j}",
                          display_name=f"Synthetic record without DOI {j}"))
    oa.append(oa_work(8200, doi="10.5555/jisa-synthetic.x1%2010.5555/jisa-synthetic.x2", wid="W7100000001"))
    oa.append(oa_work(8300, doi="10.48550/arxiv.2401.00001", wid="W7200000001", source=ARXIV_SOURCE, alt_sources=(JISA_SOURCE,),
                      type="preprint", title="Synthetic security study number 301", display_name="Synthetic security study number 301"))
    second = {
        "replace_crossref": {"JISA": [
            cr_article(600, title=["Synthetic security study number 600 (revised title)"], deposited=date_obj(2026, 10, 20, True)),
            cr_article(601, license=[cr_by_dict()], deposited=date_obj(2026, 10, 20, True)),
            cr_article(40, deposited=date_obj(2026, 10, 20, True)),          # the provider no longer reports the retraction
            cr_article(9001, doi="10.5555/jisa-synthetic.new-0001", deposited=date_obj(2026, 10, 21, True)),
        ]},
        "remove_crossref_dois": {"JISA": ["10.5555/jisa-synthetic.rn-1", "10.5555/jisa-synthetic.001999"]},
        "replace_openalex": [oa_work(40, is_retracted=False, updated_date="2026-10-20T00:00:00"),
                             oa_work(600, title="Synthetic security study number 600 (revised title)", updated_date="2026-10-20T00:00:00")],
        "remove_openalex_ids": ["https://openalex.org/W9000001999"],
    }
    return {
        "schema": "jh-sim/1",
        "description": "SYNTHETIC JISA-shaped scenario (ISSNs only). Not JISA metadata. Owner-observed 2151 is NOT the fixture size.",
        "crossref_journals": {JISA_PRINT: {"title": JTITLE, "publisher": "Synthetic Publisher One (fixture)", "works_key": "JISA",
                                           "issn_type": [{"value": JISA_PRINT, "type": "print"}, {"value": JISA_E, "type": "electronic"}]},
                              JISA_E: {"title": JTITLE, "publisher": "Synthetic Publisher One (fixture)", "works_key": "JISA",
                                       "issn_type": [{"value": JISA_PRINT, "type": "print"}, {"value": JISA_E, "type": "electronic"}]}},
        "crossref_works": {"JISA": cr},
        "openalex_sources": [{"id": f"https://openalex.org/{JISA_SOURCE}", "display_name": JTITLE, "issn_l": JISA_PRINT,
                              "issn": [JISA_PRINT, JISA_E], "type": "journal", "host_organization_name": "Synthetic Publisher One"}],
        "openalex_works": oa,
        "settings": {"openalex_require_key_after_first_page": True,
                     "openalex_valid_key_sha256": [hashlib.sha256(SYNTHETIC_OPENALEX_TEST_KEY.encode()).hexdigest()]},
        "faults": [], "cursor_expiries": [],
        "mutations": [{"works_key": "JISA", "after_request": 4, "insert_front": [
            cr_article(9101, doi="10.5555/jisa-synthetic.inserted-1"), cr_article(9102, doi="10.5555/jisa-synthetic.inserted-2")]}],
        "profiles": {
            "interrupt_once": {"faults": [{"provider": "CROSSREF", "works_key": "JISA", "page_index": 5, "responses": [{"kind": "crash"}]}]},
            "after_restart": {"generation": 1, "faults": [], "mutations": []},
            "second_snapshot": dict(second, generation=2, faults=[], mutations=[]),
        },
    }


def cr_by_dict():
    return cc_by(2020, 1, 1)


def mock(prefix, works_key, issns, source, n, *, title, publisher, doi_stem, types=("journal-article",)):
    cr, oa = [], []
    for i in range(1, n + 1):
        y = 2015 + i % 10
        doi = f"10.5555/{doi_stem}.{i:05d}"
        cr.append({"DOI": doi, "type": types[i % len(types)], "title": [f"{title} synthetic paper {i}"], "container-title": [title],
                   "ISSN": issns, "issn-type": [{"value": v, "type": t} for v, t in zip(issns, ("print", "electronic"))],
                   "publisher": publisher, "volume": str(1 + i % 9), "issued": date_obj(y, 1 + i % 12),
                   "published-online": date_obj(y, 1 + i % 12, 1 + i % 27), "deposited": date_obj(2026, 2, 1, True),
                   "author": [{"given": "C.", "family": f"Mock{i}", "sequence": "first", "affiliation": []}]})
        if i % 9:
            oa.append({"id": f"https://openalex.org/W{prefix}{i:06d}", "doi": f"https://doi.org/{doi}", "title": f"{title} synthetic paper {i}",
                       "publication_year": y, "publication_date": f"{y}-{1 + i % 12:02d}-{1 + i % 27:02d}", "type": "article",
                       "primary_location": {"source": {"id": f"https://openalex.org/{source}", "display_name": title, "issn_l": issns[0],
                                                       "issn": issns, "type": "journal"}, "landing_page_url": f"https://doi.org/{doi}",
                                            "is_oa": False, "pdf_url": None, "license": None, "version": "publishedVersion"},
                       "locations": [], "best_oa_location": None, "open_access": {"is_oa": False, "oa_status": "closed", "oa_url": None},
                       "is_retracted": False, "authorships": [{"author": {"display_name": f"C. Mock{i}"}, "author_position": "first"}],
                       "biblio": {"volume": str(1 + i % 9)}, "updated_date": "2026-02-01T00:00:00"})
    return cr, oa


def three_publishers() -> tuple[dict, list]:
    a_cr, a_oa = mock("1", "ALPHA", ["0000-0019", "0000-0027"], "S910000001", 250, title="Synthetic Alpha Journal of Systems",
                      publisher="Mock Publisher Alpha (fixture)", doi_stem="alpha-synthetic", types=("journal-article",) * 9 + ("other",))
    b_cr, b_oa = mock("2", "BETA", ["0000-006X"], "S920000001", 180, title="Synthetic Beta Letters", publisher="Mock Publisher Beta (fixture)",
                      doi_stem="beta-synthetic")
    c_cr, c_oa = mock("3", "GAMMA", ["0000-0043"], "S930000001", 120, title="Synthetic Gamma Review", publisher="Mock Publisher Gamma (fixture)",
                      doi_stem="gamma-synthetic")
    scenario = {
        "schema": "jh-sim/1",
        "description": "SYNTHETIC three-journal batch styled after three different publishers. Not real publisher metadata.",
        "crossref_journals": {
            "0000-0019": {"title": "Synthetic Alpha Journal of Systems", "publisher": "Mock Publisher Alpha (fixture)", "works_key": "ALPHA",
                          "issn_type": [{"value": "0000-0019", "type": "print"}, {"value": "0000-0027", "type": "electronic"}]},
            "0000-0027": {"title": "Synthetic Alpha Journal of Systems", "publisher": "Mock Publisher Alpha (fixture)", "works_key": "ALPHA",
                          "issn_type": [{"value": "0000-0019", "type": "print"}, {"value": "0000-0027", "type": "electronic"}]},
            "0000-006X": {"title": "Synthetic Beta Letters", "publisher": "Mock Publisher Beta (fixture)", "works_key": "BETA",
                          "issn_type": [{"value": "0000-006X", "type": "electronic"}]},
            # Gamma is deliberately absent from the simulated Crossref: its lookups answer 404.
        },
        "crossref_works": {"ALPHA": a_cr, "BETA": b_cr, "GAMMA": c_cr},
        "openalex_sources": [
            {"id": "https://openalex.org/S910000001", "display_name": "Synthetic Alpha Journal of Systems", "issn_l": "0000-0019",
             "issn": ["0000-0019", "0000-0027"], "type": "journal"},
            {"id": "https://openalex.org/S920000001", "display_name": "Synthetic Beta Letters", "issn_l": "0000-006X", "issn": ["0000-006X"], "type": "journal"},
            {"id": "https://openalex.org/S930000001", "display_name": "Synthetic Gamma Review", "issn_l": "0000-0043", "issn": ["0000-0043"], "type": "journal"},
            {"id": "https://openalex.org/S930000002", "display_name": "Synthetic Gamma Review (duplicate source record)", "issn_l": "0000-0043",
             "issn": ["0000-0043"], "type": "journal"},
        ],
        "openalex_works": a_oa + b_oa + c_oa,
        "settings": {"openalex_require_key_after_first_page": True,
                     "openalex_valid_key_sha256": [hashlib.sha256(SYNTHETIC_OPENALEX_TEST_KEY.encode()).hexdigest()]},
        # Beta: OpenAlex answers 500 on its second works page every time -> PARTIAL after bounded retries.
        "faults": [{"provider": "OPENALEX", "works_key": "primary:S920000001", "page_index": 1,
                    "responses": [{"status": 500}, {"status": 503}, {"status": 500}, {"status": 502}]}],
        "cursor_expiries": [], "mutations": [], "profiles": {},
    }
    batch = [
        {"label": "Synthetic Alpha Journal of Systems (mock publisher Alpha)", "issns": ["0000-0019", "0000-0027"]},
        {"label": "Synthetic Beta Letters (mock publisher Beta)", "issns": ["0000-006X"]},
        {"label": "Synthetic Gamma Review (mock publisher Gamma)", "issns": ["0000-0043"]},
        {"label": "Entry with an invalid ISSN (isolation check)", "issns": ["1234-5678"]},
    ]
    return scenario, batch


def render() -> dict[str, bytes]:
    three, batch = three_publishers()
    readme = (
        "# S04J synthetic provider scenarios\n\n"
        "Generated by `tools/s04j_make_fixtures.py` (deterministic). ALL DATA IS SYNTHETIC: DOIs use the Crossref test prefix 10.5555;\n"
        "titles, authors, URLs (`*.example`) and counts are invented. Nothing here is real journal or publisher metadata.\n\n"
        "- `jisa_shaped/`: only the ISSNs (2214-2126 print, 2214-2134 electronic) follow the JISA pilot. 2,226 Crossref records,\n"
        "  an independent OpenAlex inventory with deliberate gaps, overlaps, conflicts and edge cases. The owner's historical\n"
        "  Crossref count (2151, 2026-11-01) is NOT the fixture size and is never an expected value.\n"
        "- `three_publishers/`: three mock journals styled after three publishers, plus `batch.json` (one invalid ISSN).\n\n"
        "The simulated OpenAlex accepts one synthetic test key for deep paging; only its SHA-256 is stored in the scenario.\n"
        "The key string itself lives in the test code and is not a credential for any real service.\n")
    return {"jisa_shaped/journal_sim.json": dumps(jisa_shaped()), "three_publishers/journal_sim.json": dumps(three),
            "three_publishers/batch.json": dumps(batch), "README.md": readme.encode("utf-8")}


def main(argv) -> int:
    files = render()
    if "--check" in argv:
        bad = [name for name, data in files.items() if not (OUT / name).is_file() or (OUT / name).read_bytes() != data]
        print(json.dumps({"fixtures_match_generator": not bad, "mismatched": bad,
                          "sha256": {n: hashlib.sha256(d).hexdigest() for n, d in sorted(files.items())}}))
        return 1 if bad else 0
    for name, data in files.items():
        (OUT / name).parent.mkdir(parents=True, exist_ok=True)
        (OUT / name).write_bytes(data)
    print(json.dumps({"written": sorted(files), "bytes": {n: len(d) for n, d in sorted(files.items())}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
