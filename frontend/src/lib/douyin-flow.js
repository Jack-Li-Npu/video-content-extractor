const terminal = new Set(['completed', 'failed', 'cancelled']);

export const DEFAULT_DOUYIN_ALLOW_ASR = false;

const speechSetupCodes = new Set(['speech_setup_required', 'speech_runtime_missing', 'model_missing']);

export function speechSetupGuidance(capabilities) {
  const speech = capabilities?.speech;
  if (!speech) return {
    available: false,
    message: 'Local speech recognition requires a separate one-time setup. This does not affect existing captions.',
    setupHint: 'On Windows, run setup-douyin.ps1. On Apple Silicon Mac, run ./setup-douyin.sh.',
  };
  return {
    available: speech.available === true,
    message: typeof speech.message === 'string' ? speech.message : 'Check the local speech setup before enabling this fallback.',
    setupHint: typeof speech.setup_hint === 'string' ? speech.setup_hint : 'Open the setup guide for your platform.',
  };
}

export function failureGuidance(job) {
  if (job.can_retry_speech) return 'Click Retry speech recognition. The acquired video is kept, so no new login or download is needed.';
  if (job.code === 'captions_not_found') return 'No audio, video, or speech model was downloaded. This is a missing-caption result, not a login error. Try another video, or prepare local speech recognition and enable its video-download fallback before retrying.';
  if (speechSetupCodes.has(job.code)) return 'Caption checking finished, but the local speech setup is incomplete. Complete the setup shown in the source panel, restart the extractor, then retry. The browser closing at this point does not mean login failed.';
  return 'Follow the instruction above, then click Retry extraction. The separate browser closes when the job ends.';
}

export function isRunning(job) {
  return Boolean(job && !terminal.has(job.state));
}

export function primaryAction(job, selectedTrackId = '') {
  if (job?.state === 'waiting_verification') return { action: 'confirm', label: 'I’ve verified, continue', enabled: true };
  if (job?.state === 'waiting_track') return { action: 'confirm', label: 'Use selected captions', enabled: Boolean(selectedTrackId) };
  if (job?.state === 'failed') return job.can_retry_speech
    ? { action: 'retry-speech', label: 'Retry speech recognition', enabled: true }
    : { action: 'start', label: 'Retry extraction', enabled: true };
  if (isRunning(job)) return { action: 'wait', label: 'Extracting…', enabled: false };
  return { action: 'start', label: 'Extract transcript', enabled: true };
}

export function progressSteps(job) {
  const state = job?.state;
  const access = Boolean(job?.video);
  const completed = state === 'completed';
  const tracksFound = Boolean(job?.video?.tracks?.length || ['waiting_track', 'downloading_captions', 'completed'].includes(state) && job?.result?.source !== 'asr');
  const probeComplete = Boolean(job?.caption_probe_complete || tracksFound);
  const speech = Boolean(job?.can_retry_speech || ['downloading_media', 'transcribing'].includes(state) || job?.result?.source === 'asr'
    || (state === 'failed' && job.allow_asr && probeComplete && !tracksFound));
  if (speech) {
    const acquired = Boolean(job?.can_retry_speech || ['transcribing', 'completed'].includes(state));
    const setupNeeded = !acquired && speechSetupCodes.has(job?.code);
    return [
      { label: 'Access this video', done: access, current: !access },
      { label: 'Check for existing timed captions', done: true, current: false },
      { label: setupNeeded ? 'Prepare local speech setup' : 'Download video for local speech recognition', done: acquired, current: !acquired },
      { label: 'Generate speech captions', done: completed, current: acquired && !completed },
      { label: 'Prepare transcript for Codex', done: completed, current: false },
    ];
  }
  return [
    { label: 'Access this video', done: access, current: !access },
    { label: 'Check for available timed captions', done: probeComplete, current: access && !probeComplete },
    { label: probeComplete && !tracksFound ? 'No timed caption track exposed' : 'Retrieve selected caption text', done: completed, current: probeComplete && !completed },
    { label: 'Prepare transcript for Codex', done: completed, current: false },
  ];
}
