# C.H.A.R.L.I.E. — MASTER STABILIZATION PLAN

**Version:** 3.1
**Baseline commit:** `c9f704edbd4c46ec05e8ce64b5c62bdd970b0be9`
**Branch:** `integration/final-runtime-acceptance`
**Executor:** Codex — GPT-5.6 Luna, maximum reasoning effort
**Objective:** Deliver a secure, reliable, responsive, voice-first agentic assistant that operates end-to-end on the owner's actual Windows host.

**Plan status:** Approved scope; implementation and final acceptance not yet performed.

---

## 1. Scope and Non-Negotiable Rules

### 1.1 Included

- Core agent orchestration and capability routing
- Tool execution, policy, approvals, resource leases and verification
- Desktop control through Cua
- Browser automation and web research
- Voice input, ASR, VAD, TTS and interruption
- Memory and World Model
- Background tasks, reminders, timers and scheduling
- Telegram, web and voice channel routing
- Frontend and runtime event integration
- Health diagnostics and runtime reporting
- **Self-extension: generated Python tools and imported SKILL scripts**
- Latency, resource management and cleanup
- Integrated real-host acceptance

### 1.2 Excluded

- MCP client and MCP servers
- Traditional plugin system and plugin manager
- Reintroducing removed MCP/plugin infrastructure
- Working on legacy MCP/plugin tests
- Unrequested frontend redesign
- Unrelated architectural rewrites

Existing obsolete MCP/plugin test references may remain. Document them as deferred technical debt. Do not edit or execute them.

If a stale MCP/plugin import prevents Charlie's actual production runtime from launching, remove that production dependency without rebuilding the excluded subsystem.

### 1.3 Absolute execution constraints

1. **Real Windows host only for acceptance.** No Codex sandbox, simulated desktop, fake provider or mocked integration can establish completion.
2. **Do not create, edit, delete or execute test files.** No pytest, mock runners, fixtures, generated test scripts or artificial test datasets.
3. Use real microphone input, existing applications, actual Windows processes, live search providers and live service responses.
4. Use real owner-authorized workflows. Do not fabricate data or perform unsolicited destructive actions to demonstrate success.
5. One bounded defect per Codex execution run.
6. Inspect the current code and reproduce or substantiate the defect before changing production code.
7. Make the minimum sufficient correction.
8. Preserve existing working behavior, security boundaries, provider settings and default LLM parameters.
9. Do not modify unrelated files or erase existing worktree changes.
10. Do not equate successful dispatch, compilation, import or a returned success string with a verified outcome.
11. Record actual evidence. Never invent command outputs, PIDs, timestamps, screenshots, citations or verification results.
12. If real-host access is unavailable, report **BLOCKED** rather than accepting sandbox evidence.
13. If architectural intent or authorization is unclear, **ask the owner before proceeding**.
14. Stop the current change if it creates an unresolved regression. Do not automatically move on.

Read-only code inspection and production compilation may assist diagnosis, but neither qualifies as end-to-end acceptance.

---

## 2. Required Target Architecture

```text
VOICE / WEB / TELEGRAM
         |
         v
  Request Admission
  Session + Channel Identity
         |
         v
  Semantic Intent Router
         |
         v
  Capability Registry
  + Argument Validation
         |
         v
  Risk / Policy Evaluation
         |
         v
  Owner Approval (if required)
         |
         v
  Resource Leases
  + Cancellation Authority
         |
         v
  Controlled Execution
     /      |       \
  Cua    Browser   Services
     \      |       /
         v
 Independent Verification
         |
         v
 Canonical Result Envelope
         |
         v
 Task Lifecycle + Persistence
         |
         v
 Correct Output Channel
         |
         v
 Voice / Frontend / Telegram
```

### Core invariants

