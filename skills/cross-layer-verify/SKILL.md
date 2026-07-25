---
name: cross-layer-verify
description: >-
  Verify that an action actually worked by fusing TWO independent senses onto the machine — the GUI (screen-mcp's pixel-change signal) and the OS (os-control-mcp's os_verify: systemd unit state + journald). Use whenever an action is supposed to have a system effect you must confirm across a long or high-stakes task: clicking a Restart/Apply/Start button in a GUI, toggling a service from a settings panel, or any step where "it looked like it worked" is not good enough. The loop catches the failure a single-layer agent cannot see — a GUI that changed while the service never did (or a service that changed while the UI froze). Requires os-control-mcp; the pixel half additionally needs screen-mcp (without it, verification degrades cleanly to OS-only).
---

# Cross-layer action verification

One sense lies. A screenshot can't tell a spinner from a frozen app; systemd can't
see a dialog. Fuse both and you can tell a real success from a no-op — the thing that
makes long-horizon work survivable. This skill encodes the loop:

**`begin` → act → read both senses → `end` → reconcile.**

## The loop

1. **`os_verify` `action=begin`** — snapshot the baseline. Pass the `units` the action
   should affect (e.g. `["nginx.service"]`), optionally `expect` (unit → wanted state,
   e.g. `{"nginx.service": "active"}`), and `scope` (`system`|`user`). Keep the returned
   `token`.
2. **Perform the action** with whatever tool does it — a `screen_click` on the GUI
   button, an `os_service` restart, a manual step.
3. **Read the pixel signal** (only if a GUI was involved). Two options:
   - **`screen_verify`** — preferred. It *polls* until the screen settles or a timeout,
     grades the GUI itself (`CONFIRMED`/`PARTIAL`/`NO_OP`/`DIVERGED`), and returns a
     `pixel` block ready to hand straight to step 4. Takes `expect_text` / `expect_gone`
     / `expect_change`, so you can assert the button's effect rather than just "something
     moved". Use this when the GUI effect may take a moment to appear.
   - **`screen_sense`** — passive. Returns `{"pixel": {changed, opened, modal, no_op,
     activity}}` for whatever the *last* action already left behind, with no waiting. Use
     it when the action is known to be instantaneous, or when you have already screenshotted.
4. **`os_verify` `action=end`** — pass the `token` and, if you have one, `pixel=<the
   `pixel` object from screen_verify or screen_sense>`. It re-reads systemd + journald
   and returns the verdict.

Both emit the same `{"changed": bool, ...}` contract; `os_verify` reads `changed` and
ignores the rest, so either is safe to pass.

### Worked example (verified live)

```
os_verify   action=begin  units=["dbus.service"]        -> token
screen_click / os_service / manual step
screen_verify expect_change=true region=[...]           -> {"verdict":"CONFIRMED","pixel":{"changed":true}}
os_verify   action=end    token=<token> pixel={"changed":true}
```

A GUI-only action (a hover) against an untouched unit returns
`status: DIVERGED`, `cross_layer: "pixel-changed-os-static"`, `reconciled: false`.
An inert action on both layers returns `status: NO_OP`, `reconciled: true`,
`cross_layer: null`. Both quadrants were exercised end-to-end against a live desktop.

## Reading the verdict

| status | meaning | do |
|---|---|---|
| **CONFIRMED** | expectation met (or a plausible OS effect happened), no journal errors, layers agree | proceed |
| **PARTIAL** | some expected units met, others not | inspect the per-unit block; finish the rest |
| **NO_OP** | nothing changed at the OS layer and (if given) the screen was static | the action did nothing — re-ground and retry, don't build on it |
| **DIVERGED** | a unit failed, journald logged errors, OR the layers disagree | STOP. Read `cross_layer` + `journal.sample`. Do not assume success. |

`cross_layer: "pixel-changed-os-static"` is the signal to care about most: the GUI moved
but the service never did — the button "worked" visually and did nothing real. That is
exactly the silent failure that compounds over a long task.

## When to reach for it

- After clicking a control in a GUI that is supposed to drive a service (restart, apply,
  enable) — the click succeeding on screen is not proof the service restarted.
- On any high-stakes or irreversible-adjacent step where a confident wrong belief is worse
  than a slow check.
- Over long tasks (many steps), to stop drift: verify the load-bearing actions so a no-op
  never becomes a false premise for the next twenty steps.

## Notes

- `os_verify` is **read-only** — it never mutates; it only observes and reconciles. Safe
  to call freely.
- The `token` is stateless (it carries the baseline), so `begin` and `end` need not be
  adjacent — do arbitrary work in between.
- No screen-mcp installed? Skip step 3 and omit `pixel`; you still get OS-layer
  verification (unit state + journald), just without the GUI cross-check.
- `expect` is optional. With it you assert the intended end-state and get CONFIRMED/
  PARTIAL grading; without it, os_verify reports whether *anything* changed and flags
  failures/errors.
