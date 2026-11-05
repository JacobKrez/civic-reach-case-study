# Authorship

FoodPath has one human author, Jacob Krzeminski. The commit history shows
several other names, and this note says what they are.

| Name in `git log` | What it is |
|---|---|
| `Bob`, `bob` | An AI coding agent (Claude Code and Hermes sessions) that Jacob ran to implement work items. Most commits carry this name. |
| `Manuel`, `manuel`, `Manuel (civicreach watchdog)` | A second agent identity. Its commits are fixes and documentation, including a guardrail fix and the architecture write-up. |
| `civic-agent` | A third agent identity, used for two simulator commits. |
| `Jacob`, `Jacob Krzeminski` | Jacob, committing directly. |

The `.local` and `hermes` email domains on those commits are machine-local
placeholders. They are not real mailboxes and they do not belong to anyone
else.

**Who did what.** Jacob wrote the work items the agents built from, set the
thresholds in [`QUALITY_BAR.md`](QUALITY_BAR.md), decided what the gate
in `scripts/gate.py` checks before any change counts as done, and decided
what merged. The agents wrote most of the code and most of the commit messages.
The work items carry identifiers such as W38, W48 and W65, which is why
those labels appear in commit messages and documents.

**Why the history was not rewritten.** Rewriting the author fields would
make the log look like one person typed every line, which is not what
happened. The history is left as it is because it is an accurate record of
how the project was built: one person directing several agent sessions,
with a gate that none of them could bypass.