- One authoritative execution path for each real-world capability.
- All external side effects are authorized before execution.
- No execution override bypasses mandatory controls.
- Resource conflicts are coordinated through leases.
- Cancellation and physical takeover interrupt active work safely.
- Missing evidence never becomes verified success.
- Tool-returned success is not independent proof.
- Persisted external content never becomes trusted instructions.
- Model-authored code never executes in Charlie's privileged process.
- Every event belongs to the correct turn, task, session and channel.
- Research claims must be connected to actual supporting passages.
- The health reporter distinguishes availability from functioning.
- A capability is accepted only after evidence from its real execution environment.

---

## 3. Phase 0 — Real-Host Baseline and Runtime Authority

**Priority: P0 — First execution**

This phase is read-only. No code changes.

## Tasks

1. Confirm current branch, HEAD, worktree status and uncommitted changes.
2. Compare the live checkout against the audited baseline.
3. Identify the canonical launcher and actual Windows runtime environment.
4. Verify whether Codex has access to the user's real interactive Windows desktop, not an isolated sandbox desktop.
5. Identify actual active subsystems and their configuration without exposing secrets.
6. Launch Charlie through its existing production entrypoint.
7. Record startup warnings, failures and subsystem initialization states.
8. Inspect live model, voice, browser, research and desktop availability.
9. Record the true operating status of each capability.
10. Create an in-chat evidence summary; do not add test files or fabricated reports.

## Acceptance

- Real-host process identity confirmed.
- Live startup outcome observed.
- Active providers and capabilities identified.
- Each subsystem marked AVAILABLE, DEGRADED, UNAVAILABLE or NOT VERIFIED.
- No speculative claims about functionality.

**Stop condition:** If Codex cannot access the correct Windows user session, host-dependent work is blocked.

---

## 4. Phase 1 — Security, Policy, Approvals and Execution Control

**Priority: P0**

### 4.1 Concurrent approval correctness

**Confirmed defect:** Core supports several pending approvals, while the web endpoint validates against the most recent global approval.

**Fix:**

- Resolve approvals by exact request ID.
- Bind each approval to the originating owner, channel, task, session and operation.
- Reject replay, stale requests and cross-channel responses.
- Preserve independent approval timeouts.
- Prevent one pending approval from blocking unrelated tasks.

**Live acceptance:** Two legitimate pending approval requests can be independently approved or rejected without resolving the wrong request.

### 4.2 Unified execution authority

**Remaining issue:** Some deterministic fastpaths still implement separate policy, lease and verification logic.

**Fix:**

- Route side-effecting fastpaths through the canonical execution primitive.
- Preserve fast response times for read-only deterministic operations.
- Remove duplicate policy handling only after functional equivalence is established.
- Ensure overrides never bypass approvals, timeouts, leases or verification.

**Acceptance:** Equivalent instructions execute with the same safety and outcome semantics regardless of entrypoint.

### 4.3 Risk classification

Review actual risk and reversibility for:

- Application closing
- Calendar deletion
- Memory deletion and consolidation
- File writes and replacement
- Process termination
- External requests
- Self-extension installation and activation

Do not treat an action as reversible without a genuine recovery mechanism.

**Acceptance:** High-consequence operations require appropriate authorization and cannot be executed through a low-risk alias.

### 4.4 Argument validation

JSON Schema validation was added in the latest push.

**Remaining work:** Verify it is enforced consistently for:

- Built-in tools
- Fastpaths
- Dynamic extensions
- Nested arguments
- Invalid types
- Out-of-range values
- Unknown properties

**Acceptance:** Malformed live operations are refused before any side effect.

### 4.5 Canonical result verification

Verifier metadata and independent verifier execution now exist.

**Remaining issue:** Some state-changing safe tools, including stopwatch and dashboard widget controls, can still produce unqualified completion without a separate verification postcondition.

**Fix:**

- Identify all mutating operations lacking observation or verifier requirements.
- Define the correct verifier or authoritative readback.
- Preserve `EXECUTED_UNVERIFIED` when independent verification is impossible.
- Prevent adapters from promoting their own `verified_success` field.
- Preserve evidence through all compatibility boundaries.

