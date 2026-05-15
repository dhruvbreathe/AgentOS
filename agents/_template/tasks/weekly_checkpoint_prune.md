---
cron: 0 4 * * 0
kind: systemEvent
---

Weekly checkpoint pruning — silent maintenance, no Discord post.

Run:

```bash
python -m checkpoint_gate prune <my-agent-name> --keep-count 20 --keep-days 7
```

Drops checkpoints older than 7 days, keeping at minimum the last 20 regardless of age. Git-stash mode entries are dropped via `git stash drop`; file-snapshot mode entries have their dirs removed. The index.jsonl is atomically rewritten.

Output goes to `logs/<agent>-weekly_checkpoint_prune.log` (kind: systemEvent — no channel post).

If the prune count comes back unusually high (>50), surface a one-liner in next morning's digest so the operator can see the noise pattern.
