"""Owner-Contractor Agreement Redline Workspace -- built against the real,
hosted SuperDocs product. Reconciles a base Owner-Contractor Agreement,
Supplementary Conditions, and two Exhibits into one effective document
(the task doc calls this "the genuinely hard part"), then redlines that
effective document against a risk playbook -- indemnity, damages waiver,
notice periods, payment terms, termination for convenience.

Two things are deliberately engineered to be independently verifiable,
not just plausible-looking:

1. The risk playbook is established in a *separate* prior session and
   referenced only via `cross_session_search: true` in the redline step --
   never re-pasted into the redline instruction. Its thresholds are
   arbitrary, non-"standard" numbers (11 business days, 23 days, 17 days --
   not the round 10/14/30 a model would guess from generic contract
   knowledge). A correct flag against one of these specific numbers is
   evidence the search genuinely retrieved the playbook, not that the
   model pattern-matched typical contract norms.

2. The base agreement's payment term (45 days) genuinely violates the
   playbook's threshold (>23 days) on its own -- but the Supplementary
   Conditions amend it to 21 days, which is compliant. If the final
   redline treats payment terms as compliant, that's evidence real
   reconciliation happened *before* redlining, not that the base
   document was redlined in isolation while ignoring the amendment.

Run `python build.py --dry-run` first: prints the full plan (uploads,
exact chat instructions, what verification will check) with zero API
calls. Only run for real (`python build.py`) after reading that output.
"""

import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.superdocs.app"
HERE = Path(__file__).parent
CONTENT_DIR = HERE / "content"
OUTPUT_DIR = HERE / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------- API helpers ----------


class Client:
    def __init__(self, api_key: str):
        self.http = httpx.Client(base_url=BASE_URL, headers={"Authorization": f"Bearer {api_key}"}, timeout=240.0)

    def upload_document(self, path: Path, session_id: str, open_mode: str = "replace") -> dict:
        with open(path, "rb") as f:
            resp = self.http.post(
                "/v1/documents/upload",
                files={"file": (path.name, f, "text/html")},
                data={"session_id": session_id, "open_mode": open_mode},
            )
        resp.raise_for_status()
        return resp.json()

    def start_chat(self, message: str, session_id: str, approval_mode: str = "ask_every_time", cross_session_search: bool = False) -> dict:
        body = {"message": message, "session_id": session_id, "approval_mode": approval_mode}
        if cross_session_search:
            body["cross_session_search"] = True
        resp = self.http.post("/v1/chat/async", json=body)
        resp.raise_for_status()
        return resp.json()

    def get_job(self, job_id: str) -> dict:
        resp = self.http.get(f"/v1/jobs/{job_id}")
        resp.raise_for_status()
        return resp.json()

    def approve_all(self, session_id: str, job_id: str, pending_changes: list[dict]) -> None:
        changes = [{"change_id": c["change_id"], "approved": True} for c in pending_changes]
        resp = self.http.post(f"/v1/chat/{session_id}/approve", json={"job_id": job_id, "approved": True, "changes": changes})
        resp.raise_for_status()

    def continue_job(self, session_id: str, job_id: str) -> None:
        resp = self.http.post(f"/v1/chat/{session_id}/continue", json={"job_id": job_id, "continue": True})
        resp.raise_for_status()

    def wait_for_job(self, session_id: str, job_id: str, label: str, max_wait_s: int = 400) -> dict:
        start = time.time()
        while time.time() - start < max_wait_s:
            job = self.get_job(job_id)
            status = job["status"]
            if status == "completed":
                log(f"  {label}: completed")
                return job
            if status in ("failed", "cancelled"):
                raise RuntimeError(f"{label} job {status}: {job.get('error')}")
            if status == "awaiting_approval":
                metadata = job.get("metadata") or {}
                if metadata.get("awaiting_kind") == "continue_prompt":
                    log(f"  {label}: paused mid-edit, continuing")
                    self.continue_job(session_id, job_id)
                else:
                    pending = metadata.get("pending_changes") or []
                    log(f"  {label}: awaiting approval on {len(pending)} change(s) -- approving")
                    self.approve_all(session_id, job_id, pending)
            else:
                log(f"  {label}: {status}...")
            time.sleep(4)
        raise TimeoutError(f"{label} job did not complete in time")

    def session_documents(self, session_id: str, include_html: bool = True) -> dict:
        resp = self.http.get(f"/v1/sessions/{session_id}/documents", params={"include_html": str(include_html).lower()})
        resp.raise_for_status()
        return resp.json()

    def export_html(self, html: str, filename: str, fmt: str = "docx") -> Path:
        resp = self.http.post("/v1/documents/export", json={"html": html, "format": fmt, "options": {"filename": filename}})
        resp.raise_for_status()
        ext = {"docx": "docx", "pdf": "pdf", "html": "html"}.get(fmt, fmt)
        out_path = OUTPUT_DIR / f"{filename}.{ext}"
        if "application/json" in resp.headers.get("content-type", ""):
            data = resp.json()
            url = data.get("download_url") or data.get("url")
            out_path.write_bytes(self.http.get(url).content)
        else:
            out_path.write_bytes(resp.content)
        return out_path