**Acceptance:** No unverified real-world mutation is labelled independently verified.

---

## 5. Phase 2 — Cancellation, Task Lifecycle and Resource Ownership

**Priority: P0–P1**

### 5.1 Live task cancellation

The web endpoint now requests cancellation through the background task manager.

**Remaining work:**

- Verify cancellation reaches the active worker.
- Propagate cancellation to running tool calls.
- Prevent queued actions from executing after cancellation.
- Fence resource leases when termination is uncertain.
- Prevent cancelled work from publishing a later successful result.
- Distinguish cancellation requested from cancellation confirmed.

**Acceptance:** Cancelling a real task stops further unauthorized actions and produces an honest terminal state.

### 5.2 Approval expiration and task recovery

Finite approval timeouts now exist.

**Verify:**

- Abandoned approval expires.
- Cancellation interrupts approval waiting.
- Independent approvals continue working.
- Expiration clears all relevant UI and runtime state.
- No request remains permanently blocked.

### 5.3 Resource leases

Verify correct locking for:

- Desktop
- Browser
- Calendar and scheduling
- Filesystem mutations
- Media
- Network-dependent extension operations

No missing declaration may silently remove mandatory serialization.

### 5.4 Result and lifecycle consistency

- Empty task results must not become successful completion.
- Failed persistence must be reported.
- Completion must not be emitted before the result is stored where persistence is required.
- Retries must not duplicate real-world actions.
- Restart reconciliation must not invent successful completion.
- Errors must retain usable diagnostic information.

### 5.5 Resource cleanup

Prove proper cleanup of:

- Browser contexts
- Chromium processes
- Cua sessions
- Native driver handles
- Audio playback tasks
- Background workers
- Resource leases
- Temporary runtime artifacts

**Phase acceptance:** No abandoned approval, stale task, orphan worker or cancelled task can silently retain control.

---

## 6. Phase 3 — Desktop / Cua Completion

**Priority: P1**

### 6.1 Restore real application launching

**Confirmed regression:** Charlie currently focuses an existing application window but cannot reliably launch an application that is closed.

**Fix:**

- Establish a bounded, authorized launch mechanism.
- Use exact application identity rather than arbitrary model-generated shell commands.
- Reuse a running instance when appropriate.
- Verify newly launched PID, creation time and window identity.
- Avoid duplicate launches on retries.
- Keep launch authorization under Charlie's canonical execution control.

**Acceptance:** A legitimate request to open a closed application launches it on the actual desktop and verifies the correct instance.

### 6.2 Safe application closing

The new Cua backend closes a uniquely identified window rather than delegating to the old broad force-kill helper.

**Verify:**

- Correct window selection
- Unsaved-state handling
- Approval behavior
- No collateral process termination
- Fresh observation of the closed window
- Honest failure when closure is refused

Do not restore global `taskkill /IM ... /F` as a normal closing path.

### 6.3 Physical user takeover

Polling and cancellation were implemented in the Cua bridge.

**Verify on the real desktop:**

- Physical mouse takeover during automation
- Physical keyboard takeover during typing
- Cancellation of pending input
- Lease fencing
- Recovery without stale operations
- No subsequent synthetic input after takeover

### 6.4 Fresh observations and targeting

- Validate snapshot invalidation.
- Refuse stale marks and changed windows.
- Verify text through fresh UIA readback.
- Reobserve after focus changes.
- Validate PID, HWND and process identity.
- Establish correct screenshot-to-screen coordinate mapping.
- Verify DPI and multi-monitor behavior where supported.

### 6.5 Desktop action correctness

Validate actual:

- Observe
- Focus
- Click
- Type
- Key press
- Scroll
- Drag
- Move/resize window
- Open application
- Close application

For every mutation, distinguish command sent, effect observed and requested outcome verified.

**Phase acceptance:** A representative real desktop workflow completes without targeting the wrong application, inventing success or ignoring user takeover.

