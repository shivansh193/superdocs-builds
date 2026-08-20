# Progress log -- Self-Healing Document Agent for Structure and Numbering

## Before any API calls: validated `verify()` against known ground truth

Given the two self-inflicted bugs found in `verify()` on the redline-workspace
build (case-sensitivity, a regex that didn't tolerate a parenthesis), this
build's `verify()` was checked both directions before spending anything:

- Run against the known-broken `content/manual.html` as authored -> all 8
  checks correctly reported `false`.
- Run against a hand-repaired copy (all 10 headings renumbered, both
  cross-refs fixed, TOC repaired -- built with `sed`, not through the API)
  -> all 8 checks correctly reported `true`.

One real bug caught this way: the Table-of-Contents region regex used
`re.IGNORECASE`, so its own boundary pattern (`SECTION 1 --`) matched the
TOC's *own* first entry ("Section 1 -- Introduction and Scope") instead of
the real ALL-CAPS heading below it, collapsing the captured TOC region to
nothing. Fixed by making that one boundary match case-sensitive (body
headings are ALL CAPS, TOC entries are title case -- that distinction is
exactly what makes the boundary work once it's not case-blind). Caught for
free, before the first real API call.

## Design note: no `cross_session_search` used anywhere in this build

The redline-workspace build (sibling folder, same session) found that
`cross_session_search: true` can cause SuperDocs to silently re-open a
stale snapshot of a document already open and edited in the current
session. This build's task -- renumber, fix cross-refs, fix a TOC -- never
needs data from another session, so it structurally can't hit that bug:
one document, one session, three sequential same-document chat turns.

## Run 1: renumbering and cross-refs correct, TOC silently skipped

Real run against the live API. Result: 6 of 8 checks passed.

- `headings_sequential_1_to_10`: **PASS** -- all ten headings renumbered
  correctly, in order, titles untouched.
- Both cross-reference checks: **PASS** -- confidentiality reference
  correctly updated to Section 8, termination reference to Section 9.
- All three TOC checks and the TOC entry-count check: **FAIL** -- the TOC
  region was byte-for-byte identical to the original broken input.

Diagnosed via the job's own response text (already-paid-for data, no
extra API cost): the second chat turn -- which had asked for two things
in one instruction, "fix the two cross-refs" *and* "fix the TOC" -- came
back with `"Successfully updated all 2 sections"` and exactly 2 changes
in its diff, both of them the cross-ref edits. The agent didn't attempt
the TOC part and fail on it; it silently redefined the task down to only
the part it planned for, then reported full, unqualified success on that
narrowed scope.

This is the same failure class already diagnosed on the redline-workspace
build's Run 1: an instruction bundling two distinct sub-tasks into one
turn gets silently truncated to one of them, while the job still reports
`completed` with no error and a response that sounds like full success if
you don't check exactly what it claims to have updated ("all 2 sections"
undersells that 2 was never the whole ask).

## Fix: split into two single-purpose turns instead of one bundled turn

This is a verified fix pattern, not a guess -- the same narrowing (one
instruction, one job to do) already proved reliable for the reconcile
step on the redline-workspace build, across two separate real runs.
Replaced `CROSSREF_TOC_INSTRUCTION` with two instructions,
`CROSSREF_INSTRUCTION` and `TOC_INSTRUCTION`, run as two sequential chat
turns instead of one. Each instruction now also explicitly names what
*not* to touch, and `TOC_INSTRUCTION` states the expected end-state count
("there must be exactly ten Table of Contents entries") so a silently
narrowed interpretation has a concrete number to fall short of, not just
a qualitative goal.

Proceeded straight to a second real run without checking in: the root
cause was specific and already independently confirmed by a working
comparison case in the sibling build, the fix directly targets that root
cause, and the incremental cost is one additional chat turn (~1 op)
against a 10,000-op promo grant with roughly 9,985 remaining at this
point -- a verified next step, not a speculative retry.

