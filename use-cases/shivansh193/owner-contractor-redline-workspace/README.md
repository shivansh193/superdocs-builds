# Owner-Contractor Agreement Redline Workspace

Built by Shivansh Kalra for the SuperDocs task.

Reconciles a base Owner-Contractor Agreement with its Supplementary
Conditions into one effective document, then redlines that effective
document against a risk playbook retrieved from a separate session --
indemnity mutuality, damages-waiver mutuality, notice periods, payment
terms, and termination-for-convenience notice, each against a deliberately
non-"standard" numeric threshold (11 business days, 23 days, 17 days, not
the round 10/14/30 a model would guess from generic contract knowledge).

All content is synthetic: a fictional owner (Riverside Medical Partners
LLC), a fictional contractor (Meridian Builders LLC), and a fictional
internal risk playbook.

Two things were deliberately engineered to be independently verifiable,
not just plausible-looking -- see [Verified result](#verified-result) for
which one actually held up:

1. The risk playbook is established in a separate prior session and
   referenced only via `cross_session_search: true` -- never re-pasted
   into the redline instruction. A correct flag against one of its
   specific, arbitrary thresholds would be evidence the search genuinely
   retrieved the playbook, not that the model pattern-matched typical
   contract norms.
2. The base agreement's payment term (45 days) genuinely violates the
   playbook's threshold (>23 days) on its own -- but the Supplementary
   Conditions amend it to 21 days, which is compliant. If the final
   document treats payment terms as compliant, that's evidence real
   reconciliation happened *before* redlining, not that the base document
   was redlined in isolation while ignoring the amendment.

## What it does

1. Uploads the risk playbook to a throwaway "setup" session and has
   SuperDocs summarize it, so it exists in cross-session memory.
2. Opens four documents together in a second session: the base agreement
   (focused), Supplementary Conditions, and two Exhibits (background).
3. **Reconcile step**: instructs SuperDocs to read the Supplementary
   Conditions, find its numbered amendments, and edit the corresponding
   Articles in the base agreement in place -- every other Article must
   stay present and unchanged.
4. **Redline step**: instructs SuperDocs to retrieve the risk playbook via
   `cross_session_search`, check the now-reconciled document's actual
   terms against each of the playbook's five thresholds in turn, and
   insert a red `RISK FLAG:` paragraph after any Article that violates
   its threshold.
5. Exports the result as `.docx` and verifies it programmatically against
   six checks (five per-Article flag/no-flag expectations plus the
   reconciliation check itself) by inspecting the real returned HTML, not
   by asserting success.

## How to run it

```bash
python -m venv .venv
.venv/Scripts/activate    # or source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
cp .env.example .env      # then set SUPERDOCS_API_KEY
python build.py --dry-run # prints the full plan, zero API calls
python build.py           # runs it for real: ~5 uploads, 2 chat turns, 1 export
```

## SuperDocs features used

- **Multi-document sessions** (`open_mode: "replace"` / `"background"`) --
  four related contract documents open together, one focused
- **Cross-session memory + `cross_session_search`** -- the risk playbook
  lives in a separate session and is retrieved by search, not re-pasted
- **Chat / async edit** (`POST /v1/chat/async`) with
  `approval_mode: "ask_every_time"` across two sequential instructions on
  the same focused document
- **Export** (`POST /v1/documents/export`, `.docx`)
- **Job introspection** (`GET /v1/sessions/{id}/jobs`) -- used here not
  just to poll status but to directly diff each job's approved
  before/after HTML, which is how both real findings below were caught

## Verified result

**Reconciliation: verified working correctly, end to end.** Not inferred
from the final export -- the reconcile job's own before/after diff shows
Article 5.1 changed from `"forty-five (45) days"` to `"twenty-one (21)
days ... (as amended by SC-1)"`, and its `ai_explanation` correctly notes
that the Supplementary Conditions' other two clauses were reviewed and
correctly judged to be new obligations, not amendments to existing
Article text. This was the hardest of the six checks (proving the
independently-violating 45-day term was actually reconciled *before*
redlining ran) and it held up under direct inspection.

**Redline: exposed a real, reproducible SuperDocs platform bug.** The
`cross_session_search`-enabled redline call, per its own
`intermediate_responses`, opened *two* documents by name: the intended
`risk_playbook`, and a second, unrequested `base_agreement` -- the
document already open and freshly reconciled in the same session. That
second open pulled in a stale snapshot (Article 5.1 back at 45 days, with
an entirely different set of `data-chunk-id` UUIDs than the session's real
current document). Working from that stale copy, the agent correctly
judged 45 > 23 days and flagged it -- a locally correct judgment against
the wrong document state. The job reported the resulting edit as
`"approved"` and the job itself `"completed"`, but because its chunk ID
doesn't exist in the session's real document, the edit never actually
applied there. The two genuinely-required flags (Article 6 notice period,
Article 7 indemnification) were never computed at all, because the job's
one edit pass went to the phantom Article 5 violation instead.

Net effect: **a chat job can report `completed`, with a specific,
plausible-looking approved diff, while that diff has zero effect on the
document the session actually holds -- and nothing in the response
signals the divergence.** Full technical trace, including the exact job
diffs and chunk IDs involved, is in [`PROGRESS.md`](PROGRESS.md).

| Check | Expected | Actual | Result |
|---|---|---|---|
| Article 7 (indemnification) flagged | yes | no | **FAIL** |
| Article 6 (notice period) flagged | yes | no | **FAIL** |
| Article 8 (damages waiver) flagged | no | no | PASS |
| Article 5 (payment terms) flagged | no | no | PASS |
| Article 9 (termination) flagged | no | no | PASS |
| Reconciliation applied (21 days, not 45) | yes | yes | **PASS** |

Overall: **FAIL** (4 of 6). Both failures trace to the single stale-reopen
bug above, not to two independent problems -- and the check that was
actually the point of the exercise (real reconciliation before redlining)
passed cleanly.

## Honest limitations

- The redline step does not reliably produce risk flags today because of
  the platform bug described above. Re-running without
  `cross_session_search` (uploading the playbook into the main session as
  a background document instead, the same pattern used for the Exhibits)
  would very likely produce a clean pass, but that would remove the part
  of the design meant to prove genuine cross-session retrieval rather
  than pattern-matched generic contract knowledge -- left as-is rather
  than quietly working around the bug it was built to demonstrate.
- `output/` is gitignored; run `python build.py` to regenerate
  `reconciled_and_redlined_agreement.docx` (reflects the correctly
  reconciled, not-yet-redlined state), `final_document.html`, and
  `verification_result.json`.

## Files

- `build.py` -- upload -> reconcile -> redline -> verify -> export flow,
  plus `--dry-run`
- `content/base_agreement.html`, `supplementary_conditions.html`,
  `exhibit_a_scope.html`, `exhibit_b_insurance.html` -- the contract
  documents
- `content/risk_playbook.html` -- the internal risk checklist, retrieved
  via cross-session search rather than re-pasted
- `PROGRESS.md` -- full diagnostic trace of both runs, including the
  exact job diffs and chunk-ID evidence for the platform bug
