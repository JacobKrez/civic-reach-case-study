# Evaluation guide

## Continuous red-team lane (W42)

`scripts/sim_daemon.py` continuously samples the W37 simulator's persona x
scenario x seed space, executes the real shipped agent, applies the simulator's
universal invariant graders, and optionally applies its LLM judge in live mode.
It is intentionally read-only with respect to `src/`: the daemon imports and
runs the agent but never patches source files or runs git commands.

Run one deterministic offline conversation:

```text
python3 scripts/sim_daemon.py --once --dry-run
```

Run a bounded lane, useful for CI or a smoke test:

```text
python3 scripts/sim_daemon.py --max-conversations 10 --dry-run --interval 0
```

Run continuously (the default has no conversation cap):

```text
python3 scripts/sim_daemon.py --dry-run --interval 30
```

Options include `--personas`, `--scenarios`, `--seed`, `--max-conversations`,
`--interval`, and `--backoff`. `--once` takes precedence over an unbounded
loop and executes exactly one conversation. Every run is appended to
`logs/sim_daemon.jsonl`.

## Reading triage

The rolling triage report is `redteam/findings/TOP.md`. It is regenerated after
each conversation and ranks objective invariant violations above subjective
LLM-judge complaints, then ranks by repeated frequency. Each entry contains its
stable dedupe id, persona/scenario/seed, reason, trigger message, and a complete
reproducing transcript. The corresponding machine-readable records live in
`redteam/findings/<dedupe-key>.json`; repeated occurrences increment `count`
instead of creating duplicate findings.

The daemon auto-files the current top findings through
`redteam/file_failures.py` into `redteam/attack_cases.jsonl`. That queue is
append-only and deduplicated by finding id. The daemon also writes only under
`redteam/findings/` and `logs/` apart from this intentional regression-queue
update; it never edits `src/` or commits.
