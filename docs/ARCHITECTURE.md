# JaegerAgent — architecture

What this module is, how a turn actually runs, and the handful of design
decisions that make it behave differently from a generic tool-calling
loop. Every number here was measured against the source, not estimated;
where a design was tried and *failed*, that is recorded too, because a
refuted idea is the most expensive kind to re-discover.

Companion docs live beside this one:

| Area | Doc |
| --- | --- |
| One turn, end to end | [`pipelines/agent_turn_pipeline.md`](pipelines/agent_turn_pipeline.md) |
| Context window management | [`core/context_guard.md`](core/context_guard.md) |
| The public runtime contract | [`core/agent_contract.md`](core/agent_contract.md) |
| Dynamic tool surface (and why it is off) | [`core/toolset_scoping_ab.md`](core/toolset_scoping_ab.md) |
| Skill format and lifecycle | [`skills/skill_schema_v3.md`](skills/skill_schema_v3.md) |

> **Note on moved docs.** The files under `docs/core/`, `docs/pipelines/`
> and `docs/skills/` were written while this code still lived inside
> JaegerAI. They cite paths like `agent/loop/jaeger_agent.py` and
> `jaeger_os/main.py`. Read those as `jaeger_agent/loop/jaeger_agent.py`
> and "the host application" respectively. The reasoning is current; the
> paths are pre-extraction.

---

## 0. What belongs here at all

**Applications are where things are designed, developed and tested. Only
what is locked in gets a channel to the agent.**

That rule decides both code and documentation. An idea is prototyped in
JaegerAI or Mochi against a real instance, a real microphone, a real
user. It earns its way into this module only once its shape has stopped
moving — and what arrives is the *mechanism*, not the exploration that
produced it.

| Stays in the application | Merges into the module |
| --- | --- |
| Design explorations, mode A/B/C comparisons | The mode that shipped |
| Build plans, backlogs, roadmaps | The built thing |
| Product policy — which character, which voice | The seam that policy plugs into |
| Personas, traits, safety policy — demo ideas still being flushed out | Nothing yet; see §6 |
| Benchmark corpora under active authoring | Benchmark *results* that settled a decision |
| Anything still changing shape | Anything other apps must be able to rely on |

The practical test: **could a different application depend on this?** A
persona *mechanism* — yes, Mochi uses it. A persona *design document
comparing three unbuilt modes* — no, that is JaegerAI's R&D and belongs
with JaegerAI.

This is why the A/B benchmark results in `docs/core/` *are* here while
the design docs that proposed them are not. A refuted experiment is a
locked decision — the most valuable kind, because it stops the next
person re-running it. An unbuilt proposal is still R&D.

---

## 1. What it is

JaegerAgent is a JaegerOS `slot: mind` module — the only module kind that
*decides* rather than executes. It ships a complete agent, not a loop you
then have to furnish:

| | count | where |
| --- | ---: | --- |
| Tools registered at import | **96** | `jaeger_agent/tools/` |
| Named toolsets | **28** | `jaeger_agent/schemas/tool_bundles.py` |
| Playbook skills | **107** | `jaeger_agent/skills/` |
| Provider adapters | **6** | `jaeger_agent/adapters/` |
| Prompt dialects | **6** | `jaeger_agent/dialects/` |
| Tests | **375** | `tests/` |

Adapters: `anthropic`, `openai`, `local_llama`, `mlx`, `hermes_xml`, plus
the `base` protocol. Dialects: `chatml`, `gemma`, `harmony`, `llama3`,
`mistral`, and `detect` which picks among them.

A bare `pip install jaeger-agent` brings llama.cpp weights in-process — no
server, no API key, nothing leaving the machine. That posture is
deliberate and is why `llama-cpp-python` is a base dependency rather than
an extra.

---

## 2. The turn pipeline

One user message becomes one reply through `format → call → parse →
dispatch`, looping up to `max_iterations` (24).

