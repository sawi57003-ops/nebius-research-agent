"""Run the 5-question acceptance test suite and append results to TESTS.md.

Questions cover tech, news, and one obscure topic, plus the failure cases
(no-result / contradictory sources are exercised by questions 4-5).
"""
import subprocess
import sys
import time

QUESTIONS = [
    ("tech", "What is NVIDIA Nemotron 3 Nano and what is it designed for?"),
    ("tech", "How does Nebius Token Factory pricing work for open models?"),
    ("news", "Who won the ODSC x Nebius hackathon, and what were the prizes?"),
    ("obscure", "What is the 'Imphnen' Indonesian programmer community?"),
    ("obscure/no-result",
     "What did the village council of Timbuktu decide about moon cheese exports in 2025?"),
]


def main() -> int:
    lines = ["# Test results\n",
             "Live runs against Tavily + Nebius Token Factory "
             f"({time.strftime('%Y-%m-%d %H:%M %Z')}).\n",
             "| # | Type | Question | Result |\n|---|---|---|---|\n"]
    for i, (kind, q) in enumerate(QUESTIONS, 1):
        print(f"\n=== [{i}/{len(QUESTIONS)}] {kind}: {q}", flush=True)
        proc = subprocess.run(
            [sys.executable, "research_agent.py", "--json", q],
            capture_output=True, text=True, timeout=300)
        ok = proc.returncode == 0
        status = "?"
        answer = ""
        if ok:
            import json
            try:
                data = json.loads(proc.stdout)
                status = data.get("status", "?")
                n_src = len(data.get("sources", []))
                answer = data.get("answer", "")[:200].replace("\n", " ")
                print(f"status={status} sources={n_src} "
                      f"elapsed={data.get('elapsed_s')}s")
                print("answer:", answer)
            except Exception as exc:
                status = f"bad-json: {exc}"
        else:
            print("FAILED rc=", proc.returncode, proc.stderr[-500:])
        lines.append(f"| {i} | {kind} | {q} | "
                     f"{'PASS' if ok and status == 'ok' else status} |\n")
    with open("TESTS.md", "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    print("\nWrote TESTS.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
