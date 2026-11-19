# Nebius Research Agent

> ODSC × Nebius Hackathon submission · Challenge: *Build a research agent that brings its sources*

**One-line pitch:** A CLI research agent that answers any question with a sourced, verifiable answer — every claim cites its source — for anyone who needs to trust what an AI says.

- **Demo video (2–3 min):** *pending — recording after the live Nebius test run*
- **Team:** Fngyuan ([@sawi57003-ops](https://github.com/sawi57003-ops)) — solo

A research agent that turns a question into a **sourced, verifiable answer** — built for the
[ODSC × Nebius Hackathon](https://hackathon.odsc.ai/) (Oct 28 – Nov 20, 2026).

Ask a question, get an answer where **every factual claim cites its source URL**, plus the list
of sources and the model that answered. Built with:

- **Nebius Token Factory** — open-model inference (default: `nvidia/Nemotron-3-Nano-30B-A3B`)
- **Tavily** — real-time web search + full-text extraction

## What it does

```
question → plan searches (model) → Tavily search → fetch top sources
         → synthesize with inline citations [1][2] → answer + sources + model
```

- **Evidence first:** claims without a supporting source are not made — if the sources don't
  cover the question, the agent says so instead of inventing an answer.
- **Contradictions surfaced:** when sources disagree, the answer reports both sides.
- **Failure cases handled:** no search results → explicit "insufficient evidence" answer;
  extraction failure → synthesis falls back to search snippets (and says so);
  long answers → summarized with citations kept.
- **Traceable:** every run shows the planned search queries, the sources used, the model id,
  and elapsed time.

## Quick start

```bash
git clone https://github.com/sawi57003-ops/nebius-research-agent.git
cd nebius-research-agent
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in your keys — never commit .env
```

Get the keys (both free):

- **Nebius Token Factory** — register for the hackathon, then claim free credits at
  https://dev.nebius.com/builders and create an API key.
- **Tavily** — free tier at https://tavily.com.

```bash
# Ask one question
.venv/bin/python research_agent.py "What did the ODSC x Nebius hackathon ask builders to make?"

# Interactive mode
.venv/bin/python research_agent.py --interactive

# Machine-readable output
.venv/bin/python research_agent.py --json "Who won the 2026 World Series?"

# Tiny web UI on http://localhost:8000
.venv/bin/python research_agent.py --web
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `NEBIUS_API_KEY` | — | Token Factory API key (required) |
| `NEBIUS_BASE_URL` | `https://api.tokenfactory.nebius.com/v1` | OpenAI-compatible endpoint |
| `NEBIUS_MODEL` | `nvidia/Nemotron-3-Nano-30B-A3B` | Open model used for planning + synthesis |
| `TAVILY_API_KEY` | — | Tavily API key (required) |

## Architecture

Single-file agent (`research_agent.py`, stdlib + `openai` + `tavily-python`):

1. **Plan** — the model turns the question into 2–4 distinct search queries
   (falls back to the raw question if the planning call fails).
2. **Search** — Tavily `advanced` search, up to 5 hits per query, deduplicated by URL
   (max 6 sources).
3. **Fetch** — Tavily extract pulls full text (up to 6,000 chars/source) so synthesis
   works from evidence, not snippets.
4. **Synthesize** — the model answers *only* from the fetched sources, with inline
   citations `[n]`, a contradiction rule, and a hard length cap. Source content is
   data, never instructions.
5. **Report** — Markdown answer + numbered source list + model, query count, timing.

No framework, no database, no server needed for the CLI. The `--web` mode is a
stdlib `http.server` wrapper around the same pipeline.

## Example

*Pending — filled in from the live 5-question test run once the Nebius key is available.*

**Question:** *TBD from the test suite*
**Answer (excerpt):** *TBD — answer with [1], [2] citations*
**Sources:** *TBD*

## Failure / insufficient-evidence case

When Tavily returns no usable sources, the agent answers explicitly that the evidence is
insufficient instead of inventing an answer. When extraction fails for a source, synthesis
falls back to search snippets and says so. A concrete failure-case run is shown in the demo
video (question with no good sources → "insufficient evidence" answer).

## Judging criteria map

| Criterion | Weight | Where to look |
|---|---|---|
| Sourced and verifiable answers | 30% | Example above, demo 0:00–… |
| Works end to end | 25% | Setup, demo |
| Usefulness | 20% | What it does |
| Agent design | 15% | Architecture |
| Use of Token Factory and Tavily | 10% | Configuration, How it works |

## Demo

<!-- demo video: 2–3 min screen recording, agent answering 2 questions + 1 failure case -->
<!-- TODO: add demo video link after recording -->

## Test results

See [TESTS.md](TESTS.md) — 5 real questions (tech, news, obscure topic), run live
against Tavily + Token Factory.

## License

MIT — see [LICENSE](LICENSE).