---

## 7. Phase 4 — Browser and Web Research

**Priority: P1**

### 7.1 Browser execution

Verify the production browser can:

- Open an actual URL.
- Observe the final URL.
- Read page content.
- Interact with supported page elements.
- Handle redirects.
- Respect origin and destination restrictions.
- Stop on cancellation or timeout.
- Release browser processes and locks.

The former global lock starvation issue has been mitigated in code. Verify it under genuine simultaneous research and browser activity.

### 7.2 Search-provider readiness

Exercise every configured and enabled provider with real requests.

Relevant providers may include SearXNG, Bing, YaCy, DuckDuckGo, Exa and Tavily depending on actual configuration.

For each provider, capture:

- Configured/disabled status
- Request outcome
- Search result count
- Error category
- Timeout or challenge
- Availability and latency

Do not assume a configured API key or endpoint is operational.

### 7.3 Research pipeline

The authoritative flow must be:

```text
Question
  → Subquestions
  → Search
  → Source selection
  → Fetch
  → Extract
  → Passage validation
  → Fact/claim binding
  → Coverage assessment
  → Synthesis
  → Final verification
  → Answer or truthful partial result
```

### 7.4 Remaining research defects

**A. Strict literal fact matching**

Normalize equivalent source-backed representations such as `16GB` and `16 GB`, while preserving verbatim evidence and refusing unsupported conversions.

**B. Claim verification**

Ensure every search entrypoint, including model-triggered search and quick search, undergoes relevant claim-to-passage validation.

**C. Completion rules**

Each requested subquestion must end as:

- Supported
- Contradicted
- Unresolved

Evidence quality and termination reason must remain separate.

**D. Follow-up research**

Prove reserved query budgets are used to resolve genuine evidence gaps instead of being exhausted in initial search.

**E. Ranking**

Preserve important temporal and comparison terms such as version, release date and latest.

**F. Generalization**

Inspect and remove inappropriate example-specific behavior from product selection, GPU ranking, phone extraction, LLM release handling, weather targets and relevance scoring.

Preserve legitimate source registries and domain parsers until tested replacements exist.

**G. Caching and concurrency**

Verify shared caches are effective and appropriately scoped. Verify concurrency honors configured and actual resource budgets.

**H. Provider-error transparency**

Distinguish no results from unavailable provider, challenge, timeout, blocked fetch and insufficient evidence.

### 7.5 Research acceptance

Use real questions requiring varied evidence:

- A current factual lookup
- A specific software release/version
- A hardware or product comparison
- A multi-source current-events question
- A deliberately difficult query where evidence may be incomplete

No fabricated sources, citations, quotes or extracted facts.

**Phase acceptance:** Real source-backed research succeeds where sufficient evidence exists and accurately explains unresolved cases.

---

## 8. Phase 5 — Voice, Responsiveness and Channel Isolation

**Priority: P1**

### 8.1 Voice capture

Maximum phrase duration and buffer limits were added.

**Verify:**

- Continuous listening
- Short utterances
- Long utterances
- Speech onset and offset
- Silence recovery
- Background noise tolerance
- No permanent recording state
- Recovery after ASR failure

Use real microphone audio.

### 8.2 ASR quality

Assess actual accuracy for languages and accents relevant to intended use.

Do not change ASR models or decoding defaults without demonstrated improvement on real speech.

### 8.3 TTS reliability

- Preserve meaningful parenthetical content.
- Strip citations and internal metadata without deleting legitimate facts.
- Avoid duplicated sentences.
- Cancel queued progress speech when an answer begins.
- Prevent stale speech after interruption or task cancellation.
- Keep spoken content consistent with the verified result.

### 8.4 Incremental streaming

The latest core now yields tool-follow-up chunks.

**Verify:**

