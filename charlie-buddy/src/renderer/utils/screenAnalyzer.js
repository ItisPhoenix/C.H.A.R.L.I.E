/**
 * Screen analyzer - captures desktop and infers context using simple heuristics.
 * No API calls, no ML models, just pixel analysis.
 */

let analysisInterval = null;
let lastContext = 'general';

export function startScreenAnalysis(onContextChange) {
  if (analysisInterval) return;

  analysisInterval = setInterval(async () => {
    try {
      const context = await analyzeScreen();
      if (context !== lastContext) {
        lastContext = context;
        onContextChange(context);
      }
    } catch (e) {
      console.warn('Screen analysis failed:', e.message);
    }
  }, 10000);

  return () => stopScreenAnalysis();
}

export function stopScreenAnalysis() {
  if (analysisInterval) {
    clearInterval(analysisInterval);
    analysisInterval = null;
  }
}

async function analyzeScreen() {
  // desktopCapturer is only available in Electron main process
  // For renderer, we use a workaround via IPC or skip if not available
  if (!window.electronAPI?.captureScreen) {
    return 'general';
  }

  const imageData = await window.electronAPI.captureScreen();
  if (!imageData) return 'general';

  const canvas = document.createElement('canvas');
  canvas.width = 320;
  canvas.height = 180;
  const ctx = canvas.getContext('2d');

  const img = new Image();
  img.src = imageData;
  await new Promise((resolve) => {
    img.onload = resolve;
    img.onerror = resolve;
  });

  ctx.drawImage(img, 0, 0, 320, 180);
  const pixels = ctx.getImageData(0, 0, 320, 180).data;

  let totalBrightness = 0;
  let textLikePixels = 0;
  const pixelCount = pixels.length / 4;

  for (let i = 0; i < pixels.length; i += 4) {
    const r = pixels[i];
    const g = pixels[i + 1];
    const b = pixels[i + 2];
    const brightness = (r + g + b) / 3;
    totalBrightness += brightness;

    // Text detection: high contrast between adjacent pixels
    if (i > 4) {
      const prevBrightness = (pixels[i - 4] + pixels[i - 3] + pixels[i - 2]) / 3;
      if (Math.abs(brightness - prevBrightness) > 50) textLikePixels++;
    }
  }

  const avgBrightness = totalBrightness / pixelCount;
  const textRatio = textLikePixels / pixelCount;

  return inferContext(avgBrightness, textRatio);
}

function inferContext(brightness, textDensity) {
  if (textDensity > 0.3) return 'coding';   // Lots of text = code
  if (brightness < 30) return 'dark';        // Dark screen = late night
  if (brightness > 180) return 'video';      // Bright = video/streaming
  return 'general';
}