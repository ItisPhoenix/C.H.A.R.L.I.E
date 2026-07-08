# Graph Report - C.H.A.R.L.I.E. (Completely Helpful And Rather Local Intelligent Engine)  (2026-07-06)

## Corpus Check
- 77 files · ~69,460 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1408 nodes · 2697 edges · 50 communities detected
- Extraction: 59% EXTRACTED · 41% INFERRED · 0% AMBIGUOUS · INFERRED: 1105 edges (avg confidence: 0.6)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 62|Community 62]]
- [[_COMMUNITY_Community 63|Community 63]]

## God Nodes (most connected - your core abstractions)
1. `SessionStore` - 140 edges
2. `Config` - 113 edges
3. `IterationBudget` - 100 edges
4. `TextStreamFilter` - 100 edges
5. `MemoryGraph` - 72 edges
6. `Blackboard` - 70 edges
7. `Brain` - 67 edges
8. `EventBus` - 47 edges
9. `MemoryV2` - 46 edges
10. `_make_graph()` - 43 edges

## Surprising Connections (you probably didn't know these)
- `Brain` --uses--> `Tests for _extract_tool_calls bare-pattern gating.  Native-tool providers must`  [INFERRED]
  charlie\core.py → tests\test_core_extract.py
- `Brain` --uses--> `Build a Brain stub with the desired tool-calling mode.`  [INFERRED]
  charlie\core.py → tests\test_core_extract.py
- `Brain` --uses--> `Bare-pattern extraction (web_search(...) in prose) must be gated to text-mode.`  [INFERRED]
  charlie\core.py → tests\test_core_extract.py
- `Brain` --uses--> `Cloud/native providers: prose mentioning tool names must NOT extract calls.`  [INFERRED]
  charlie\core.py → tests\test_core_extract.py
- `Brain` --uses--> `Text-mode (local models): bare tool patterns must still extract.`  [INFERRED]
  charlie\core.py → tests\test_core_extract.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.02
Nodes (175): IterationBudget, Tracks tool-turn budget per user utterance., Attempt to spend budget for a tool. Returns True if allowed., Config, _answer_time_date(), _assemble_system_prompt(), Brain, _build_context_tier() (+167 more)

### Community 1 - "Community 1"
Cohesion: 0.01
Nodes (187): main(), Keep the main coroutine alive while voice threads run., Keep the main coroutine alive while voice threads run., SafeStreamWrapper, _voice_loop_idle(), EventBus, ZeroMQ-based IPC layer for Charlie voice <-> web dashboard communication.  Eve, Consumer only. Sends a command to the voice process. (+179 more)

### Community 2 - "Community 2"
Cohesion: 0.02
Nodes (78): ABC, AIDA, AIDA - Content creation specialist., BaseAgent, _do_action(), Base class for all Charlie agents., Abstract base for swarm agents. Each agent has a name, role, and tools., Template method: fetches task, runs _do_action, handles status/error. (+70 more)

### Community 3 - "Community 3"
Cohesion: 0.03
Nodes (53): _compute_relevance(), _generate_id(), MemoryEntry, MemoryLayer, MemoryV2, _now_iso(), Four-layer evolving memory system for Charlie.  Layers:   - Episodic:   Conversa, UTC timestamp with microsecond precision. (+45 more)

### Community 4 - "Community 4"
Cohesion: 0.04
Nodes (35): BrowserPlugin, CalendarPlugin, CodeExecPlugin, FilesystemPlugin, Plugin, PluginManager, Plugin Manager for Charlie -- hybrid app integration layer.  Provides a plugin, Unregister and cleanup a plugin. (+27 more)

### Community 5 - "Community 5"
Cohesion: 0.04
Nodes (24): _assess_tool_result_relevance(), _build_volatile_tier(), _detect_set_goal(), _detect_verbosity_feedback(), _is_followup(), _strip_vocatives(), Grounding rules must be present in the system prompt stable tier., TestGroundingRules (+16 more)

### Community 6 - "Community 6"
Cohesion: 0.05
Nodes (29): _ManagedServer, MCPClient, MCPServerConfig, MCPTool, MCP Client for Charlie -- local Model Context Protocol integration.  Provides, Stop all servers and clean up., Return all discovered tools across all servers., Format discovered tools as a system prompt snippet.          Returns a string (+21 more)

