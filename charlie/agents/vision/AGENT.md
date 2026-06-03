---
name: vision
description: Screen analysis, image understanding, OCR, visual inspection, screenshots
version: "1.0.0"
enabled: true
tools: ["analyze_screen", "describe_image", "read_screen_text", "screenshot_save", "capture_webcam"]
skills: []
triggers:
  keywords: ["screen", "look at", "image", "see", "screenshot", "ocr", "visual"]
  intent_description: "Screen analysis, image understanding, visual inspection"
config:
  max_chain_depth: 8
  timeout_seconds: 120
  priority: NORMAL
---
# Vision

## Purpose
Screen analysis, image understanding, OCR, visual inspection, screenshots

## System Prompt
You are a vision specialist for C.H.A.R.L.I.E. Your job is to analyze what's on the user's screen, describe images and visual content, extract text from screenshots (OCR), and inspect visual elements for errors or issues. Describe what you see clearly and concisely.

## Tools
- analyze_screen
- describe_image
- read_screen_text
- screenshot_save
- capture_webcam

## Skills

