"""CLI entry point: run the agent on one protein.

    python -m agent.run --accession P04637 [--eval-mode] [--max-steps 10]
    python -m agent.run --sequence MEEP... [--accession-label myprotein]

If --sequence is omitted, the sequence is fetched once via tools/uniprot.py
(outside the agent loop, as plain setup -- not counted as agent evidence)
so the agent always starts from a sequence, matching how Phase 2 is
specified (sequence, optionally a UniProt accession).
"""
from __future__ import annotations

import argparse
import json

from agent.loop import AgentRun
from tools import uniprot


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--accession", help="UniProt accession to fetch the sequence for")
    parser.add_argument("--sequence", help="amino-acid sequence (overrides --accession lookup)")
    parser.add_argument("--eval-mode", action="store_true")
    parser.add_argument("--max-steps", type=int, default=None)
    args = parser.parse_args()

    sequence = args.sequence
    accession = args.accession
    if sequence is None:
        if accession is None:
            parser.error("must give --accession or --sequence")
        print(f"Fetching sequence for {accession} from UniProt (setup, not an agent step) ...")
        ev = uniprot.run(accession)
        if ev.error:
            parser.error(f"could not fetch {accession}: {ev.error}")
        sequence = ev.raw_scores["sequence"]
        print(f"  {len(sequence)} aa")

    run = AgentRun(query_sequence=sequence, query_accession=accession, eval_mode=args.eval_mode, max_steps=args.max_steps)
    print(f"Running agent on {accession or '(sequence only)'} (eval_mode={args.eval_mode}) ...")
    report = run.run()

    print(f"\nStop reason: {report['stop_reason']} (steps_used={report['steps_used']})")
    print(f"Token usage: input={report['token_usage']['input']} output={report['token_usage']['output']} total={report['token_usage']['total']}")
    print(f"Trace/report saved to: {report['run_dir']}")
    print("\n--- report.md ---\n")
    print((__import__("pathlib").Path(report["run_dir"]) / "report.md").read_text())


if __name__ == "__main__":
    main()