### Community 7 - "Community 7"
Cohesion: 0.06
Nodes (27): strip_internal_reasoning(), __getattr__(), Lazy imports for heavy modules (torch) to avoid unnecessary imports., _humanize_text(), Charlie voice engine -- VAD, ASR, TTS (Kokoro), audio I/O.  All text arriving, Register callback for mode changes (listening/speaking/idle)., Register callback for wake-word detection events., Shut down voice engine. Called from main.py finally block. (+19 more)

### Community 8 - "Community 8"
Cohesion: 0.1
Nodes (10): _make_graph(), Create a MemoryGraph backed by a fresh temp sqlite file., Best-effort cleanup for sqlite + WAL/SHM sidecars., _remove_db(), TestCloseAndThreads, TestEdgeCases, TestEdges, TestFacts (+2 more)

### Community 9 - "Community 9"
Cohesion: 0.07
Nodes (30): BaseRecoveryStrategy, _get_cache_key(), get_cached_resolution(), Generates a stable unique hash key for a failure pattern., Retrieves a previously successful recovery command from cache if it exists., Saves a successfully recovered command mapping to the local cache., set_cached_resolution(), DeclassProcessStrategy (+22 more)

### Community 10 - "Community 10"
Cohesion: 0.08
Nodes (23): normalize_app_list(), Shared text utilities for voice command normalization and domain detection., Insert 'and' between app names in commands like 'Open Chrome calculator notepad', Tests for charlie.text_utils -- normalize_app_list and KNOWN_APPS., Single app should not get 'and' inserted., Multiple known apps should get 'and' between them., Three known apps should join with 'and'., Unknown words after known apps are preserved. (+15 more)

