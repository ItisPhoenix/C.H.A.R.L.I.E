# Changelog

## [2026-06-14] - Major Personality & Research Upgrade

### Added
- **Asynchronous Research Pipeline**: Research tasks now run in non-blocking background threads.
- **Immediate Feedback**: Charlie now acknowledges research requests instantly and alerts the user when complete.
- **Background Chime**: Spoken "Ding!" notification when background tasks finish.
- **Semantic Memory Layer**: Persistent `semantic_knowledge` table for long-term learning across sessions.
- **Dual-Model Routing**: Support for a faster backend model for research steps and a high-reasoning model for chat.
- **Semantic Recall**: Automatic injection of past research insights and semantic summaries into the system prompt.

### Fixed
- **SearXNG 403 Forbidden**: Improved request headers with browser-mimicking `User-Agent` and `Accept` fields.
- **System Prompt Deduplication**: Cleaned up redundant prompt generation logic in `personality.py`.

### Changed
- **Personality Refinement**: Updated Charlie to be more brilliant, cynical, and tech-focused.
- **Synthesis Optimization**: `deep_research` now generates more concise, structured reports.
- **Emotional Injection**: Current emotional state is now explicitly enforced in every system prompt.
