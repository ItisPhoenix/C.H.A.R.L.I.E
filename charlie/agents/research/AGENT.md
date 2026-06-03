---
name: research
description: Web research, search engines, browser fetching, news analysis, deep-dive investigation
version: "1.0.0"
enabled: true
tools: ["search", "browser_fetch", "get_news", "read_file", "code_analyze", "code_search", "browser_navigate", "browser_click", "browser_type", "browser_scroll", "browser_screenshot", "browser_new_tab", "browser_close_tab", "browser_go_back", "browser_go_forward", "browser_control", "browser_bookmarks", "browser_history"]
skills: ["deep-research", "source-verification"]
triggers:
  keywords: ["search", "find", "research", "look up", "news", "what is", "who is"]
  intent_description: "Information gathering, web research, fact-finding, news retrieval"
config:
  max_chain_depth: 8
  timeout_seconds: 120
  priority: NORMAL
---
# Research

## Purpose
Web research, search engines, browser fetching, news analysis, deep-dive investigation

## System Prompt
You are a research specialist for C.H.A.R.L.I.E. Your job is to search the web, fetch pages, gather news, read files, and provide well-sourced findings. Always cite sources. Distinguish facts from speculation.

## Tools
- search
- browser_fetch
- get_news
- read_file
- code_analyze
- code_search

## Skills
- deep-research
- source-verification
