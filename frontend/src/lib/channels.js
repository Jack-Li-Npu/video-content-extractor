import { isRunning } from './douyin-flow.js';

export const CHANNELS = {
  youtube: {
    name: 'YouTube',
    summary: 'Existing captions',
    heading: 'YouTube captions',
    description: 'Read the captions already available on a video.',
    placeholder: 'https://www.youtube.com/watch?v=…',
    urlHint: 'Watch, share, Shorts, and embed links are supported.',
  },
  douyin: {
    name: 'Douyin',
    summary: 'Timed captions + manual verification',
    heading: 'Douyin captions',
    description: 'Read available timed captions through your local Douyin session.',
    placeholder: 'https://www.douyin.com/video/…',
    urlHint: 'Video, jingxuan, and v.douyin.com share links are supported.',
  },
};

export function channelMismatch(url, platform) {
  let host;
  try { host = new URL(url.trim()).hostname.toLowerCase(); } catch { return ''; }
  const target = ['douyin.com', 'www.douyin.com', 'v.douyin.com'].includes(host) ? 'douyin'
    : ['youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be', 'www.youtu.be', 'youtube-nocookie.com', 'www.youtube-nocookie.com'].includes(host) ? 'youtube' : null;
  return target && target !== platform ? `This is a ${CHANNELS[target].name} link. Use the ${CHANNELS[target].name} tab above.` : '';
}

export function channelStatus({ job, loading, result, error }) {
  if (job?.state === 'waiting_verification') return 'Needs verification';
  if (job?.state === 'waiting_track') return 'Choose captions';
  if (loading || isRunning(job)) return 'Extracting';
  if (error || job?.state === 'failed') return 'Needs attention';
  if (result) return 'Ready';
  return '';
}