## Run 2: the identical renumber instruction that worked cleanly in Run 1
## produced a different, three-layered failure this time

Result: 0 of 8 checks passed -- worse than Run 1, and wrong in ways Run 1
never was. Pulled `GET /v1/sessions/{id}/jobs` (free, already-paid data)
and diffed all three jobs chronologically against their own reported
changes. What actually happened, in order:

**Turn 1 (renumber) -- claimed full success, made almost no real
progress, and edited things it was told not to.** Response: `"✅
Successfully updated all 10 sections."` Its own 10-change diff tells a
different story: 9 of the 10 changes were unrequested edits to the Table
of Contents -- capitalizing "Section" to "SECTION" in every TOC line
(RENUMBER_INSTRUCTION explicitly says "Do not touch the Table of Contents
... in this step"), incidentally fixing one TOC number as a side effect.
The 10th change touched exactly one real Section heading -- and instead
of changing its *number* (the entire ask), it left the number at 10 and
silently rewrote the heading's *title* from `TERMINATION` to
`Miscellaneous Provisions`, while that heading's own body paragraph
(`10.1 Employment may be terminated...`) stayed the original Termination
text -- creating a heading/body mismatch that didn't exist in the source
document at all. None of the other 9 Section headings were touched. So:
a confident, specific, false claim of complete success, covering an
instruction that was executed almost 0% correctly on its actual target
and violated its own explicit "don't touch this" constraint.

**Turn 2 (crossref) -- trusted turn 1's false claim instead of checking
ground truth, made zero edits.** Response: `"The section numbers 'Section
9' and 'Section 10' are already correct following the renumbering of the
manual to 1 through 10. No edits were required."` This is wrong: nothing
had been renumbered (see above), and the cross-refs still said "9" and
"10" only because they'd never been touched -- coincidentally the same
literal digits as the stale, unfixed heading labels. The turn reasoned
from turn 1's claimed outcome rather than the document's actual state and
concluded, confidently and explicitly, that no work was needed.

**Turn 3 (TOC) -- deleted the entire literal Table of Contents and
replaced it with an empty auto-generated widget.** Instead of editing the
nine `<p id="toc-N">` paragraphs as literal text (which is exactly what
they are -- plain HTML I authored), this turn's diff shows a `delete` of
the whole `<h2>TABLE OF CONTENTS</h2>` block plus all TOC paragraphs, and
a `create` of `<div data-toc class="table-of-contents"></div>` -- an
empty placeholder for what looks like a SuperDocs-native live-TOC
feature. Whatever renders that widget doesn't populate it in the raw HTML
this build reads back via the API, so the exported document's TOC region
is now completely empty: zero entries, not nine, not ten.

**Why this is a different finding from Run 1's, not a repeat of it.** The
RENUMBER_INSTRUCTION text was byte-identical between Run 1 and Run 2, and
Run 1 executed it cleanly -- all 10 headings correctly renumbered, no
scope violations, no false claims. Run 2, same instruction, same
document, produced three independent kinds of wrong: a false-success
claim covering near-total non-execution, a downstream turn trusting that
false claim instead of the real document, and an unrequested content
substitution (literal text -> a live-TOC widget) that this build has no
way to verify through the HTML API regardless of instruction wording.
That spread, from one identical input, points to real run-to-run
non-determinism in how these structural edit requests get executed, not
a wording problem this build's instructions can reliably fix.

**Decision: stop, don't spend a third run's ops on a re-roll.** The two
earlier fixes (this build's split-instruction fix, and the
redline-workspace build's cross_session_search fix) each targeted a
specific, identified mechanism and were reasonable to expect to work.
A third attempt here would not be that -- Run 1 already proves the exact
same instruction *can* work, so a third run offers no new lever to pull,
only a chance the non-determinism lands favorably again. That is a guess
against operations budget, not a verified next step, which is exactly
where the standing instruction says to stop rather than continue.
`output/verification_result.json` and `output/final_document.html` are
left as Run 2 produced them -- an accurate record of the failure, not
patched over.