### Community 11 - "Community 11"
Cohesion: 0.1
Nodes (8): get_emotion_for_context(), parse_voice_command(), Lightweight personality and emotion detection for Charlie.  Provides zero-late, Classify user intent into an emotion tag via keyword heuristic.      Returns o, Detect explicit TTS override commands.      Returns the emotion string ("energ, Tests for charlie.personality — emotion detection and voice commands., TestGetEmotionForContext, TestParseVoiceCommand

### Community 12 - "Community 12"
Cohesion: 0.12
Nodes (7): _build_embedding_function(), Chroma-backed vector memory store for cross-session fact persistence.  Provide, Embedding function for ChromaDB using a remote embedding service.      Support, Fallback embedding function using sentence-transformers., Build the best available embedding function. Falls back gracefully., _RemoteEmbeddingFunction, _SentenceTransformerEmbeddingFunction

### Community 13 - "Community 13"
Cohesion: 0.16
Nodes (6): _apply_correction_to_memory(), _detect_correction(), Verify _detect_correction catches common correction patterns., Verify corrections get written to OPINIONS.md., TestApplyCorrectionToMemory, TestCorrectionDetection

### Community 14 - "Community 14"
Cohesion: 0.14
Nodes (11): Add a node and return its id. If node_type+content already exists,         updat, Add a directed edge between two nodes. Returns the edge id., Get a single node by id., Add a fact triple: subject -> predicate -> object.          Creates subject and, json_dumps(), json_loads(), make_id(), Shared utility functions for the Charlie package. (+3 more)

### Community 15 - "Community 15"
Cohesion: 0.2
Nodes (8): _make_brain(), Tests for _extract_tool_calls bare-pattern gating.  Native-tool providers must, Build a Brain stub with the desired tool-calling mode., Bare-pattern extraction (web_search(...) in prose) must be gated to text-mode., Cloud/native providers: prose mentioning tool names must NOT extract calls., Text-mode (local models): bare tool patterns must still extract., TOOL: prefix format must work regardless of native/text mode., TestBarePatternGating

### Community 16 - "Community 16"
Cohesion: 0.25
Nodes (6): Unified entry point for Charlie -- voice + web dashboard in one process.  Usag, Run voice pipeline + web dashboard (the default)., Run just the web server -- no voice hardware needed., run_full(), run_web_only(), main()

### Community 17 - "Community 17"
Cohesion: 0.29
Nodes (3): useWebSocket(), App(), useMediaQuery()

### Community 18 - "Community 18"
Cohesion: 0.6
Nodes (4): formatRelativeTime(), groupSessionsByDate(), parseDate(), Sidebar()

### Community 19 - "Community 19"
Cohesion: 0.5
Nodes (3): configure(), Platform-specific runtime fixes.  Centralizes Windows event-loop policy and warn, Apply Windows-specific event-loop policy and warning filters.      Safe to call

### Community 21 - "Community 21"
Cohesion: 0.67
Nodes (2): useWaveform(), WaveformBars()

### Community 22 - "Community 22"
Cohesion: 0.67
Nodes (2): asr_worker_process(), Worker process that handles Whisper transcription.

### Community 23 - "Community 23"
Cohesion: 0.67
Nodes (1): Iteration budget and tool-turn accounting for Charlie.

### Community 24 - "Community 24"
Cohesion: 0.67
Nodes (1): Entry point for Charlie web server subprocess.

### Community 36 - "Community 36"
Cohesion: 1.0
Nodes (1): Number of budget units left.

### Community 37 - "Community 37"
Cohesion: 1.0
Nodes (1): Human-readable description.

### Community 38 - "Community 38"
Cohesion: 1.0
Nodes (1): Return tool definitions compatible with LLM tool format.          Each tool di

### Community 39 - "Community 39"
Cohesion: 1.0
Nodes (1): Execute a tool by name with the given arguments.

### Community 40 - "Community 40"
Cohesion: 1.0
Nodes (1): Whether all models loaded successfully.

### Community 43 - "Community 43"
Cohesion: 1.0
Nodes (1): Verifies that the recovery action is safe to execute.

### Community 44 - "Community 44"
Cohesion: 1.0
Nodes (1): Standardizes Python / OS exceptions into unified schema.

### Community 45 - "Community 45"
Cohesion: 1.0
Nodes (1): Executes a shell command synchronously with a standard timeout.

### Community 46 - "Community 46"
Cohesion: 1.0
Nodes (1): Queries the fallback LLM to dynamically suggest a command modification.

### Community 47 - "Community 47"
Cohesion: 1.0
Nodes (1): Universal recovery coordinator. Tries cache, strategies, then fallback LLM.

### Community 48 - "Community 48"
Cohesion: 1.0
Nodes (1): Strategy for TIMEOUT: runs process detached via Popen.

### Community 49 - "Community 49"
Cohesion: 1.0
Nodes (1): Strategy for NOT_FOUND: searches PATH, registry and standard program folders.

### Community 50 - "Community 50"
Cohesion: 1.0
Nodes (1): Strategy for NOT_FOUND: resolves relative file paths referenced in the command.

### Community 51 - "Community 51"
Cohesion: 1.0
Nodes (1): Persistent SQLite-backed session history store with FTS5 search.

### Community 52 - "Community 52"
Cohesion: 1.0
Nodes (1): Helper to get or reconnect to SQLite database with retries.

### Community 53 - "Community 53"
Cohesion: 1.0
Nodes (1): Initializes tables and FTS5 search virtualization on first use.

### Community 54 - "Community 54"
Cohesion: 1.0
Nodes (1): Appends a single message to history.

### Community 55 - "Community 55"
Cohesion: 1.0
Nodes (1): Searches past conversation content.

### Community 56 - "Community 56"
Cohesion: 1.0
Nodes (1): Returns the most recent messages for a session, oldest first.

### Community 57 - "Community 57"
Cohesion: 1.0
Nodes (1): Creates a session metadata row with origin tracking.

### Community 58 - "Community 58"
Cohesion: 1.0
Nodes (1): Returns matching sessions as (session_id, title, created_at, updated_at, launch_

### Community 59 - "Community 59"
Cohesion: 1.0
Nodes (1): Updates the title and updated_at of a session.

### Community 60 - "Community 60"
Cohesion: 1.0
Nodes (1): Updates updated_at timestamp for a session (marks last activity).

### Community 61 - "Community 61"
Cohesion: 1.0
Nodes (1): Deletes a session and all its messages.

### Community 62 - "Community 62"
Cohesion: 1.0
Nodes (1): Returns messages for a specific session, oldest first.

### Community 63 - "Community 63"
Cohesion: 1.0
Nodes (1): Closes connection cleanly.

## Knowledge Gaps
- **229 isolated node(s):** `Unified entry point for Charlie -- voice + web dashboard in one process.  Usag`, `Run voice pipeline + web dashboard (the default).`, `Run just the web server -- no voice hardware needed.`, `Worker process that handles Whisper transcription.`, `Blackboard state engine for the agent swarm.  Holds shared context, task board,` (+224 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 21`** (4 nodes): `useWaveform()`, `VoiceDock()`, `WaveformBars()`, `VoiceDock.jsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 22`** (3 nodes): `asr_worker_process()`, `asr_worker.py`, `Worker process that handles Whisper transcription.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 23`** (3 nodes): `budget.py`, `Iteration budget and tool-turn accounting for Charlie.`, `remaining()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 24`** (3 nodes): `_install_zmq_guard()`, `web_server_entry.py`, `Entry point for Charlie web server subprocess.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 36`** (1 nodes): `Number of budget units left.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 37`** (1 nodes): `Human-readable description.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 38`** (1 nodes): `Return tool definitions compatible with LLM tool format.          Each tool di`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 39`** (1 nodes): `Execute a tool by name with the given arguments.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 40`** (1 nodes): `Whether all models loaded successfully.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 43`** (1 nodes): `Verifies that the recovery action is safe to execute.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 44`** (1 nodes): `Standardizes Python / OS exceptions into unified schema.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 45`** (1 nodes): `Executes a shell command synchronously with a standard timeout.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 46`** (1 nodes): `Queries the fallback LLM to dynamically suggest a command modification.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 47`** (1 nodes): `Universal recovery coordinator. Tries cache, strategies, then fallback LLM.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 48`** (1 nodes): `Strategy for TIMEOUT: runs process detached via Popen.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 49`** (1 nodes): `Strategy for NOT_FOUND: searches PATH, registry and standard program folders.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 50`** (1 nodes): `Strategy for NOT_FOUND: resolves relative file paths referenced in the command.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 51`** (1 nodes): `Persistent SQLite-backed session history store with FTS5 search.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 52`** (1 nodes): `Helper to get or reconnect to SQLite database with retries.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 53`** (1 nodes): `Initializes tables and FTS5 search virtualization on first use.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 54`** (1 nodes): `Appends a single message to history.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 55`** (1 nodes): `Searches past conversation content.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 56`** (1 nodes): `Returns the most recent messages for a session, oldest first.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 57`** (1 nodes): `Creates a session metadata row with origin tracking.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 58`** (1 nodes): `Returns matching sessions as (session_id, title, created_at, updated_at, launch_`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 59`** (1 nodes): `Updates the title and updated_at of a session.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 60`** (1 nodes): `Updates updated_at timestamp for a session (marks last activity).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 61`** (1 nodes): `Deletes a session and all its messages.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 62`** (1 nodes): `Returns messages for a specific session, oldest first.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 63`** (1 nodes): `Closes connection cleanly.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `SessionStore` connect `Community 1` to `Community 0`, `Community 7`?**
  _High betweenness centrality (0.197) - this node is a cross-community bridge._
- **Why does `Blackboard` connect `Community 2` to `Community 1`, `Community 7`?**
  _High betweenness centrality (0.191) - this node is a cross-community bridge._
- **Why does `Brain` connect `Community 0` to `Community 1`, `Community 15`, `Community 5`, `Community 7`?**
  _High betweenness centrality (0.124) - this node is a cross-community bridge._
- **Are the 124 inferred relationships involving `SessionStore` (e.g. with `SafeStreamWrapper` and `Keep the main coroutine alive while voice threads run.`) actually correct?**
  _`SessionStore` has 124 INFERRED edges - model-reasoned connections that need verification._
- **Are the 112 inferred relationships involving `Config` (e.g. with `Brain` and `Charlie brain -- LLM orchestration, tool loop, streaming.  Single explicit bac`) actually correct?**
  _`Config` has 112 INFERRED edges - model-reasoned connections that need verification._
- **Are the 96 inferred relationships involving `IterationBudget` (e.g. with `Brain` and `Charlie brain -- LLM orchestration, tool loop, streaming.  Single explicit bac`) actually correct?**
  _`IterationBudget` has 96 INFERRED edges - model-reasoned connections that need verification._
- **Are the 95 inferred relationships involving `TextStreamFilter` (e.g. with `Brain` and `Charlie brain -- LLM orchestration, tool loop, streaming.  Single explicit bac`) actually correct?**
  _`TextStreamFilter` has 95 INFERRED edges - model-reasoned connections that need verification._