```
ChatMessage (/act/chat) or Transcript (/sense/stt/transcript)
      │  AgentBridge._on_chat / _on_transcript → inbox queue
      │  (turn already live? steer() it instead of queueing)
      ▼
AgentBridge._loop  (worker thread)          jaeger_agent/bridge.py
  publish AgentState "thinking"  → /sense/agent_state
      │
  drive_one_turn(agent, text)               loop/runtime_bridge.py
      ▼
  JaegerAgent.run_turn(user_message)        loop/jaeger_agent.py
    append {role:"user"}, then loop:
      │
  ┌─▶ 1  pre-flight ContextGuard trim  (§5)
  │   2  format_messages → adapter.call → parse_response
  │      (classified retry + fallback chain; heartbeat ticks)
  │   3  append assistant Message (may carry text AND tool_calls)
  │      no tool_calls → return text ───── final answer ──▶
  │   4  skip-final fast path: iteration 1, one deterministic tool,
  │      non-multistep → dispatch + finalize, no second model call
  │   5  dispatch each tool_call → append {role:"tool"}
  │      (all-read / path-scoped batches run via _dispatch_parallel)
  │      loop-backstop: identical-call · semantic-failure · runaway
  └────── loop ◀───────────────────────────────────────────┘
      │
  ChatReply → /sense/chat ; AgentState "idle"
```

`run_turn` repairs the transcript on **every** exit path. A pre-flight
overflow rolls the user message back and re-raises; a mid-turn overflow,
interrupt, or error closes dangling tool calls so the next turn still
formats cleanly. A half-open tool call is how a transcript becomes
permanently unusable, and no provider forgives it.

**Bus contract.** Consumes `/act/chat`, `/sense/stt/transcript`,
`/act/response`. Produces `/sense/chat`, `/sense/agent_state`,
`/sense/tool`, `/sense/activity`, `/sense/request`. Note what is *absent*:
the mind never publishes `/act/speech/say`. It has no opinion about being
heard — see §7.

---

## 3. Tools — and why the surface is *not* dynamic by default

96 tools register themselves at import time onto the process-wide
JaegerOS registry. The agent re-reads that registry every turn, which is
why any module, skill, or MCP server that registers a tool becomes
reachable without this package knowing it exists. That is also why
`module.yaml` declares `tools: []` — the mind calls tools, it does not
contribute them.

### The dynamic-tool experiment, and its result

The obvious optimisation: don't show a small model 96 schemas. Keep a
~17-tool CORE visible and let the model pull the rest on demand via a
`load_toolset` tool. Each skill is its own self-describing toolset.

It was built, and benchmarked — 33 prompts, Gemma 4 26B-A4B, in-process:

| | All tools visible | Toolset scoping |
| --- | ---: | ---: |
| Routing accuracy | **28/33 (85%)** | 22/33 (67%) |
| Avg per prompt | **2.83s** | 3.16s |

**Scoping cost 18 points of accuracy and was slower.** The `load_toolset`
meta-step is the culprit: the model must first *realise* it needs a
toolset, *call* the loader with the right name, then call the real tool.
Gemma 4 does not reliably take that step — it answers in plain text or
grabs a wrong core tool instead.

The deeper finding is the one worth keeping: **"too many tools" was never
the routing bottleneck.** A 26B model handles a 96-tool surface at 85%.
Adding the meta-step *created* failures that did not previously exist.

So the machinery ships **off**: `JAEGER_TOOLSET_SCOPING`, default `0`. The
code is deliberately kept, because it is the foundation for the one
variant that might still win — *auto-load on intent*, where a cheap
keyword pre-pass loads the likely toolset **before** the turn so the model
never takes the meta-step at all.

This is the house discipline, and it recurs: build it, benchmark it, and
if it is not a win, **flag it off rather than ship it**. The same
happened to the native chat handler ([`core/native_handler_ab.md`](core/native_handler_ab.md)).

### Permission tiers

Every tool declares a `side_effect`. Write-tier tools are gated, and the
gates fail *closed* — a tool whose owning module is absent reports
unavailable rather than falling through to an optimistic default. That
fail-open regression happened once, in the plugin era, and the current
`_TOOL_TO_MODULE` / `_TOOL_TO_SLOT` split in `availability.py` exists to
prevent its return.

---

## 4. Skills — the agentic multiplier

A skill is a `SKILL.md` playbook the agent reads **on demand**. 107 ship
in the box. They are not prompts and not tools; they are recipes that
tell the model *how to sequence the tools it already has*.

The mechanism is one tool, `use_skill(name)`, whose description is the
whole design:

> *"Load a specialized JROS playbook recipe and FOLLOW it. Call this
> BEFORE raw tools for any specialized task — first scan your skills for
> a match; only if NONE fits do you reach for raw tools."*

`name` is a generated enum of the available skills, so the model cannot
hallucinate a skill that does not exist — the schema constrains it.

**Why this raises agentic quality.** A generic loop, asked to "analyse
this codebase," improvises an order of operations every time and
improvises differently every time. A skill fixes the sequence that was
found to work, so the model spends its reasoning on the *problem* rather
than re-deriving procedure. It is progressive disclosure applied to
method rather than data: the catalogue is cheap (names only), the recipe
is loaded only when chosen.

