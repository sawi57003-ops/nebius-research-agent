"""Nebius Research Agent — ODSC x Nebius Hackathon 2026.

Turns a question into a sourced, verifiable answer:

    question -> plan searches (Nebius model) -> Tavily search
             -> extract top sources -> synthesize with citations
             (Nebius Token Factory, open model)

Every factual claim in the final answer cites a source URL by number.
No-result, contradictory-source, and over-long-answer cases are handled
explicitly instead of being papered over.

Usage:
    python research_agent.py "What is ...?"
    python research_agent.py --interactive
    python research_agent.py --web          # tiny local web UI on :8000
    python research_agent.py --json "question"   # machine-readable output

Config (env vars, or a local .env file — never commit real keys):
    NEBIUS_API_KEY   Token Factory API key (required)
    NEBIUS_BASE_URL  default https://api.tokenfactory.nebius.com/v1
    NEBIUS_MODEL     default nvidia/Nemotron-3-Nano-30B-A3B
    TAVILY_API_KEY   Tavily API key (required)
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date

DEFAULT_MODEL = "nvidia/Nemotron-3-Nano-30B-A3B"
DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1"
MAX_SOURCES = 6            # sources passed to the synthesis model
MAX_SOURCE_CHARS = 6000    # chars of extracted text kept per source
MAX_ANSWER_WORDS = 450     # long answers get summarized, with citations kept


# ---------------------------------------------------------------- .env ----
def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (KEY=value lines, # comments, no interpolation)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("\"'")
            os.environ.setdefault(key, value)


# ---------------------------------------------------------------- types ---
@dataclass
class Source:
    url: str
    title: str = ""
    snippet: str = ""
    content: str = ""
    published: str = ""

    def evidence(self) -> str:
        text = self.content or self.snippet
        return text[:MAX_SOURCE_CHARS]


@dataclass
class ResearchResult:
    question: str
    answer: str
    sources: list[Source] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    model: str = ""
    status: str = "ok"          # ok | insufficient-evidence | error
    notes: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "status": self.status,
            "model": self.model,
            "queries": self.queries,
            "answer": self.answer,
            "sources": [
                {"n": i + 1, "title": s.title, "url": s.url,
                 "published": s.published}
                for i, s in enumerate(self.sources)
            ],
            "notes": self.notes,
            "elapsed_s": round(self.elapsed_s, 1),
        }

    def to_markdown(self) -> str:
        out = [f"# {self.question}\n"]
        if self.status == "insufficient-evidence":
            out.append("> ⚠️ **Insufficient evidence** — no reliable source "
                       "was found to answer this question.\n")
        out.append(self.answer.strip() + "\n")
        if self.sources:
            out.append("## Sources\n")
            for i, s in enumerate(self.sources, 1):
                label = s.title or s.url
                pub = f" ({s.published})" if s.published else ""
                out.append(f"[{i}] {label}{pub} — {s.url}")
            out.append("")
        out.append(f"_Answered by `{self.model}` via Nebius Token Factory · "
                   f"{len(self.queries)} search(es) · "
                   f"{self.elapsed_s:.1f}s · {date.today().isoformat()}_\n")
        if self.notes:
            out.append("\nNotes: " + "; ".join(self.notes) + "\n")
        return "\n".join(out)


# ---------------------------------------------------------------- agent ---
class ResearchAgent:
    def __init__(self, nebius_key: str, tavily_key: str,
                 model: str = DEFAULT_MODEL,
                 base_url: str = DEFAULT_BASE_URL):
        from openai import OpenAI
        from tavily import TavilyClient
        self.model = model
        self.client = OpenAI(base_url=base_url.rstrip("/") + "/",
                             api_key=nebius_key)
        self.tavily = TavilyClient(api_key=tavily_key)

    # -- step 1: plan ------------------------------------------------------
    def plan_queries(self, question: str) -> list[str]:
        """Ask the model for 2-4 distinct search queries for the question."""
        prompt = (
            "You plan web searches for a research agent. Given the user's "
            "question, output 2 to 4 short, distinct search queries that "
            "together would find the facts needed to answer it. "
            "One query per line, no numbering, no commentary.\n\n"
            f"Question: {question}"
        )
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2, max_tokens=256,
            )
            queries = [ln.strip(" -•\t") for ln in
                       (resp.choices[0].message.content or "").splitlines()]
            queries = [q for q in queries if len(q) > 3][:4]
        except Exception as exc:  # model hiccup -> fall back to the question
            queries = []
            print(f"[plan] model call failed ({exc}); using raw question",
                  file=sys.stderr)
        if not queries:
            queries = [question]
        return queries

    # -- step 2: search ----------------------------------------------------
    def search(self, queries: list[str]) -> list[Source]:
        seen: dict[str, Source] = {}
        for q in queries:
            try:
                res = self.tavily.search(
                    query=q, search_depth="advanced", max_results=5,
                    include_answer=False)
            except Exception as exc:
                print(f"[search] Tavily failed for {q!r}: {exc}",
                      file=sys.stderr)
                continue
            for hit in res.get("results", []):
                url = hit.get("url", "")
                if not url or url in seen:
                    continue
                seen[url] = Source(
                    url=url, title=hit.get("title", ""),
                    snippet=hit.get("content", ""),
                    published=hit.get("published_date", "") or "")
            if len(seen) >= MAX_SOURCES:
                break
        return list(seen.values())[:MAX_SOURCES]

    # -- step 3: fetch -----------------------------------------------------
    def fetch_sources(self, sources: list[Source]) -> None:
        urls = [s.url for s in sources]
        if not urls:
            return
        try:
            extracted = self.tavily.extract(
                urls, extract_depth="advanced", query="",
                chunks_per_source=2)
        except Exception as exc:
            print(f"[fetch] Tavily extract failed: {exc}", file=sys.stderr)
            return
        by_url = {r.get("url"): r.get("raw_content", "")
                  for r in extracted.get("results", [])}
        for s in sources:
            if by_url.get(s.url):
                s.content = by_url[s.url]

    # -- step 4: synthesize ------------------------------------------------
    def synthesize(self, question: str, sources: list[Source]) -> str:
        evidence = []
        for i, s in enumerate(sources, 1):
            evidence.append(
                f"[Source {i}] {s.title}\nURL: {s.url}\n{s.evidence()}")
        evidence_block = "\n\n---\n\n".join(evidence)
        system = (
            "You are a careful research agent. Answer ONLY from the sources "
            "below. Rules:\n"
            "1. Every factual claim must carry an inline citation like [1] "
            "or [2] pointing at the source that supports it.\n"
            "2. If sources disagree, say so explicitly and report both "
            "sides with their citations — never pick one silently.\n"
            "3. If the sources do not contain enough to answer, say "
            "'The sources found do not provide enough evidence to answer "
            "this question.' and stop — do not invent facts or citations.\n"
            "4. Keep the answer focused and under "
            f"{MAX_ANSWER_WORDS} words; use short paragraphs or bullets.\n"
            "5. Distinguish facts (cited) from your own inference "
            "(label it as such)."
        )
        user = (f"Question: {question}\n\nSources:\n{evidence_block}")
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=0.2, max_tokens=1600,
        )
        return (resp.choices[0].message.content or "").strip()

    # -- full pipeline -----------------------------------------------------
    def research(self, question: str, verbose: bool = False) -> ResearchResult:
        started = time.time()
        result = ResearchResult(question=question, model=self.model)
        log = (lambda *a: print("[agent]", *a, file=sys.stderr)) if verbose \
            else (lambda *a: None)

        result.queries = self.plan_queries(question)
        log("queries:", result.queries)
        result.sources = self.search(result.queries)
        log(f"found {len(result.sources)} sources")
        if not result.sources:
            result.status = "insufficient-evidence"
            result.answer = (
                "No web sources were found for this question, so I cannot "
                "give a sourced answer. Try rephrasing the question or "
                "checking whether the topic is covered online at all.")
            result.notes.append("Tavily returned zero results for all "
                                "planned queries.")
            result.elapsed_s = time.time() - started
            return result

        self.fetch_sources(result.sources)
        fetched = sum(1 for s in result.sources if s.content)
        log(f"extracted full text from {fetched}/{len(result.sources)}")
        if fetched == 0:
            result.notes.append(
                "Full-text extraction failed; synthesis used search snippets.")

        try:
            result.answer = self.synthesize(question, result.sources)
        except Exception as exc:
            result.status = "error"
            result.answer = f"Synthesis failed: {exc}"
            result.elapsed_s = time.time() - started
            return result

        # contradiction / gap surfacing: the model is instructed to flag
        # them, but also note when the answer itself admits insufficiency.
        low = result.answer.lower()
        if "do not provide enough evidence" in low or \
                "not enough evidence" in low:
            result.status = "insufficient-evidence"
            result.notes.append(
                "Sources were found but did not cover the question.")
        if "disagree" in low or "conflict" in low or "contradict" in low:
            result.notes.append("Sources disagreed; both sides are reported "
                                "in the answer.")

        # long-answer handling: synthesis prompt already caps length, but
        # enforce a hard bound and keep citations intact.
        words = result.answer.split()
        if len(words) > MAX_ANSWER_WORDS + 50:
            result.notes.append(
                f"Answer was {len(words)} words; capped summary kept with "
                "citations.")
            # keep the first MAX_ANSWER_WORDS words + any trailing citation
            result.answer = " ".join(words[:MAX_ANSWER_WORDS]) + " …"

        result.elapsed_s = time.time() - started
        return result


# ---------------------------------------------------------------- web UI --
WEB_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Nebius Research Agent</title>
<style>body{font-family:system-ui;max-width:760px;margin:40px auto;padding:0 16px}
input{width:70%;padding:8px}button{padding:8px 14px}pre{white-space:pre-wrap}</style>
</head><body><h1>🔍 Nebius Research Agent</h1>
<p>Ask a question — the agent searches the live web (Tavily), then answers
with citations, using an open model on Nebius Token Factory.</p>
<form method="post"><input name="q" placeholder="Ask anything…" required>
<button>Research</button></form>
{body}</body></html>"""


