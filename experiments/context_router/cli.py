"""
cli.py — test harness for the context router.

Usage:
    # Route a single prompt (pretty, with per-chunk debug):
    python -m experiments.context_router.cli "Why does PySide6 fail on Linux?"

    # JSON only (the router's actual output contract):
    python -m experiments.context_router.cli --json "What does markup mean?"

    # Run the built-in eval set and show expected-vs-actual:
    python -m experiments.context_router.cli --eval

    # Interactive REPL — type prompts, blank line to quit:
    python -m experiments.context_router.cli --repl

The default embedder is the zero-dependency lexical backend. Force the optional
sentence-transformers backend with:
    set CONTEXT_ROUTER_EMBEDDER=sentence-transformers    (Windows)
    export CONTEXT_ROUTER_EMBEDDER=sentence-transformers (POSIX)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field

from .router import ContextRouter


@dataclass
class EvalCase:
    """One labelled prompt: acceptable levels + the genuinely-relevant chunks.

    ``gold`` is the set of chunk ids that actually help answer the prompt — the
    labels were assigned by reasoning through each answer, not by what the
    router happens to return. An empty ``gold`` means "no stored context should
    be retrieved" (generic/definition/off-domain prompts).
    """

    prompt: str
    levels: set[str]
    gold: set[str] = field(default_factory=set)


# Gold labels — hand-assigned by answering each prompt and asking "what would I
# actually need to answer this well?". See the chat for the reasoning per case.
EVAL_SET: list[EvalCase] = [
    # --- generic / definition / off-domain: retrieve nothing -------------
    EvalCase("What does lifecycle hook mean?", {"none", "tiny"}, set()),
    EvalCase("What does markup mean?", {"none", "tiny"}, set()),
    EvalCase("What is who vs whom?", {"none", "tiny"}, set()),
    EvalCase("Explain that simpler.", {"tiny", "none"}, set()),
    EvalCase("Why is this slow?", {"tiny", "selected", "none"}, set()),
    EvalCase("What does origin mean?", {"none", "tiny", "selected"}, set()),
    EvalCase("What's a good pasta recipe?", {"none", "tiny"}, set()),
    # --- specific technical: retrieve the chunk(s) that answer it ---------
    EvalCase("Why does PySide6 fail on Linux?", {"selected", "full"},
             {"pyside-linux-dll-010", "env-winlinux-050"}),
    EvalCase("How do I switch my Linux clone to the bug fix branch?",
             {"selected", "full"}, {"git-branch-switch-003"}),
    EvalCase("Why am I confused about origin/main again?", {"selected", "full"},
             {"git-origin-main-001"}),
    EvalCase("Why are my Supabase Edge Functions throwing a CORS error?",
             {"selected", "full"}, {"supabase-cors-030"}),
    EvalCase("Where do I set VITE_SUPABASE_URL?", {"selected", "full"},
             {"supabase-env-031"}),
    EvalCase("Can you summarize what went wrong with my AI team workflow?",
             {"selected", "full"}, {"aiteam-workflow-040", "aiteam-taskjar-041"}),
    EvalCase("What is TaskJar?", {"selected", "full"}, {"aiteam-taskjar-041"}),
    EvalCase("How do I run the test suite?", {"selected", "full"}, {"env-venv-051"}),
    EvalCase("Why is my clipboard being redacted?", {"selected", "full"},
             {"clipboard-redact-061"}),
    EvalCase("Tell me about the Wisp overlay architecture", {"selected", "full"},
             {"wisp-arch-020", "wisp-context-fetcher-021", "wisp-hotkey-022"}),
]


def _prf(selected: list[str], gold: set[str]) -> tuple[float, float, float]:
    """Precision, recall, F1 of the selected chunk set against gold."""
    sel = set(selected)
    if not gold:
        # No context expected: precision is "did we stay clean?" (1.0 if empty).
        return (1.0 if not sel else 0.0, 1.0, 1.0 if not sel else 0.0)
    if not sel:
        return (1.0, 0.0, 0.0)
    tp = len(sel & gold)
    precision = tp / len(sel)
    recall = tp / len(gold)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def _eval_metrics(router: ContextRouter) -> dict:
    """Run the eval set and return aggregate metrics (no printing)."""
    level_ok = 0
    retr_cases = 0
    retr_p = retr_r = retr_f = 0.0
    noise_clean = noise_cases = 0
    for case in EVAL_SET:
        res = router.route(case.prompt)
        level_ok += res.context_level in case.levels
        p, r, f = _prf(res.selected_chunk_ids, case.gold)
        if case.gold:
            retr_cases += 1
            retr_p += p; retr_r += r; retr_f += f
        else:
            noise_cases += 1
            noise_clean += 1 if not res.selected_chunk_ids else 0
    n = len(EVAL_SET)
    return {
        "level_acc": level_ok / n,
        "P": retr_p / retr_cases if retr_cases else 0.0,
        "R": retr_r / retr_cases if retr_cases else 0.0,
        "F1": retr_f / retr_cases if retr_cases else 0.0,
        "noise_clean": noise_clean,
        "noise_cases": noise_cases,
        "level_ok": level_ok,
        "n": n,
    }


def _run_eval(router: ContextRouter) -> int:
    print(f"Embedder: {router.embedder.name} | chunks: {len(router.chunks)} "
          f"| rel_cutoff={router.rel_cutoff}\n")
    print(f"{'lvl':>4} {'P':>5} {'R':>5} {'F1':>5}  prompt")
    print("-" * 88)
    for case in EVAL_SET:
        res = router.route(case.prompt)
        good_level = res.context_level in case.levels
        p, r, f = _prf(res.selected_chunk_ids, case.gold)
        print(f"{'ok' if good_level else 'XX':>4} {p:>5.2f} {r:>5.2f} {f:>5.2f}  {case.prompt}")
        missed = case.gold - set(res.selected_chunk_ids)
        extra = set(res.selected_chunk_ids) - case.gold
        if not good_level:
            print(f"      -> level={res.context_level} expected {sorted(case.levels)}")
        if missed:
            print(f"      -> MISSED gold: {sorted(missed)}")
        if extra and case.gold:
            print(f"      -> EXTRA (not gold): {sorted(extra)}")
        if extra and not case.gold:
            print(f"      -> NOISE (should be empty): {sorted(extra)}")

    m = _eval_metrics(router)
    print("-" * 88)
    print(f"Level accuracy:        {m['level_ok']}/{m['n']} ({m['level_acc']:.0%})")
    print(f"Retrieval (context-needed): P={m['P']:.2f}  R={m['R']:.2f}  F1={m['F1']:.2f}")
    print(f"Noise avoidance:       {m['noise_clean']}/{m['noise_cases']} "
          f"no-context prompts retrieved nothing")
    return 0 if m["level_ok"] == m["n"] else 1


def _sweep(router: ContextRouter) -> int:
    """Try several relative-cutoff values and report metrics for each."""
    print(f"Embedder: {router.embedder.name} | tuning rel_cutoff against the eval set\n")
    print(f"{'rel_cutoff':>10} {'level_acc':>10} {'P':>6} {'R':>6} {'F1':>6}")
    print("-" * 46)
    best = (-1.0, None)
    for cut in [0.0, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.70]:
        router.rel_cutoff = cut
        m = _eval_metrics(router)
        flag = ""
        if m["F1"] > best[0]:
            best = (m["F1"], cut)
        print(f"{cut:>10.2f} {m['level_acc']:>9.0%} {m['P']:>6.2f} "
              f"{m['R']:>6.2f} {m['F1']:>6.2f}{flag}")
    print("-" * 46)
    print(f"Best F1={best[0]:.2f} at rel_cutoff={best[1]:.2f}  "
          f"(set REL_CUTOFF in router.py to lock it in)")
    return 0


def _row_why(s) -> str:
    """One-line justification naming the actual signal that matched."""
    if s.matched_identifiers:
        return f"identifier '{s.matched_identifiers[0]}'"
    if s.matched_phrases:
        return f"phrase '{s.matched_phrases[0]}'"
    if s.matched_rare_terms:
        return f"rare term '{s.matched_rare_terms[0]}'"
    if s.vec >= 0.25:
        return "semantic match"
    if s.vec > 0.0:
        return "weak semantic only"
    return "no distinctive match"


def _use_label(s, res) -> str:
    """yes = fed to model | maybe = close call (trimmed) | no = ignored."""
    if s.chunk_id in res.selected_chunk_ids:
        return "yes"
    floor = res.applied_floor
    if floor <= 0:                      # none/tiny/definition: nothing retrieved
        return "no"
    if s.score >= floor:                # cleared floor but cut by the limit
        return "maybe"
    if s.score >= 0.6 * floor:          # borderline below floor
        return "maybe"
    return "no"


def decision_table(router: ContextRouter, prompt: str, top_n: int = 8) -> str:
    """Compact, human-readable 'what context will we use, and why' view."""
    res = router.route(prompt)
    rows = res.scores[:top_n]
    w_src = max(12, *(len(s.chunk_id) for s in rows)) if rows else 12

    out = [
        f'Context decision for: "{prompt}"',
        f"Level: {res.context_level}   confidence {res.confidence:.2f}   "
        f"match={res.match_type or '-'}   floor={res.applied_floor:.2f}",
        "",
        f"{'CONTEXT SOURCE':<{w_src}}  {'USE?':<5} {'SCORE':>6} {'COS':>5}  WHY",
        f"{'-' * w_src}  {'-' * 5} {'-' * 6} {'-' * 5}  {'-' * 24}",
    ]
    for s in rows:
        out.append(
            f"{s.chunk_id:<{w_src}}  {_use_label(s, res):<5} "
            f"{s.score:>6.2f} {s.vec:>5.2f}  {_row_why(s)}"
        )
    out.append("")
    out.append("(yes = fed to model | maybe = close call, trimmed | no = ignored)")
    return "\n".join(out)


def _repl(router: ContextRouter) -> int:
    print(f"Context router REPL (embedder={router.embedder.name}). Blank line to quit.\n")
    while True:
        try:
            prompt = input("prompt> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not prompt:
            break
        print(router.explain(prompt))
        print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="context_router", description=__doc__)
    parser.add_argument("prompt", nargs="*", help="prompt to route")
    parser.add_argument("--json", action="store_true", help="print only the JSON contract")
    parser.add_argument("--eval", action="store_true", help="run the built-in test set")
    parser.add_argument("--sweep", action="store_true",
                        help="tune rel_cutoff against the eval set and report best F1")
    parser.add_argument("--repl", action="store_true", help="interactive prompt loop")
    parser.add_argument("--top", type=int, default=8, help="how many chunks to show")
    parser.add_argument("--signals", action="store_true",
                        help="show the detailed per-signal breakdown instead of the table")
    parser.add_argument("--debug", action="store_true",
                        help="emit router debug logs (scoring, floors, dropped chunks) to stderr")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="[%(name)s %(levelname)s] %(message)s",
        stream=sys.stderr,
    )

    router = ContextRouter()

    if args.sweep:
        return _sweep(router)
    if args.eval:
        return _run_eval(router)
    if args.repl:
        return _repl(router)

    prompt = " ".join(args.prompt).strip()
    if not prompt:
        parser.print_help()
        return 2

    if args.json:
        print(json.dumps(router.route(prompt).to_dict(), indent=2))
    elif args.signals:
        print(router.explain(prompt, top_n=args.top))
    else:
        print(decision_table(router, prompt, top_n=args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