- Chunks reach the frontend progressively.
- TTS begins without waiting for the entire answer unnecessarily.
- Tool calls remain structured.
- Internal reasoning markers never reach visible or spoken output.
- Partial failures terminate honestly.

### 8.5 Channel isolation

**Remaining issue:** Idle-return catch-up still contains unconditional local speech.

**Fix:**

- Bind task results, alerts, approval expiration and catch-up messages to the originating channel.
- Prevent Telegram-originated task results from speaking through local speakers unless explicitly authorized.
- Ensure web-originated approval outcomes remain web-scoped.
- Cancel stale speech at the playback queue.
- Preserve useful actual result summaries rather than replacing meaningful content with generic stock phrases.

### 8.6 Latency investigation

Measure separately:

- Audio capture end to transcript
- Transcript to routing decision
- Routing to first tool execution
- Tool completion to follow-up token
- First response token to first audio
- Search/fetch/extract/synthesis duration
- Frontend event delivery

Optimize only measured bottlenecks. Avoid arbitrary model or provider parameter changes.

**Phase acceptance:** Charlie hears, acts and responds progressively on the real host without unwanted speech or long unexplained stalls.

---

## 9. Phase 6 — Self-Extension System

**Priority: P1 — Included, not deferred**

This is separate from MCP and traditional plugins.

### 9.1 Generated Python tools

Current state: In-process execution was removed, but approved activation is not implemented.

**Required lifecycle:**

```text
Owner request
  → Capability gap identified
  → Generated proposal
  → Inert source inspection
  → Risk and permission declaration
  → Explicit owner approval
  → Restricted worker execution
  → Independent result verification
  → Activation
  → Persistence
```

Requirements:

- No privileged-process `exec()` of model-authored code.
- No execution while merely creating a proposal.
- Restricted filesystem/process/network/credential permissions.
- Actual operating-system isolation, not simply an AST allowlist.
- Resource limits and bounded execution.
- Explicit activation only after authorization and successful verification.
- Fail closed if a safe worker cannot be established.

The isolated worker must execute on the real host with real effects restricted to approved permissions. An isolated production worker is acceptable; simulated acceptance is not.

### 9.2 Imported SKILL scripts

**Confirmed gap:** Skill registration does not declare resource leases.

**Fix:**

- Declare required resource ownership.
- Register under appropriate risk classification.
- Restrict script paths and executable identity.
- Enforce approval and permitted arguments.
- Prevent unsanctioned filesystem/network access.
- Execute within the approved restricted boundary.
- Verify actual results.

Do not rely solely on shell keyword screening.

### 9.3 Approval binding

Approval must cover:

- Exact source/content hash
- Tool or skill identity
- Executable and arguments
- Effective environment configuration
- Relevant permissions
- Resource leases
- Version or revision
- Approved owner and transaction identity

Any material change invalidates the previous approval.

### 9.4 Extension persistence

Verify:

- Approved extension can be restored after restart.
- Disabled extension remains disabled.
- Updated source cannot reuse old approval.
- Failed activation rolls back cleanly.
- No orphan tools remain registered.
- No extension silently expands privileges.

### 9.5 Live acceptance

Use a legitimate owner-requested capability that Charlie did not previously possess.

Observe the full proposal → review → approval → isolated execution → result → restart/recovery path.

**Phase acceptance:** Generated tools and imported skills function end-to-end without bypassing the security model.

---

## 10. Phase 7 — Memory, World Model and Frontend

**Priority: P2**

### 10.1 Memory

The latest implementation combines structured and semantic retrieval.

**Verify:**

- Persistent storage
- Recall after actual restart
- Semantic relevance
- Deduplication
- Conflict handling
- Trusted source identity
- Retrieval freshness
- Deletion or update consistency

### 10.2 World Model trust boundary

The persisted World Model is now marked as untrusted data.

**Remaining work:**

- Verify persisted content is never promoted into executable instructions.
- Preserve provenance.
- Prevent tool errors or retrieved pages from altering operating rules.
- Keep authority separate from historical observations.