# ---------- the plan (shared by --dry-run and the real run) ----------

RECONCILE_INSTRUCTION = (
    "There is another document open in this session called supplementary_conditions. Read it specifically. "
    "It contains one or more numbered amendments (labeled like 'SC-1'); each one names which Article of "
    "THIS document it amends and states the new term. For each amendment you find in supplementary_conditions: "
    "edit the corresponding Article in THIS document so it states the new term instead of the old one, and "
    "add a short parenthetical note right after the changed sentence naming which amendment made the change, "
    "for example '(as amended by SC-1)'. Do not change any Article that supplementary_conditions doesn't "
    "amend, and do not summarize, shorten, or remove any other part of this document -- every Article must "
    "still be present with its number and full text, unchanged except where an amendment applies."
)

REDLINE_INSTRUCTION = (
    "Search your memory of previous sessions for a document called the Meridian Builders Owner Contract "
    "Risk Playbook, and retrieve its content -- do not ask me what it says. It lists five numbered risk "
    "categories, each with one specific numeric threshold. Work through the five categories one at a time, "
    "in order. For each one: find the Article in this document that covers that category, compare this "
    "document's actual current term for it against that category's threshold, and only if it violates the "
    "threshold, insert one new paragraph directly after that Article's text: start it with the literal text "
    "'RISK FLAG:', explain which threshold is violated and by how much, and make the whole paragraph red "
    "using style=\"color:#b00\". If a category's term already meets the threshold, insert nothing for it and "
    "move to the next category. Some of the five will need a flag and some won't."
)


def print_dry_run() -> None:
    print("=== DRY RUN -- no API calls will be made ===\n")
    print("Documents that would be uploaded to a setup session (session A):")
    print(f"  - {CONTENT_DIR / 'risk_playbook.html'}")
    print()
    print("Documents that would be uploaded to the main session (session B), in order:")
    for name, mode in [
        ("base_agreement.html", "replace (becomes focused)"),
        ("supplementary_conditions.html", "background"),
        ("exhibit_a_scope.html", "background"),
        ("exhibit_b_insurance.html", "background"),
    ]:
        print(f"  - {CONTENT_DIR / name}  [{mode}]")
    print()
    print("Chat instruction 1 (reconcile, targets the focused base_agreement doc, no document_id set):")
    print(f"  {RECONCILE_INSTRUCTION[:200]}...")
    print()
    print("Chat instruction 2 (redline, same focused doc, cross_session_search=true):")
    print(f"  {REDLINE_INSTRUCTION[:200]}...")
    print()
    print("Expected verification result (against the source documents as authored):")
    print("  FLAG expected  : Article 7 (indemnification) -- one-directional in the base, never amended")
    print("  FLAG expected  : Article 6 (notice period) -- 5 business days < 11-day playbook threshold")
    print("  NO FLAG expected: Article 8 (damages waiver) -- already mutual in the base")
    print("  NO FLAG expected: Article 5 (payment terms) -- 45 days in the base (would violate on its")
    print("                    own) but Supplementary Conditions SC-1 amends it to 21 days, which is")
    print("                    compliant with the 23-day threshold -- this is the reconciliation check")
    print("  NO FLAG expected: Article 9 (termination) -- 30 days >= 17-day threshold")
    print()
    print("API calls this would make for real: 5 uploads, 2 chat turns (+ approvals), 1-2 exports.")
    print("Re-run without --dry-run once this plan looks right.")


