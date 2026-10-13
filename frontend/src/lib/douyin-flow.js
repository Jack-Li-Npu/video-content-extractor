const terminal = new Set(['completed', 'failed', 'cancelled']);

export const DEFAULT_DOUYIN_ALLOW_ASR = false;

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
    const setupNeeded = !acquired && ['speech_setup_required', 'speech_runtime_missing'].includes(job?.code);
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