Note the asymmetry with §3. Loading a *skill* on demand works; loading a
*toolset* on demand did not. The difference is that choosing a skill is
the task the model is already doing — deciding what kind of problem this
is — whereas choosing a toolset is bookkeeping the model has no reason to
care about.

Skills are also the self-improvement seam: `reload_skills()` re-scans
after the agent authors a new one. See
[`pipelines/skill_discovery_pipeline.md`](pipelines/skill_discovery_pipeline.md)
and [`pipelines/skill_self_improvement_pipeline.md`](pipelines/skill_self_improvement_pipeline.md).

---

## 5. The context guard — three stages, then refuse

`util/context_guard.py`. Runs pre-flight on every turn, and degrades in
defined stages rather than failing at a cliff.

| Stage | Action |
| --- | --- |
| **1 · Prune** | Stub oversized tool-result bodies once their turn leaves the protected tail. The artifact path survives in the stub, so the model can read any of it back. |
| **2 · Digest** | Fold dropped turns into one `[EARLIER CONTEXT — REFERENCE ONLY]` message — what was asked, what tools ran, what errored. A previous digest is *merged*, never stacked. |
| **3 · Prune in-flight** | The current turn overflowed *by itself* (39 file reads, a big grep). Stub its **oldest** results, protecting the last two and the user message. |
| **4 · Refuse** | `ContextOverflow`, typed. |

Stage 3 is the newest and the one that changed a documented invariant.
Before it, "everything after the latest user message is verbatim" was
absolute, and a turn that read too much was simply lost with its work.
Now that guarantee holds *until stage 3*. The cost is real — the model
continues on stubs — so it is reported separately as
`TrimResult.inflight_pruned_count` rather than folded into the ordinary
prune count, and the turn surfaces it on `on_thinking`.

Two budget subtleties worth knowing:

- **`ctx_window` means the *serving* model's window**, not a global
  constant. A cloud model answering and the local worker lane have
  different windows; the guard is rescoped per active model per turn.
- **`completion_reserve`** is held back from the prompt budget because the
  server counts prompt + completion against **one** window. A reserve
  smaller than the answer you asked for overflows at generation time even
  though the prompt fit. It is clamped to half the window so a
  misconfigured `max_tokens` cannot zero the prompt budget.

---

## 6. The dual-lane persona pipeline — "the id and the ego"

This is the most unusual thing in the module, and the one most likely to
be mistaken for ordinary persona prompting. It is not.

**The problem.** Give an agent a character and it starts asserting things
in character. Ask Lilith the time and she will *tell* you the time —
warmly, confidently, and wrongly. Every hallucination of this shape is a
persona answering a reality question it should have delegated.

**The structure.** Persona Mode C (`prompts/persona_lane.py`) runs two
lanes with a Freudian division that is load-bearing, not decorative:

```
        the id                    the ego                the superego
   ┌──────────────────┐     ┌──────────────────┐    ┌──────────────────┐
   │  persona lane    │     │  clean agent     │    │ permission tiers │
   │  voice, desire,  │────▶│  persona OFF,    │    │ e-stop,          │
   │  character       │     │  all 96 tools,   │    │ fail-closed      │
   │                  │◀────│  hardened prompt │    │ gates            │
   └──────────────────┘     └──────────────────┘    └──────────────────┘
        wants to              reality-tests            says no to both
        answer now            via tool calls
                    ▲
          ONE tool: perform_task
```

**The invariant that makes it safe: the id never touches reality
directly.** The persona reaches the agent through exactly one tool,
`perform_task`. A tool call *is* reality-testing.

**Why it does not degrade the clean agent.** `perform_task` invokes the
full agentic loop with **persona off, every tool available, and the
hardened system prompt**. The character is not in the room while the work
happens. So routing accuracy, tool selection, and multi-step planning run
on exactly the same surface the benchmarks measure — the persona
influences *which questions get delegated* and *how the answer is
voiced*, never how the work is done.

Three details that make it hold:

1. **Delegation is a tool call, not a prose classifier.** The decision to
   delegate uses the same text-dialect tool-call shape the routing bench
   already measures, so it inherits that reliability instead of inventing
   a new, unmeasured classifier.
2. **Recursion is structurally impossible, not policed.** The
   `perform_task` closure is built by the *caller* and invokes
   `drive_one_turn` directly — never back through the persona module.
   There is no code path for the ego to re-enter the id.
