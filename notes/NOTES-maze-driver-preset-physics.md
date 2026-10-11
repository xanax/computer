# maze-driver: page-load TDZ crash, and three physics claims that did not survive measurement

Not a cptr-fork bug — no ledger row, no upstream probe. Work in the separate
`~/maze-driver` repo (browser sim: car physics + genetic evolution), commit
`400c2d3`. Written up here because that repo has no notes convention and this is
where the bug notes are kept.

## Symptom

The page was **dead on load**. Canvas blank, no panel, console showed
`ReferenceError: Cannot access 'generation' before initialization`. Every test in
the suite passed anyway — nothing exercised the real page script end to end.

## Why: a `let` declared 1200 lines below its first use

`buildTuningPanel()` ran at ~line 1047 and called `selectPreset('stock')` ->
`resetSim()`, which touches `let generation` and `wallGrid`, both declared at
~line 2279. Classic temporal dead zone: the function was *defined* earlier but
*invoked* before the declarations were evaluated. `var` would have been
`undefined` and survived; `let` throws.

Fix: move the panel build to the end of the script, after `initCars(); update();`,
and guard it so a missing DOM node cannot take the physics down with it.

## The lesson worth keeping: a green suite proved nothing here

40 physics + 8 evolution + 6 wall grid checks all passed against a page that
could not boot, because every one of them ran the physics **out of the script
text** rather than running the script. The gap was the UI path.

`tests/page_smoke.js` (new, 241 lines, 77 checks) closes it by running the real
unmodified page script, including the preset UI. Deliberately **not** jsdom — a
hand-rolled DOM stub, because jsdom is not available here. One trap: the stub's
`className` has to stay in sync with `classList` the way a real DOM does, or the
panel wiring silently diverges and the stub agrees with a broken page. The test
was given a negative control (revert the fix, confirm it fails) rather than being
trusted.

Verified in a real browser, not just the stub: click a preset -> 20 live sliders
update (`mu 1.0 -> 1.6`, `maxSpeed 5.0 -> 6.0`, ...), blurb swaps, **zero console
errors**. That is the check that would have caught the original bug.

## Three physics claims that were plausible, documented, and wrong

The presets carried confident prose about how each car handles. Measuring them
(`tests/probe_balance.js`, new) found three claims that were not true:

1. **`cgToFront` was documented as the rotate/plow knob. It is nearly inert.**
   Sweeping it 0.30 -> 0.70 — a **2.3x change in the front moment arm** — moves a
   steady corner's mean yaw rate by ~5% (0.1242 -> 0.1309) and moves the front/rear
   slide balance by *nothing at all*. Reason: `b = wheelbase - a` moves the other
   way and cancels most of the moment, and the slide telemetry runs off
   `loadF/loadR`, which are `0.5 +/- transfer` — and `transfer` never mentions
   `cgToFront`. So the preset values are close to decoration. The real character
   lives in `tyreStiffness`, the grip biases, `steerFalloff`, `yawInertia`.

   Worse: this means an earlier "sign fix" to the truck's `cgToFront` was
   **unfalsifiable**. The honest status of that change is "harmless, documented",
   not "handling corrected". *Check a knob is live — A/B it at two extremes —
   before changing it on a correctness argument.*

2. **The sports car promised lift-off oversteer** ("how these end up backwards in
   a hedge"). Measured, it has the **smallest** lift-off transient of the whole
   set, which is exactly what its rear-staggered tyres should do. Note rewritten
   to describe the car that is actually there.

3. **`frontSlide - rearSlide` was documented as "understeer vs oversteer, made
   visible"** with no caveats. It is a ratio against each axle's own grip (so not
   comparable between cars) and it is **clamped at 1** — the rally preset pins at
   0.998 and reads flat no matter what you do. Both limits now in the comment.

Also recorded: the textbook understeer gradient `K` is **method-sensitive**. The
same sports car read -0.144 under one sweep and -0.237 under another. Sign and
R^2 are trustworthy; magnitude is not comparable across harnesses. Assert on the
sign, or don't assert.

## Repro

    cd ~/maze-driver
    node tests/page_smoke.js                    # 77 checks, the UI path
    node tests/preset_validate.js --physics     # all 13 presets, stability + grip
    node tests/probe_balance.js                 # steady-state / --power / --liftoff

`tests/probe_balance.js` prints three different balance measures and documents why
each disagrees with the others. `--diag` explains why a candidate run was
rejected (the truck yields **zero** usable steady-state points: it plows so hard
it cannot hold a small-lock corner in the linear band — R=200-557px in a 1440px
arena — which is correct behaviour for a truck, and is itself the measurement).

## Where I stopped

Done and committed (`400c2d3`): the TDZ fix, the page smoke test with negative
control, `probe_balance.js`, and the three doc corrections above. All suites
green: 40 physics + 8 evolution + 6 wall grid + 77 page checks, 13/13 presets
stable, no NaN.

**Unfinished, in priority order:**

1. **`preset_evo.log` still uncollected.** The `--evolve` sweep writes nothing
   until the end (26 workers, `CONC=4`), so it looks hung. It was restarted and is
   running now. Check `/tmp/preset_evo.log` — but note `/tmp` is ephemeral
   between sessions, and this has already been lost once.
2. **The flaky test `flat out into a wall still crashes` is still unresolved.**
   Repro: `node tests/probe_flaky_crash.js`. Not investigated this session.
3. **Evolving the presets was never tried.** Measuring the balance is done;
   *searching* for preset configs that hit a target balance is the obvious next
   move, and the harness (`evo_harness.js`, `evo_sweep.js`) already exists.
4. **`cgToFront` is load-bearing in the docs and inert in the physics.** Either
   make it matter (it would need to enter the static load split, which is
   currently hard-coded 50/50 in `Car.update`) or remove it. Right now it is a
   slider that lies.

**Strongest result from this session, worth not losing:** a 64-seed sweep
(4 families x 16) shows the working tree beats HEAD hard **at the tail** while the
mean is a wash — reaching >= 65 cells is **HEAD 0/64 (0%) vs WORK 22/64 (34%)**,
and >= 70 is **0/64 vs 19/64 (30%)**. Per-seed spread (sd 12-30) dwarfs the mean
effect, so the mean is not the signal here; the upper quantiles are. Anything that
reports only a mean on this benchmark will conclude "no difference" and be wrong.
