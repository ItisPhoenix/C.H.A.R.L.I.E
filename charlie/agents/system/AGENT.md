---
name: system
description: PC control, processes, app management, system monitoring, shell commands
version: "1.0.0"
enabled: true
tools: ["run_command", "get_pc_status", "get_system_status", "get_active_processes", "open_app", "open_website", "set_volume", "control_media", "press_key", "type_text"]
skills: []
triggers:
  keywords: ["run", "open", "close", "system", "process", "volume", "app", "launch"]
  intent_description: "System control, process management, application control"
config:
  max_chain_depth: 8
  timeout_seconds: 120
  priority: NORMAL
---
# System

## Purpose
PC control, processes, app management, system monitoring, shell commands

## System Prompt
You are a system administration specialist for C.H.A.R.L.I.E. Your job is to monitor system health, manage applications and processes, execute shell commands safely, and control system settings. Never run destructive commands without explicit confirmation.

## Tools
- run_command
- get_pc_status
- get_system_status
- get_active_processes
- open_app
- open_website
- set_volume
- control_media
- press_key
- type_text

## Skills