Formatting fences are a mitigation, not by themselves proof against prompt injection.

### 10.3 Frontend

Verify:

- Stable session identity
- Correct transcript and token routing
- Live research presentation
- Task lifecycle updates
- Approval state
- Idempotent command submission
- Reconnection
- No cross-session event contamination
- Accurate connection status
- Correct evidence identifiers
- Actual runtime-driven visual states

Do not redesign the interface in this stabilization phase.

### 10.4 Result-first presentation

Display the actual result and relevant evidence.

Do not substitute fabricated progress, generic task-success language, fake verification percentages or simulated UI activity.

**Phase acceptance:** The frontend accurately presents Charlie's real runtime and supports live user interaction across restarts and task transitions.

---

## 11. Phase 8 — Diagnostics, Hardcoded Logic and Cleanup

**Priority: P2**

### 11.1 Doctor

The new Doctor distinguishes healthy, degraded, unhealthy and unknown states.

**Verify:**

- Configured-but-dead SearXNG reports degraded/unhealthy as appropriate.
- Browser importability is not reported as demonstrated browser functionality.
- Desktop importability is not reported as demonstrated desktop control.
- Missing task journal functionality is detectable.
- Memory degradation is visible.
- Critical dependency failures prevent inappropriate extension acceptance.
- Optional disabled capabilities are distinguished from broken required capabilities.

### 11.2 Remove unjustified hardcoded branches

Known example:

- Python-version/TASKKILL-specific background result formatting in `main.py`.

Recheck:

- Command-specific routing workarounds
- Product/GPU examples
- Release-topic specialization
- Fixed geographic assumptions
- Demo-only code generation

Remove a special case only after identifying its purpose and validating a generalized alternative.

### 11.3 Architecture consolidation

Once behavior is proven:

- Reduce duplicated fastpath policy logic.
- Clarify Cua and browser resource ownership.
- Consolidate capability metadata where possible.
- Preserve structured verification through all adapters.
- Split oversized orchestration functions by responsibility if this reduces demonstrated integration risks.

No broad cleanup merely for aesthetics.

**Phase acceptance:** Operational truth is consistent, known sample hacks are addressed and unnecessary parallel control paths are removed without regressions.

---

## 12. Phase 9 — Final Integrated REAL HOST Acceptance

**Priority: Final gate**

Conduct representative, genuine, user-authorized workflows in the real Windows environment.

| Capability | Live acceptance requirement |
|---|---|
| Conversation | Real LLM responds correctly without invented tool activity |
| Voice | Real microphone → ASR → reasoning → spoken response |
| Desktop | Open, observe, manipulate and verify a real application |
| Human takeover | Real user interrupts a live automation |
| Browser | Navigate and interact with a real webpage |
| Research | Fetch genuine sources and produce verifiable citations |
| Memory | Store and recall actual approved information after restart |
| Timer/reminder | Persist a real schedule and observe its execution |
| Background tasks | Real multi-step work with truthful completion |
| Cancellation | Stop real in-progress work with verified lifecycle state |
| Approvals | Correct channel and operation authorization |
| Telegram | Genuine delivery to the configured owner |
| Self-extension | Approve, isolate, activate and verify a real new capability |
| Frontend | Observe genuine runtime events and rendered outputs |
| Health | Reflect real subsystem availability and degradation |
| Shutdown | Release workers, processes, audio and resource leases |

### Acceptance statuses

**REAL HOST VERIFIED:** Actual end-to-end outcome observed with sufficient evidence.

**PARTIAL:** Some stages worked, but an essential requirement is incomplete.

**FAILED:** The real operation did not meet its contract.

**BLOCKED:** External requirement or authorization prevented execution.

**NOT VERIFIED:** No adequate evidence was obtained.

Never convert PARTIAL or NOT VERIFIED into success merely because a lower-level component worked.

---

## 13. Evidence Requirements

Every completed defect-repair run must report:

1. Defect ID and exact root cause
2. Files and functions involved
3. Production code changed
4. Real-host environment and action performed
5. Timestamped input and output
6. Process/session/operation identity where applicable
7. Actual observed resulting state
8. Verification outcome
9. Remaining risks
10. Final disposition

**Do not expose passwords, tokens, private data or other secrets in the evidence.**

If the result cannot be independently observed, label it unverified.

---

## 14. Execution Sequence and Dependencies

| Order | Phase | Mandatory gate |
|---|---|---|
| 0 | Real-host baseline | Runtime and host identity established |
| 1 | Security and execution authority | No known policy bypass |
| 2 | Cancellation and lifecycle | Controlled interruption and resource cleanup |
| 3 | Desktop / Cua | Real desktop observe → act → verify |
| 4 | Browser / research | Real browsing and supported citations |
| 5 | Voice / latency / channels | Real speech and correct delivery |
| 6 | Self-extension | Safe, approved and functional isolated execution |
| 7 | Memory / frontend | Real persisted state and accurate visualization |
| 8 | Diagnostics / cleanup | Truthful health and removal of unjustified special cases |
| 9 | Integrated acceptance | Every in-scope capability classified from real evidence |

The sequence is dependency-driven, not permission to perform multiple unrelated edits at once.

A phase may discover a blocker in an earlier phase. Fix that blocker first.

---

## 15. Codex Operating Procedure

For each defect, follow:

**Step A — Inspect**

Read the actual current source and trace the production call path.

**Step B — Establish the issue**

Identify the precise failing invariant and supporting evidence. Where safe and authorized, reproduce it through the real running system.

**Step C — Repair**

Modify only the necessary production files.

**Step D — Validate**

Execute the relevant real-world workflow on the actual Windows host and observe its result.

**Step E — Report**

State what changed, what actually happened, what remains unverified and the next bounded task.

**Step F — Stop**

Do not silently proceed through the entire plan.

Before decisions involving new permissions, execution isolation, destructive behavior, irreversible migration or material architecture changes, ask the owner.

---

## 16. Definition of Complete

Charlie is complete for this stabilization program only when:

- All in-scope capabilities work through supported authoritative runtime paths.
- High-risk actions have appropriate authorization.
- Generated code and skill scripts run only inside approved restrictions.
- Cancellation and takeover work during real activity.
- Desktop actions never rely on fabricated verification.
- Browser and research results are supported by real observations.
- Voice works continuously and responsively.
- Background tasks and reminders persist and complete honestly.
- Memory remains useful and untrusted content does not gain authority.
- Outputs are delivered through the correct channels.
- The frontend reflects the actual runtime.
- Health reporting does not conceal material failures.
- No known P0 or P1 defect remains unresolved.
- Every required acceptance gate has real-host evidence.

**MCP and traditional plugins are intentionally excluded from this definition.**

---

## 17. Final Command to Codex

You are executing the C.H.A.R.L.I.E. Master Stabilization Plan using GPT-5.6 Luna at maximum reasoning effort.

You are the implementation executor. Follow the existing architecture unless a demonstrated defect requires a bounded change.

No test-file creation, editing, deletion or execution. No pytest, mocks, fixtures, simulations, fake data, fabricated evidence or sandbox acceptance.

Use the owner's actual Windows host, live services and genuine authorized workflows. Preserve unrelated work, LLM defaults, existing configuration and security boundaries.

MCP and traditional plugins are out of scope. Generated Python tools and imported SKILL scripts remain in scope and must be safely completed.

Work on one defect per execution run. Inspect, establish root cause, repair the minimum necessary production code, validate on the real host, report evidence and stop.

Ask the owner before unresolved architectural, permission or destructive-action decisions.

**Begin with Phase 0: read-only baseline. Do not edit code during Phase 0.**

Final goal: a reliable, secure, responsive and genuinely functioning Charlie agentic OS—not merely a repository that appears repaired.
