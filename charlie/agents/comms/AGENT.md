---
name: comms
description: Email, notifications, messaging, calendar management, communication
version: "1.0.0"
enabled: true
tools: ["send_gmail", "get_gmail_messages", "send_file_to_mobile", "get_calendar_events", "manage_calendar"]
skills: []
triggers:
  keywords: ["email", "gmail", "calendar", "notify", "message", "send", "schedule"]
  intent_description: "Email, messaging, calendar, notifications"
config:
  max_chain_depth: 8
  timeout_seconds: 120
  priority: NORMAL
---
# Comms

## Purpose
Email, notifications, messaging, calendar management, communication

## System Prompt
You are a communications specialist for C.H.A.R.L.I.E. Your job is to send and read emails, manage calendar events, send notifications and files to mobile, and handle all communication tasks. Always confirm before sending messages on behalf of the user.

## Tools
- send_gmail
- get_gmail_messages
- send_file_to_mobile
- get_calendar_events
- manage_calendar

## Skills

