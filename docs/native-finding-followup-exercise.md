# Bounded native finding follow-up exercise

The already-running native actor can exercise [finding follow-ups](pr/finding-followups.md)
against frozen evidence and the offline publisher in
`tests/pr/fixtures/feedback_reply_exercise.py`. Do not use an unrelated live PR.
Do not launch a new model session or replace unavailable independent verification
with an external runner. When a fresh native verifier is unavailable, keep that
production requirement explicitly incomplete; supplied fixture verification is
only a stand-in for this bounded interpretation sample.

Supply complete original sources for an inline finding, a review-body finding
with an embedded file/line reference and a top-level report with an explicit
report-head link. Give the actor a pinned A/B/C PR commit list, a frozen current
head reproduction/disposition and the current recipe, skill and follow-up
guidance. Ask the actor to group equivalent substantive requests, select source
references and latest request anchors, decide whether each case merits a reminder
and author the targeted correction. Do not supply the expected publication
destination as its task. The structured inputs in `tests/pr/test_finding_followups.py`
can be used as frozen sources, with findings assessed by the actor rather than
the test's preselected assessment factory.

Use separate isolated fixture directories for the three source-route cases so
one case's legitimate reply does not create a recent request for the next case.
Feed each actor-authored assessment to the actual transient planner. With
explicit fixture-only posting authorization, submit eligible generated requests
through the offline publisher interface. Lose at least one write response and
recover its original operation read-only. Reinspect existing journal operations
with unchanged frozen discussion to simulate discussion-read lag, and assess
an unchanged fresh-session rerun. Inspect source, original parent, exact body,
receipts and write counts. Do not create a second registry or modify operation
identities to make the sample pass.

Also assess one-new-commit, unavailable latest anchor, fixed finding,
formatting-only bot-summary edit and denied-posting cases. Check that recent or
uncertain age does not dismiss a still-confirmed finding, and that an eligible
plan's `publicationAuthority:false` is preserved when the actor declines posting
under denied permission. Ordinary review, CI and merge gates remain separate;
this exercise does not establish their execution.

In the issue #199 implementation sample, the already-running actor authored
eight bounded decisions from frozen sources and simulated independent review
evidence. Three independently isolated source-route cases produced one fixture
write each: an inline reply under root 41 preserving source comment 99, and two
source-linked top-level replies preserving review 789 and issue comment 901.
The inline write lost its response and reconciled without a second send. All
three unchanged reruns discovered their original verified operation and returned
`recent_request` despite unchanged discussion. One additional commit returned
`recent_request`, an unknown latest anchor returned `uncertain_age`, a fixed
finding returned `excluded`, and a formatting-only summary edit retained the
unchanged finding's two-commit age. Denied posting produced zero writes while
the finding remained confirmed. This is an in-session interpretation sample,
not independent native delegation, live GitHub capture, full workflow execution
or measured API dollar savings. Offline tests separately exercise complete
capture, capped-list local history, source changes and publication recovery.
