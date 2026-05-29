# Tasker Setup Guide — CHARLIE Call Tracking

This guide walks you through setting up Tasker on Android to automatically forward call events to CHARLIE via Telegram.

## Prerequisites

- Android phone with [Tasker](https://play.google.com/store/apps/details?id=net.dinglisch.android.taskerm) installed
- Telegram bot token and chat ID configured in CHARLIE
- Tasker permissions: Phone, SMS, Notification Access

## Step 1: Create the Tasker Profile

### Import the Profile

1. Download the Tasker profile XML: `charlie-call-tracker.xml` (below)
2. Open Tasker → Profiles → Import → Select the XML file
3. Enable the profile

### Or Create Manually

1. Open Tasker → **Profiles** → **+** → **Event** → **Phone** → **Phone Ringing**
2. Create a new Task called `CHARLIE Call Event`

### Task Actions

Add these actions in order:

#### Action 1: Variable Set
- **Name:** `%call_type`
- **To:** `incoming`

#### Action 2: Variable Set
- **Name:** `%call_number`
- **To:** `%CNUM`

#### Action 3: Wait
- **Seconds:** 2

#### Action 4: HTTP Request
- **Method:** POST
- **URL:** `https://api.telegram.org/botYOUR_TOKEN/sendMessage`
- **Headers:** `Content-Type: application/json`
- **Body:**
```json
{
  "chat_id": "YOUR_CHAT_ID",
  "text": "CALL|incoming|%call_number|%TIMES|0"
}
```

## Step 2: Track Missed Calls

Create another profile:

1. **Profiles** → **+** → **Event** → **Phone** → **Missed Call**
2. Task: `CHARLIE Missed Call`

### Task Actions

#### Action 1: HTTP Request
- **Method:** POST
- **URL:** `https://api.telegram.org/botYOUR_TOKEN/sendMessage`
- **Body:**
```json
{
  "chat_id": "YOUR_CHAT_ID",
  "text": "CALL|missed|%CNUM|%TIMES|0"
}
```

## Step 3: Track Call Duration

For outgoing calls with duration:

1. **Profiles** → **+** → **Event** → **Phone** → **Call Ended**
2. Task: `CHARLIE Call Ended`

### Task Actions

#### Action 1: Variable Set
- **Name:** `%call_duration`
- **To:** `%CSECOND`

#### Action 2: HTTP Request
- **Method:** POST
- **URL:** `https://api.telegram.org/botYOUR_TOKEN/sendMessage`
- **Body:**
```json
{
  "chat_id": "YOUR_CHAT_ID",
  "text": "CALL|outgoing|%CNUM|%TIMES|%call_duration"
}
```

## Step 4: Replace Placeholders

Replace these in all HTTP Request bodies:

| Placeholder | Where to find it |
|-------------|------------------|
| `YOUR_TOKEN` | `.env` → `TELEGRAM_TOKEN` |
| `YOUR_CHAT_ID` | `.env` → `TELEGRAM_CHAT_ID` |

## Step 5: Test

1. Call your phone from another number
2. Check CHARLIE's Telegram — you should see the call event logged
3. Ask CHARLIE: "What calls did I get today?"

## Tasker Profile XML (Auto-Import)

Save this as `charlie-call-tracker.xml` and import into Tasker:

```xml
<TaskerData sr="" dvi="1" tv="6.2.0">
    <Profile sr="prof1" ve="2">
        <cdate>1717000000000</cdate>
        <edate>1717000000000</edate>
        <id>1</id>
        <mid0>1</mid0>
        <nme>CHARLIE Call Tracking</nme>
        <Event sr="con0" ve="2">
            <code>170</code>
            <Str sr="arg0" ve="3"/>
        </Event>
    </Profile>
    <Task sr="task1">
        <cdate>1717000000000</cdate>
        <edate>1717000000000</edate>
        <id>1</id>
        <nme>CHARLIE Call Event</nme>
        <Action sr="act0" ve="7">
            <code>547</code>
            <Str sr="arg0" ve="3">CALL|incoming|%CNUM|%TIMES|0</Str>
            <Str sr="arg1" ve="3"/>
            <Int sr="arg2" val="0"/>
        </Action>
    </Task>
</TaskerData>
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| No calls showing up | Check Tasker has Phone permission |
| Wrong numbers | Ensure `%CNUM` is available in your Android version |
| HTTP errors | Verify token and chat ID are correct |
| Tasker not running | Disable battery optimization for Tasker |

## What CHARLIE Does With Call Data

When CHARLIE receives a call event, it:

1. **Records** the call in `scratch/call_tracker.db`
2. **Analyzes** the caller (history, frequency, patterns)
3. **Responds** with caller intelligence if you ask
4. **Suggests** callback times based on patterns
5. **Alerts** on frequent missed calls from the same number

### Example Interactions

```
You: "Who called me today?"
CHARLIE: "📱 3 calls today:
  • +1234567890 — missed (2x, last at 14:30)
  • +0987654321 — incoming, 5 min duration"

You: "Track +1234567890"
CHARLIE: "📱 Caller Intelligence: +1234567890
  Total calls: 12
  Missed: 8
  Last contact: May 29 at 14:30
  ⚠️ Frequent caller (12 calls)
  🔴 All recent calls were missed — may be urgent
  💡 This number usually calls around 14:00."
```
