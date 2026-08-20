# Progress log -- Owner-Contractor Agreement Redline Workspace

## 2026-08-20 -- reconciliation verified, redline surfaces a real platform bug

### Run 1 (first real run, no `--dry-run`)

Both chat jobs (`reconcile`, `redline`) reported `status: "completed"`, but
`GET /v1/sessions/{id}/documents?include_html=true` afterward showed the
focused document completely unmodified from the original upload -- no
reconciliation, no redline. Cross-checked against `GET /v1/sessions/{id}/jobs`:
both jobs' `result.response` text admitted failure in prose
(`"I couldn't access the requested sections of the document."` /
`"I wasn't able to complete the per-document work."`) while the job status
itself never said `failed`.

Diagnosed via two cheap, targeted live-API calls instead of blind retries:

1. A minimal 2-document test with a unique marker string -- succeeded,
   proving background-document reading works in principle.
2. A single targeted question on the still-live real session
   ("what does SC-1 say?") -- succeeded with the fully correct answer.

Conclusion: the original `RECONCILE_INSTRUCTION` and `REDLINE_INSTRUCTION`
were too open-ended for one turn (read N documents, synthesize a full
rewrite, invent formatting, all at once) -- not a categorical platform
limit. Rewrote both to be narrower and procedural: one document to read,
one thing to extract from it, one edit rule, applied one category at a
time. Also fixed a real bug in `main()`: it picked the first session
document with non-empty `html` rather than matching by title, which would
have silently graded the wrong document if the roster ever came back in a
different order.

### Run 2 (after the instruction rewrite and the document-selection fix)

**Reconciliation: verified working correctly, end to end.** Not just
inferred from the final export -- the reconcile job's own before/after
diff (`GET /v1/sessions/{id}/jobs`) shows Article 5.1 changed from
`"forty-five (45) days"` to `"twenty-one (21) days ... (as amended by
SC-1)"`, with the job's `ai_explanation` correctly noting that SC-2 and
SC-3 were reviewed and correctly judged not to be amendments to existing
Article text. This is the hardest check in the build (proving the base
agreement's 45-day term, which independently violates the playbook's
23-day threshold, was actually reconciled to the amended 21-day term
*before* redlining happened) and it held up under direct inspection.

**Redline: a real, reproducible SuperDocs bug, not a build mistake.**
The redline chat call uses `cross_session_search: true` so the playbook's
five thresholds are retrieved from a separate prior session rather than
re-pasted into the instruction -- proof the agent genuinely searched
memory rather than pattern-matching generic contract norms. Its own
`intermediate_responses` log two `open_document` operations: one for
`risk_playbook` (expected -- that's the intended cross-session retrieval)
and a second, unrequested one for `base_agreement` -- the document already
open and freshly reconciled in *this* session.

That second open pulled in a stale snapshot: its HTML shows Article 5.1
back at **45 days** (the pre-reconciliation figure), with a completely
different set of `data-chunk-id` UUIDs than the session's actual live
document (e.g. `h1 data-chunk-id="33047d90-..."` in the redline job's
snapshot vs. `h1 data-chunk-id="1085cde4-..."` in the reconcile job's --
same document, same content, different identity). Working from that stale
copy, the agent correctly judged 45 days > 23-day threshold and flagged
it -- a *locally correct* judgment made against the *wrong* document
state. The job reported the edit as `"approved"` and the job itself
`"completed"`. But because the chunk ID it edited doesn't exist in the
session's real current document, the edit never actually applied there:
`GET /v1/sessions/{id}/documents` immediately after both jobs still shows
the correctly-reconciled, unflagged 21-day text -- and neither of the two
genuinely-required flags (Article 6, notice period; Article 7,
indemnification) was ever computed at all, because the job's one
"parallel edit" pass was spent on the phantom Article 5 violation instead.

Net effect: **a chat job can report `completed`, with a specific,
plausible-looking approved diff, while that diff has zero effect on the
document the session actually holds -- and nothing in the API response
signals the divergence.** The only way to catch it was comparing chunk-id
UUIDs across two different jobs' snapshots of "the same" document, which
isn't something a caller would normally think to do. This is a sharper
finding than Run 1's silent-failure-in-prose bug: that one at least made
the mismatch visible in the `response` text if you read it; this one
reports success at every layer that matters (`status`, `changes[].status`,
`ai_explanation`) and is only detectable via document-identity metadata a
caller has no obvious reason to cross-check.

Working theory for the trigger, not confirmed: `cross_session_search`
resolves by document *title* across all of the account's sessions, not
scoped to "only search for things not already open here." Run 1's failed
session had also uploaded a document titled `base_agreement`, never
edited (since Run 1's reconcile job silently did nothing) -- a very
plausible candidate for the stale copy that got re-opened, though a
same-session naming collision without any Run 1 leftover would produce
the same symptom.

### Decision: stop here, don't blind-retry against operations budget

Also found and fixed, while diagnosing: `verify()` compared literal
`"Article 7"` against document text that actually reads `"ARTICLE 7"`
(all-caps headings), so every `article_found` check was silently `false`
regardless of real content -- fixed to case-insensitive search. Its
reconciliation regex (`21\s*day`) also didn't match the real text
`"twenty-one (21) days"` because of the parenthesis -- fixed to
`21\)?\s*day`. Both were bugs in this repo's own verification script, not
platform behavior; re-running the fixed `verify()` against the existing
Run 2 export (no API cost) gives the accurate final picture below.

Given the root cause of the redline failure isn't fully pinned down
(cross-session title collision vs. a more general re-open-on-touch
behavior), a third run risks reproducing the same failure for the same
reason and spending ops without new information. Reported the honest,
well-diagnosed result instead of retrying blind.

## Final verification result (Run 2, corrected `verify()`, no further API calls)

| Check | Expected | Actual | Result |
|---|---|---|---|
| Article 7 (indemnification) flagged | yes | no | **FAIL** |
| Article 6 (notice period) flagged | yes | no | **FAIL** |
| Article 8 (damages waiver) flagged | no | no | PASS |
| Article 5 (payment terms) flagged | no | no | PASS |
| Article 9 (termination) flagged | no | no | PASS |
| Reconciliation applied (shows 21 days, not 45) | yes | yes | **PASS** |

Overall: **FAIL** (4 of 6 checks correct). The hardest check --
reconciliation actually landing before redlining ran -- passed cleanly.
Both failures trace to the single stale-reopen bug above, not to two
independent problems.
