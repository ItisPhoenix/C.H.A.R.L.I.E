import React, { useRef, useState, useEffect, useCallback } from 'react';
import { gsap } from 'gsap';

const STATES = {
  idle: { eyeScale: 1, pupilSize: 8, mouthCurve: 'M 80 130 Q 100 140 120 130', bodyY: 0 },
  listening: { eyeScale: 1.2, pupilSize: 10, mouthCurve: 'M 85 130 Q 100 132 115 130', bodyY: -5 },
  thinking: { eyeScale: 0.6, pupilSize: 5, mouthCurve: 'M 90 130 Q 100 128 110 130', bodyY: 0, tilt: 5 },
  speaking: { eyeScale: 1, pupilSize: 8, mouthCurve: 'M 80 130 Q 100 130 120 130', bodyY: 0 },
  happy: { eyeScale: 1.3, pupilSize: 9, mouthCurve: 'M 75 125 Q 100 150 125 125', bodyY: -10, bounce: true },
  curious: { eyeScale: 1.1, pupilSize: 9, mouthCurve: 'M 85 130 Q 100 138 115 130', bodyY: 0, tilt: -8 },
  confused: { eyeScale: 0.8, pupilSize: 6, mouthCurve: 'M 85 132 Q 100 132 115 132', bodyY: 0 },
  sleepy: { eyeScale: 0.4, pupilSize: 4, mouthCurve: 'M 90 130 Q 100 125 110 130', bodyY: 5 },
};

// Bright & saturated per-emotion palette
const PALETTE = {
  idle:     { body: '#06b6d4', face: '#083344', glow: '#22d3ee', antenna: '#06b6d4', feet: '#0e7490' },
  listening:{ body: '#22c55e', face: '#064e3b', glow: '#4ade80', antenna: '#22c55e', feet: '#166534' },
  thinking: { body: '#3b82f6', face: '#1e3a5a', glow: '#60a5fa', antenna: '#3b82f6', feet: '#1e3a8a' },
  speaking: { body: '#f97316', face: '#7c2d12', glow: '#fb923c', antenna: '#f97316', feet: '#9a3412' },
  happy:    { body: '#eab308', face: '#713f12', glow: '#facc15', antenna: '#eab308', feet: '#854d0e' },
  curious:  { body: '#14b8a6', face: '#134e4a', glow: '#2dd4bf', antenna: '#14b8a6', feet: '#0f766e' },
  confused: { body: '#ef4444', face: '#7f1d1d', glow: '#f87171', antenna: '#ef4444', feet: '#991b1b' },
  sleepy:   { body: '#6366f1', face: '#312e81', glow: '#818cf8', antenna: '#818cf8', feet: '#3730a3' },
};

const EMOTION_MAP = {
  energetic: 'happy',
  frustrated: 'confused',
  sad: 'sleepy',
  calm: 'curious',
  neutral: 'idle',
};

const MAX_BUBBLE_WORDS = 30;
const MAX_BUBBLE_LINES = 3;

function SpeechBubble({ text }) {
  if (!text) return null;

  // Word-wrap: show last MAX_BUBBLE_LINES lines
  const words = text.split(/\s+/);
  const trimmed = words.length > MAX_BUBBLE_WORDS ? words.slice(-MAX_BUBBLE_WORDS) : words;
  const lines = [];
  let line = '';
  for (const w of trimmed) {
    const test = line ? `${line} ${w}` : w;
    if (test.length > 35 && line) {
      lines.push(line);
      line = w;
    } else {
      line = test;
    }
  }
  if (line) lines.push(line);
  const visible = lines.slice(-MAX_BUBBLE_LINES);

  return (
    <div style={{
      position: 'absolute',
      top: 5,
      left: '50%',
      transform: 'translateX(-50%)',
      background: '#ffffff',
      color: '#1a202c',
      borderRadius: 12,
      padding: '8px 16px',
      boxShadow: '0 4px 12px rgba(0,0,0,0.25)',
      maxWidth: 260,
      fontSize: 13,
      textAlign: 'center',
      lineHeight: 1.4,
      zIndex: 10,
      pointerEvents: 'none',
      wordBreak: 'break-word',
    }}>
      {visible.join(' ')}
      <div style={{
        position: 'absolute',
        bottom: -8,
        left: '50%',
        transform: 'translateX(-50%)',
        width: 0,
        height: 0,
        borderLeft: '8px solid transparent',
        borderRight: '8px solid transparent',
        borderTop: '8px solid #ffffff',
      }} />
    </div>
  );
}