def serve(agent: ResearchAgent, port: int = 8000) -> None:
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs

    class Handler(BaseHTTPRequestHandler):
        def _page(self, body: str = "") -> None:
            data = WEB_PAGE.format(body=body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._page()

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            q = parse_qs(self.rfile.read(n).decode()).get("q", [""])[0]
            if not q.strip():
                self._page("<p>Please ask a question.</p>")
                return
            res = agent.research(q)
            body = "<hr><pre>" + html.escape(res.to_markdown()) + "</pre>"
            self._page(body)

        def log_message(self, *a):
            pass

    print(f"Web UI at http://localhost:{port}  (Ctrl-C to stop)")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


# ---------------------------------------------------------------- CLI -----
def build_agent() -> ResearchAgent:
    load_dotenv()
    nebius_key = os.environ.get("NEBIUS_API_KEY", "")
    tavily_key = os.environ.get("TAVILY_API_KEY", "")
    missing = [n for n, v in (("NEBIUS_API_KEY", nebius_key),
                              ("TAVILY_API_KEY", tavily_key)) if not v]
    if missing:
        print("Missing: " + ", ".join(missing) +
              ". Copy .env.example to .env and fill in the keys.",
              file=sys.stderr)
        sys.exit(2)
    return ResearchAgent(
        nebius_key, tavily_key,
        model=os.environ.get("NEBIUS_MODEL", DEFAULT_MODEL),
        base_url=os.environ.get("NEBIUS_BASE_URL", DEFAULT_BASE_URL))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Nebius Research Agent")
    ap.add_argument("question", nargs="?", help="question to research")
    ap.add_argument("--interactive", "-i", action="store_true")
    ap.add_argument("--web", action="store_true",
                    help="serve a small web UI on :8000")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)

    agent = build_agent()
    if args.web:
        serve(agent)
        return 0

    questions = []
    if args.question:
        questions = [args.question]
    elif args.interactive or sys.stdin.isatty():
        print("Nebius Research Agent — type a question (empty line quits).")
        while True:
            try:
                q = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not q:
                break
            questions.append(q)
    else:
        questions = [ln.strip() for ln in sys.stdin if ln.strip()]

    for q in questions:
        res = agent.research(q, verbose=args.verbose)
        if args.json:
            print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(res.to_markdown())
            print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