def _norm(s: str) -> str:
    return re.sub(r"[_\-\s]+", " ", (s or "")).strip().lower()


def find_document_html(doc_list: dict, title_substring: str) -> str:
    needle = _norm(title_substring)
    for d in doc_list.get("documents", []):
        if needle in _norm(d.get("title")):
            html = d.get("html")
            if not html:
                raise ValueError(f"document matching '{title_substring}' found but has no html: {d}")
            return html
    raise ValueError(f"no open document matching '{title_substring}' -- got {doc_list}")


# ---------- verification ----------


def verify(html: str) -> dict:
    import re

    checks = {
        "indemnification_flagged": ("Article 7", True),
        "notice_period_flagged": ("Article 6", True),
        "damages_waiver_flagged": ("Article 8", False),
        "payment_terms_flagged": ("Article 5", False),
        "termination_flagged": ("Article 9", False),
    }
    results = {}
    for check_name, (article, should_be_flagged) in checks.items():
        m = re.search(re.escape(article), html, re.IGNORECASE)
        idx = m.start() if m else -1
        window = html[idx : idx + 1200] if idx != -1 else ""
        has_flag = "RISK FLAG" in window
        results[check_name] = {
            "article_found": idx != -1,
            "flagged": has_flag,
            "expected_flagged": should_be_flagged,
            "correct": has_flag == should_be_flagged,
        }

    payment_shows_21 = bool(re.search(r"21\)?\s*day", html, re.IGNORECASE))
    payment_shows_stale_45 = bool(re.search(r"45\)?\s*day", html, re.IGNORECASE))
    results["reconciliation_applied"] = {
        "shows_amended_21_days": payment_shows_21,
        "still_shows_stale_45_days": payment_shows_stale_45,
        "correct": payment_shows_21 and not payment_shows_stale_45,
    }

    all_correct = all(r["correct"] for r in results.values())
    return {"pass": all_correct, "details": results}


# ---------- main ----------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        print_dry_run()
        return

    api_key = os.environ.get("SUPERDOCS_API_KEY")
    if not api_key:
        print("SUPERDOCS_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    client = Client(api_key)

    # --- setup session: establish the risk playbook for cross-session search ---
    setup_session = f"playbook-setup-{uuid.uuid4()}"
    log(f"setup session: {setup_session}")
    client.upload_document(CONTENT_DIR / "risk_playbook.html", setup_session, open_mode="replace")
    job = client.start_chat(
        "Read this risk playbook and confirm you understand it. Just summarize the five categories in one line each.",
        setup_session,
        approval_mode="auto-apply",
    )
    client.wait_for_job(setup_session, job["job_id"], "playbook setup")

    # --- main session: open all four contract documents together ---
    main_session = f"redline-{uuid.uuid4()}"
    log(f"main session: {main_session}")
    for name, mode in [
        ("base_agreement.html", "replace"),
        ("supplementary_conditions.html", "background"),
        ("exhibit_a_scope.html", "background"),
        ("exhibit_b_insurance.html", "background"),
    ]:
        client.upload_document(CONTENT_DIR / name, main_session, open_mode=mode)
        log(f"  opened {name} ({mode})")

    log("reconciling into one effective document")
    job = client.start_chat(RECONCILE_INSTRUCTION, main_session, approval_mode="ask_every_time")
    client.wait_for_job(main_session, job["job_id"], "reconciliation")

    log("redlining against the risk playbook (cross-session search, playbook not re-pasted)")
    job = client.start_chat(REDLINE_INSTRUCTION, main_session, approval_mode="ask_every_time", cross_session_search=True)
    client.wait_for_job(main_session, job["job_id"], "redline")

    docs = client.session_documents(main_session, include_html=True)
    html = find_document_html(docs, "base_agreement")

    result = verify(html)
    log("verification:")
    for name, detail in result["details"].items():
        log(f"  {name}: {json.dumps(detail)}")
    log(f"OVERALL: {'PASS' if result['pass'] else 'FAIL'}")

    export_path = client.export_html(html, "reconciled_and_redlined_agreement", fmt="docx")
    log(f"exported -> {export_path}")

    (OUTPUT_DIR / "final_document.html").write_text(html, encoding="utf-8")
    (OUTPUT_DIR / "verification_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    if not result["pass"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