export default function BuddyRobot({ state = 'idle', mouthValue = 0.0, lastText = '', emotion = 'neutral' }) {
  const svgRef = useRef(null);
  const [pupilOffset, setPupilOffset] = useState({ x: 0, y: 0 });
  const [blinkState, setBlinkState] = useState(false);
  const [idleAnim, setIdleAnim] = useState(null);
  const lastClickTime = useRef(0);
  const [headPose, setHeadPose] = useState({ x: 0, y: 0, tilt: 0, scale: 1 });
  const prevState = useRef(state);
  const stateConfig = STATES[state] || STATES.idle;
  const emotionKey = EMOTION_MAP[emotion] || 'idle';
  const palette = PALETTE[emotionKey] || PALETTE[state] || PALETTE.idle;

  // Mouse tracking for eye follow
  useEffect(() => {
    const handleMouseMove = (e) => {
      if (!svgRef.current) return;
      const rect = svgRef.current.getBoundingClientRect();
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      const angle = Math.atan2(e.clientY - centerY, e.clientX - centerX);
      const distance = Math.min(Math.hypot(e.clientX - centerX, e.clientY - centerY) / 20, 8);

      setPupilOffset({
        x: Math.cos(angle) * distance,
        y: Math.sin(angle) * distance,
      });
    };

    window.addEventListener('mousemove', handleMouseMove);
    return () => window.removeEventListener('mousemove', handleMouseMove);
  }, []);
  // Global shortcut listener (Ctrl+Shift+Space)
  useEffect(() => {
    window.electronAPI?.onToggleExpandKey?.(() => {
      window.electronAPI?.toggleExpand();
    });
  }, []);

  // Custom drag: mousedown starts, mousemove sends IPC
  const handleDragStart = (e) => {
    if (e.button !== 0) return; // left click only
    window.electronAPI?.dragStart(e.screenX, e.screenY);
    const onMove = (ev) => window.electronAPI?.dragMove(ev.screenX, ev.screenY);
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  // EMO-style Head Pose & Idle Animation
  useEffect(() => {
    const timeline = gsap.timeline({ repeat: -1 });
    
    // Clear previous animations
    gsap.killTweensOf(headPose);

    if (state === 'speaking') {
      // Lip-sync micro-nods
      gsap.to(headPose, {
        y: mouthValue * -3,
        tilt: mouthValue * 2,
        duration: 0.1,
        onUpdate: () => setHeadPose({ ...headPose })
      });
    } else {
      // Idle movements based on emotion
      switch (emotion) {
        case 'energetic':
          timeline.to(headPose, { y: -5, scale: 1.05, duration: 0.4, ease: "power1.inOut", onUpdate: () => setHeadPose({ ...headPose }) })
                  .to(headPose, { y: 0, scale: 1, duration: 0.4, ease: "power1.inOut", onUpdate: () => setHeadPose({ ...headPose }) });
          break;
        case 'frustrated':
          timeline.to(headPose, { tilt: 3, x: 2, duration: 0.1, onUpdate: () => setHeadPose({ ...headPose }) })
                  .to(headPose, { tilt: -3, x: -2, duration: 0.1, onUpdate: () => setHeadPose({ ...headPose }) });
          break;
        case 'sad':
          timeline.to(headPose, { y: 5, tilt: 2, duration: 2, ease: "sine.inOut", onUpdate: () => setHeadPose({ ...headPose }) })
                  .to(headPose, { y: 0, tilt: 0, duration: 2, ease: "sine.inOut", onUpdate: () => setHeadPose({ ...headPose }) });
          break;
        case 'calm':
          timeline.to(headPose, { y: -3, tilt: -2, x: 2, duration: 3, ease: "sine.inOut", onUpdate: () => setHeadPose({ ...headPose }) })
                  .to(headPose, { y: 0, tilt: 0, x: 0, duration: 3, ease: "sine.inOut", onUpdate: () => setHeadPose({ ...headPose }) });
          break;
        default:
          timeline.to(headPose, { y: -2, duration: 2, ease: "sine.inOut", onUpdate: () => setHeadPose({ ...headPose }) })
                  .to(headPose, { y: 0, duration: 2, ease: "sine.inOut", onUpdate: () => setHeadPose({ ...headPose }) });
      }
    }

    return () => timeline.kill();
  }, [emotion, state, mouthValue]);

  // Blink timer
  useEffect(() => {
    const blinkInterval = setInterval(() => {
      if (state !== 'sleepy') {
        setBlinkState(true);
        setTimeout(() => setBlinkState(false), 150);
      }
    }, 3000 + Math.random() * 2000);
    return () => clearInterval(blinkInterval);
  }, [state]);

  // Idle behaviors
  useEffect(() => {
    if (state !== 'idle') return;
    const idleTimer = setInterval(() => {
      const r = Math.random();
      if (r < 0.3) {
        setBlinkState(true);
        setTimeout(() => setBlinkState(false), 150);
      } else if (r < 0.5) {
        setIdleAnim('tilt');
        setTimeout(() => setIdleAnim(null), 1000);
      } else if (r < 0.6) {
        setIdleAnim('yawn');
        setTimeout(() => setIdleAnim(null), 1500);
      } else if (r < 0.7) {
        setIdleAnim('stretch');
        setTimeout(() => setIdleAnim(null), 1200);
      } else if (r < 0.8) {
        setIdleAnim('lookAround');
        setTimeout(() => setIdleAnim(null), 2000);
      }
    }, 3000);
    return () => clearInterval(idleTimer);
  }, [state]);

  // GSAP morph + pulse on state change
  useEffect(() => {
    if (!svgRef.current) return;
    const body = svgRef.current.querySelector('.buddy-body');
    if (!body) return;

    // Morph: smooth transition to new position/rotation
    gsap.to(body, {
      y: stateConfig.bodyY,
      rotation: stateConfig.tilt || 0,
      duration: 0.4,
      ease: 'power2.inOut',
    });

    // Pulse: brief scale bounce on state change
    if (prevState.current !== state) {
      gsap.fromTo(body,
        { scale: 1 },
        { scale: 1.05, duration: 0.15, ease: 'power2.out', yoyo: true, repeat: 1 }
      );
      prevState.current = state;
    }

    if (stateConfig.bounce) {
      gsap.to(body, {
        y: -20,
        duration: 0.3,
        ease: 'power2.out',
        yoyo: true,
        repeat: 2,
      });
    }
  }, [state, stateConfig]);


  // Mouth path for lip sync
  const mouthPath = state === 'speaking'
    ? `M ${80 + mouthValue * 5} 130 Q 100 ${130 + mouthValue * 15} ${120 - mouthValue * 5} 130`
    : stateConfig.mouthCurve;

  // Eye shape based on state
  const eyeHeight = stateConfig.eyeScale * 20;
  const eyeWidth = stateConfig.eyeScale * 14;
  const pupilR = stateConfig.pupilSize;

  // Sleepy droop effect
  const eyeClip = state === 'sleepy' ? 'url(#sleepyClip)' : undefined;

  // Idle animation modifiers
  const idleScale = idleAnim === 'stretch' ? 1.05 : 1;
  const idleRotate = idleAnim === 'tilt' ? 3 : 0;

  return (
    <div
      style={{
        position: 'relative',
        width: '100%',
        height: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
      onMouseEnter={() => window.electronAPI?.setIgnoreMouseEvents(false)}
      onMouseLeave={() => window.electronAPI?.setIgnoreMouseEvents(true)}
    >
      {/* Speech bubble above buddy */}
      <SpeechBubble text={lastText} />
        <svg
          ref={svgRef}
          viewBox="0 0 200 200"
          preserveAspectRatio="xMidYMid meet"
          style={{ width: 200, height: 200, overflow: 'visible', cursor: 'grab' }}
          onMouseDown={handleDragStart}
          onDoubleClick={() => window.electronAPI?.toggleExpand()}
        >
          <defs>
            <filter id="glow" x="-30%" y="-30%" width="160%" height="160%">
              <feGaussianBlur stdDeviation="4" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
              <feDropShadow dx="0" dy="4" stdDeviation="6" floodColor="#000" floodOpacity="0.3" />
            </filter>
            <clipPath id="sleepyClip">
              <rect x="0" y="0" width="200" height="145" />
            </clipPath>
          </defs>

          <g
            className="buddy-body"
            filter="url(#shadow)"
            transform={`translate(${headPose.x}, ${headPose.y + (stateConfig.bodyY || 0)}) rotate(${headPose.tilt + (stateConfig.tilt || 0)} 100 100) scale(${headPose.scale})`}
          >
            {/* Body — dynamic fill */}
            <rect x="55" y="60" width="90" height="110" rx="20" ry="20" fill={palette.body} />

            {/* Face screen — dynamic fill */}
            <rect x="65" y="70" width="70" height="60" rx="10" ry="10" fill={palette.face} />

            {/* Left eye */}
            <g clipPath={eyeClip}>
              <ellipse
                cx="85"
                cy="100"
                rx={eyeWidth / 2}
                ry={blinkState ? 1 : eyeHeight / 2}
                fill="white"
              />
              {!blinkState && (
                <circle
                  cx={85 + pupilOffset.x}
                  cy={100 + pupilOffset.y}
                  r={pupilR}
                  fill="#1a1a2e"
                />
              )}
            </g>

            {/* Right eye */}
            <g clipPath={eyeClip}>
              <ellipse
                cx="115"
                cy="100"
                rx={eyeWidth / 2}
                ry={blinkState ? 1 : eyeHeight / 2}
                fill="white"
              />
              {!blinkState && (
                <circle
                  cx={115 + pupilOffset.x}
                  cy={100 + pupilOffset.y}
                  r={pupilR}
                  fill="#1a1a2e"
                />
              )}
            </g>

            {/* Mouth */}
            <path d={mouthPath} stroke={palette.glow} strokeWidth="2" fill="none" />

            {/* Antenna — dynamic glow */}
            <line x1="100" y1="60" x2="100" y2="40" stroke={palette.glow} strokeWidth="3" />
            <circle
              cx="100"
              cy="35"
              r="6"
              fill={palette.antenna}
              filter="url(#glow)"
            />

            {/* State-specific elements */}
            {state === 'thinking' && (
              <g>
                <circle cx="155" cy="55" r="8" fill="white" opacity="0.8" />
                <circle cx="165" cy="40" r="5" fill="white" opacity="0.6" />
                <circle cx="172" cy="28" r="3" fill="white" opacity="0.4" />
              </g>
            )}

            {state === 'happy' && (
              <g>
                <text x="155" y="50" fontSize="14" fill="#ffd700">✨</text>
              </g>
            )}

            {state === 'curious' && (
              <text x="150" y="55" fontSize="18" fill={palette.glow} fontWeight="bold">?</text>
            )}

            {state === 'confused' && (
              <text x="150" y="55" fontSize="18" fill={palette.glow} fontWeight="bold">?!</text>
            )}

            {state === 'sleepy' && (
              <g>
                <text x="145" y="50" fontSize="12" fill={palette.glow} opacity="0.8">z</text>
                <text x="155" y="40" fontSize="14" fill={palette.glow} opacity="0.6">z</text>
                <text x="162" y="28" fontSize="16" fill={palette.glow} opacity="0.4">z</text>
              </g>
            )}

            {/* Feet — dynamic fill */}
            <rect x="65" y="165" width="25" height="10" rx="5" fill={palette.feet} />
            <rect x="110" y="165" width="25" height="10" rx="5" fill={palette.feet} />
          </g>
        </svg>
    </div>
  );
}