3. **Compose never means replace.** When the id styles the tool's raw
   result, it is checked by the same content-survival gate the restyle
   pass uses (`persona_filter.py`) — *imported, not duplicated*. If
   styling would gut the content, the raw answer ships instead.

### Mechanism here, content and policy in the application

Persona is the clearest case of §0 in practice, and the split runs
straight through the source:

| | app imports | verdict |
| --- | ---: | --- |
| `safety.py` | **0** | self-contained |
| `prompts/persona_lane.py` — the lane | 2 | mechanism, nearly clean |
| `prompts/persona_filter.py` — survival gate | 0 | mechanism |
| `tools/persona.py` — traits, characters, people | **6** | content, fully coupled |

That 6-of-6 is not a coupling bug to fix in place. Characters, trait
layers, and safety policy are still demo-stage ideas being worked out
against a real product — they belong in JaegerAI, and `tools/persona.py`
reads as a set of tools that have not moved back yet. The *lane* is what
locked in: a structure any application can pour its own character into.
Mochi does exactly that, with a completely different character model.

So this section documents a **mechanism with a hole in the middle**. The
hole is deliberate. What fills it is the host's business.

**Fail-open contract.** `run_persona_turn` returns `None` **only** for a
failure occurring *before* `perform_task` runs — the caller's signal to
fall through to plain Mode A untouched. Once `perform_task` has been
called it always returns a string, never `None`, because the alternative
is running the turn twice.

---

## 7. The module boundary

`docs/EXTRACTION.md` states the rule plainly:

> **JaegerAgent depends on JaegerOS. JaegerAI depends on JaegerAgent.
> JaegerAgent must never import JaegerAI.**

Where the boundary holds:

- **Zero** imports of sibling modules. No `jaeger_kokoro_tts`, no
  `jaeger_whisper_stt`, anywhere in package code. TTS and STT are reached
  as *slots and topics*, never as packages.
- The instance layout is **injected**, not fetched: `workspace.bind(layout)`
  wires all tool I/O to a host-chosen directory at startup, and the
  `InstanceLayout` type import is `TYPE_CHECKING` only.
- The whole 375-test suite passes with **no host application installed**.

Where it does not, as of `1.0.1`:

- **54 imports of `jaeger_ai`** across ~24 files in package code, spanning
  18 distinct app modules. Heaviest: instance schemas (9), `jaeger_ai.main`
  (8), instance layout (7), usage stats (4), plugins (4). Not all want the
  same remedy: the instance and pipeline ones want a seam, while
  `tools/persona.py`'s six want the *tools themselves* to move back to the
  application (§6).
- **42 undeclared third-party imports** in package code — `numpy`, `yaml`,
  `requests`, `certifi`, `torch`, `pydantic` — against a `pyproject.toml`
  declaring only `jaeger-os`, `llama-cpp-python`, `jinja2`.
- The benchmark corpus lives in the application, so `tools/bench.py` — an
  agent-callable "run the system benchmark" tool — hard-fails without it.

The suite passing without the app while 54 such imports exist means those
paths are simply untested. The package *imports* standalone; it does not
yet *run* standalone, and `pyproject.toml` currently claims otherwise.

**The direction of the fix is the interesting part.** Embedded in Mochi,
it is Mochi's character that applies, not JaegerAI's — so the agent cannot
name either. The pattern that works is inversion: the **app pushes into
the agent**, the agent never pulls. Mochi already does this
(`modules/jaeger_agent.py :: set_persona`), and JaegerAI adopted it in
`0.11.0`. Each remaining coupling wants the same treatment — a seam here,
filled by whichever application is hosting.

---

## 8. Reading order

New to the module: §2, then
[`pipelines/agent_turn_pipeline.md`](pipelines/agent_turn_pipeline.md).

Changing routing or tools: [`core/toolset_scoping_ab.md`](core/toolset_scoping_ab.md)
first — it will save you from re-running a refuted experiment.

Changing the context guard: [`core/context_guard.md`](core/context_guard.md),
and note the stage-3 invariant in §5.

Touching persona: §6, then `prompts/persona_lane.py` — its module
docstring is the authoritative statement of the contract. The id/ego
split is not a metaphor to tidy up; it is the safety property. The
design exploration that produced Mode C (modes A/B/C compared, the build
plan) stayed in JaegerAI under `dev/docs/roadmap/`, per §0 — read it for
*why these three options*, read here for *what shipped*.
