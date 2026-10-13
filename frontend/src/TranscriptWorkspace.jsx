import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDownToLine, ArrowRight, Captions, Check, CheckCheck, Clock3, Copy, ExternalLink, Link2, Plus, Search, X } from 'lucide-react';
import TranscriptDisplay from './components/TranscriptDisplay';
import SkeletonLoader from './components/SkeletonLoader';
import EmptyState from './components/EmptyState';
import ErrorToast from './components/ErrorToast';
import CodexUsage from './components/CodexUsage';
import DouyinProgress from './components/DouyinProgress';
import { DEFAULT_DOUYIN_ALLOW_ASR, isRunning, primaryAction } from './lib/douyin-flow';
import { channelMismatch, channelStatus, CHANNELS } from './lib/channels';
import { buildCodexHandoff, CODEX_PROMPT, downloadExport, formatTime, safeFilename, timestampLink } from './lib/transcript';

const API_BASE_URL = import.meta.env.DEV ? (import.meta.env.VITE_API_URL || '') : '';
const EXAMPLE_URL = 'https://www.youtube.com/watch?v=Hrbq66XqtCo';

async function post(path, body) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: AbortSignal.timeout(120000),
    });
  } catch {
    throw new Error('Could not reach the local extractor. Check that start.command is running, then retry.');
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || 'The request failed. Check the video link and retry.');
  return data;
}

function VideoInfo({ data }) {
  const [imgSrc, setImgSrc] = useState(`https://img.youtube.com/vi/${data.video_id}/maxresdefault.jpg`);
  const douyin = data.platform === 'douyin';
  return (
    <section className={`video-info ${douyin ? 'douyin-video-info' : ''}`} aria-label="Video details">
      {!douyin && <a className="video-thumbnail" href={`https://www.youtube.com/watch?v=${data.video_id}`} target="_blank" rel="noopener noreferrer" aria-label="Watch this video on YouTube">
        <img src={imgSrc} alt={data.title} onError={() => setImgSrc(`https://img.youtube.com/vi/${data.video_id}/hqdefault.jpg`)} />
        {data.duration > 0 && <span className="video-duration">{formatTime(data.duration)}</span>}
        <span className="video-open"><ExternalLink size={15} /> Open video</span>
      </a>}
      <p className="video-label">Source video</p>
      <h2 title={data.title}>{data.title}</h2>
      <p className="video-channel">{data.channel}</p>
      {douyin && <a className="text-button" href={`https://www.douyin.com/video/${data.video_id}`} target="_blank" rel="noopener noreferrer">Open on Douyin <ExternalLink size={15} /></a>}
    </section>
  );
}

function CodexHandoff({ data, active, onError }) {
  const [copied, setCopied] = useState(false);
  async function copyAll() {
    try {
      const item = buildCodexHandoff(data);
      await navigator.clipboard.writeText(item.content);
      setCopied(true);
      onError('');
    } catch {
      setCopied(false);
      onError('Could not copy the transcript. Download it and attach the file in Codex.');
    }
  }
  function download() {
    try {
      downloadExport(buildCodexHandoff(data), safeFilename(data));
    } catch (failure) {
      onError(failure.message);
    }
  }
  return (
    <div className="codex-handoff" aria-label="Take the transcript to Codex">
      <div className="handoff-heading"><div><h4>Ready for Codex</h4><p>Full transcript, timestamps, and a ready-to-use prompt.</p></div><span className="file-type">.MD</span></div>
      <CodexUsage data={data} enabled={active} />
      <div className="export-toolbar">
        <button className="button button-dark" onClick={download}><ArrowDownToLine size={16} />Download for Codex</button>
        <button className="button button-quiet" onClick={copyAll}>
          {copied ? <CheckCheck size={16} /> : <Copy size={16} />}
          {copied ? 'Transcript & prompt copied' : 'Copy for Codex'}
        </button>
      </div>
      <p className="handoff-instruction">Copy and paste into Codex, or attach the downloaded file and ask Codex to follow its opening instructions.</p>
      <p className="handoff-note">The prompt guides Codex to illustrate the takeaways, examine important claims and plausible alternatives, and link to the passages you can judge for yourself. It includes skill setup if needed.</p>
      <details className="handoff-details">
        <summary>View the included prompt</summary>
        <p className="handoff-prompt">{CODEX_PROMPT}</p>
      </details>
    </div>
  );
}

// Each mounted workspace owns its draft, result and request lifecycle.
// Hiding a tab must not clear its data or stop an active Douyin job.
export default function TranscriptWorkspace({ platform, active, onStatus }) {
  const douyin = platform === 'douyin';
  const channel = CHANNELS[platform];
  const [url, setUrl] = useState('');
  const [video, setVideo] = useState(null);
  const [trackId, setTrackId] = useState('');
  const [result, setResult] = useState(null);
  const [stage, setStage] = useState('');
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [job, setJob] = useState(null);
  const [allowAsr, setAllowAsr] = useState(DEFAULT_DOUYIN_ALLOW_ASR);
  const [confirming, setConfirming] = useState(false);
  const urlInput = useRef(null);
  const touched = useRef(false);
  const loading = Boolean(stage);
  const visibleLines = useMemo(() => {
    const lines = result?.transcript_lines || [];
    const query = search.trim().toLocaleLowerCase();
    return query ? lines.filter((line) => line.text.toLocaleLowerCase().includes(query)) : lines;
  }, [result, search]);
  const mismatch = channelMismatch(url, platform);
  const status = channelStatus({ job, loading, result, error });
  useEffect(() => { onStatus(platform, status); }, [platform, status, onStatus]);
  const jobId = job?.id;
  const polling = isRunning(job);
  const action = primaryAction(job, trackId);
  const applyJob = useCallback((current) => {
    setJob(current);
    if (typeof current.allow_asr === 'boolean') setAllowAsr(current.allow_asr);
    if (current.video) setVideo(current.video);
    if (current.state === 'waiting_track') {
      setTrackId((selected) => current.video?.tracks?.some((track) => track.id === selected) ? selected : '');
    }
    if (current.state === 'completed') setResult(current.result);
    setStage(isRunning(current) ? current.message : '');
    setError('');
  }, []);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    if (!douyin) return;
    fetch(`${API_BASE_URL}/api/douyin/current-job`, { signal: controller.signal })
      .then((response) => response.ok ? response.json() : null)
      .then((data) => {
        if (active && !touched.current && data?.job) {
          setUrl(data.job.url);
          applyJob(data.job);
        }
      }).catch(() => { /* A fresh extraction can still be started after a connection recovers. */ });
    return () => { active = false; controller.abort(); };
  }, [applyJob, douyin]);

  useEffect(() => {
    if (!jobId || !polling) return;
    let active = true;
    let timer;
    let controller;
    async function poll() {
      controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 12000);
      try {
        const response = await fetch(`${API_BASE_URL}/api/douyin/jobs/${jobId}`, { signal: controller.signal });
        const current = await response.json();
        if (response.status === 404) {
          if (active) { setJob(null); setStage(''); setError('The local extractor restarted or this job ended. Click Extract transcript to start again.'); }
          return;
        }
        if (!response.ok) throw new Error(current.message || 'Could not read the extraction status.');
        if (!active) return;
        applyJob(current);
        if (!isRunning(current)) return;
      } catch (failure) {
        if (!active) return;
        setError(failure.name === 'AbortError' ? 'The local extractor is not responding. Check start.command; status will retry.' : failure.message);
      } finally { clearTimeout(timeout); }
      if (active) timer = setTimeout(poll, 1500);
    }
    poll();
    return () => { active = false; clearTimeout(timer); controller?.abort(); };
  }, [jobId, polling, applyJob]);

  function changeUrl(value) {
    touched.current = true;
    setStage('');
    setUrl(value);
    setVideo(null);
    setResult(null);
    setTrackId('');
    setError('');
    setSearch('');
    setJob(null);
  }

  function startNew(value = '') {
    changeUrl(value);
    urlInput.current?.focus();
  }

  async function handleExtract(event) {
    event.preventDefault();
    touched.current = true;
    if (mismatch) { setError(mismatch); return; }
    if (douyin && !action.enabled) return;
    if (douyin && action.action === 'confirm') { await confirmDouyin(); return; }
    if (douyin && action.action === 'retry-speech') {
      setConfirming(true);
      setError('');
      try { applyJob(await post(`/api/douyin/jobs/${job.id}/retry-speech`, {})); }
      catch (failure) { setError(failure.message); }
      finally { setConfirming(false); }
      return;
    }
    if (loading || confirming) return;
    setError('');
    setResult(null);
    setSearch('');
    if (douyin) {
      setJob(null);
      setStage('Opening your Douyin session…');
      try { applyJob(await post('/api/douyin/jobs', { url: url.trim(), allow_asr: allowAsr })); }
      catch (failure) { setError(failure.message); setStage(''); }
      return;
    }
    try {
      let selected = trackId;
      let current = video;
      if (!current) {
        setStage('Checking available captions…');
        current = await post('/api/video-info', { url: url.trim() });
        setVideo(current);
        selected = current.recommended_track_id || '';
        setTrackId(selected);
      }
      if (!current.tracks.length) throw new Error('This video has no retrievable captions. Try a video with subtitles.');
      if (!selected) return;
      setStage('Retrieving the full transcript…');
      const transcript = await post('/api/extract', { url: current.video_id, track_id: selected });
      setResult({ ...transcript, channel: current.channel, duration: current.duration });
    } catch (failure) {
      setError(failure.message);
    } finally {
      setStage('');
    }
  }

  async function confirmDouyin() {
    setConfirming(true);
    setError('');
    try { applyJob(await post(`/api/douyin/jobs/${job.id}/confirm`, { track_id: trackId })); }
    catch (failure) { setError(failure.message); }
    finally { setConfirming(false); }
  }

  async function showDouyin() {
    setConfirming(true);
    try { await post(`/api/douyin/jobs/${job.id}/show-browser`, {}); }
    catch (failure) { setError(failure.message); }
    finally { setConfirming(false); }
  }

  async function cancelDouyin() {
    setConfirming(true);
    try { await post(`/api/douyin/jobs/${job.id}/cancel`, {}); }
    catch (failure) { setError(failure.message); }
    finally { setConfirming(false); }
  }

  return (
    <div className="workspace-grid">
      <aside className="source-panel" aria-label="Video source">
        <div className="source-heading"><h2>{channel.heading}</h2><button className="icon-button" aria-label={`New ${channel.name} transcript`} title="New transcript" onClick={() => startNew()} disabled={loading || confirming}><Plus size={18} /></button></div>
        <p className="channel-description">{channel.description}</p>
        <form onSubmit={handleExtract} aria-label={`${channel.name} transcript extraction`}>
          <label htmlFor={`${platform}-url`}>{channel.name} video URL</label>
          <div className="url-input">
            <Link2 size={17} aria-hidden="true" />
            <input id={`${platform}-url`} ref={urlInput} required disabled={loading || confirming} value={url} onChange={(event) => changeUrl(event.target.value)} placeholder={channel.placeholder} spellCheck={false} autoComplete="off" aria-describedby={`${platform}-url-hint`} aria-invalid={Boolean(mismatch)} />
          </div>
          <p id={`${platform}-url-hint`} className={`field-hint ${mismatch ? 'accent-text' : ''}`}>{mismatch || channel.urlHint}</p>
          {douyin && <div className="douyin-options">
            <p className="caption-mode-note"><strong>Captions only by default.</strong> Retrieve exposed timed text without downloading audio, video, or speech models. Some videos have no usable caption track.</p>
            <div className="verification-note"><ExternalLink size={17} aria-hidden="true" /><div><h3>A separate Douyin window</h3><p>If Douyin asks you to log in or verify, complete it there. Return here and click <strong>I’ve verified, continue</strong>.</p><p>A working saved session continues automatically.</p></div></div>
            <label className="checkbox-label"><input type="checkbox" checked={allowAsr} disabled={loading || confirming} onChange={(event) => setAllowAsr(event.target.checked)} /><span>Allow video download for local speech recognition<span className="checkbox-hint">Optional fallback when no timed captions are exposed. Downloads the source video and uses a local speech model.</span></span></label>
            {allowAsr && <details className="speech-setup"><summary>One-time speech setup</summary><p>Apple Silicon Mac and FFmpeg required. Run <code>./setup-douyin.sh</code> once to install the local speech environment and about 1.6 GB of model weights. Speech processing temporarily saves this video on your Mac.</p><a href="https://github.com/Jack-Li-Npu/video-content-extractor/blob/main/docs/DOUYIN.md" target="_blank" rel="noopener noreferrer">Open setup guide <ExternalLink size={13} /></a></details>}
          </div>}
          {video?.tracks.length > 0 && (!douyin || job?.state === 'waiting_track') && (
            <div className="track-field">
              <label htmlFor={`${platform}-caption-track`}>Caption track</label>
              <select id={`${platform}-caption-track`} value={trackId} disabled={confirming || (loading && job?.state !== 'waiting_track')} onChange={(event) => { setTrackId(event.target.value); setResult(null); setError(''); setSearch(''); }}>
                <option value="" disabled>Choose a caption language</option>
                {video.tracks.map((track) => <option key={track.id} value={track.id}>{track.name} ({track.language}) · {track.source === 'platform' ? 'Platform captions' : track.source === 'uploaded' ? 'Uploaded' : 'Automatic'}</option>)}
              </select>
              {!trackId && <p className="field-hint accent-text">{douyin ? 'Choose a caption track, then click Use selected captions.' : 'Choose a caption track, then get the transcript.'}</p>}
            </div>
          )}
          <button disabled={!url.trim() || Boolean(mismatch) || confirming || (douyin ? !action.enabled || (loading && !job) : loading)} type="submit" className="button button-primary extract-button">
            {confirming ? 'Continuing…' : douyin ? action.label : loading ? 'Working…' : 'Get transcript'}<ArrowRight size={17} />
          </button>
          <ErrorToast error={error} onClose={() => setError('')} />
        </form>
        {video && <VideoInfo key={video.video_id} data={video} />}
        <p className="source-footnote">{douyin ? 'Your login stays in the local Douyin browser profile. Starting another Douyin job replaces the previous result and any temporary video.' : 'Existing captions only. No video downloads, speech models, or separate login window.'}</p>
      </aside>

      <section className={`transcript-panel ${result && !loading ? 'has-transcript' : ''}`} aria-label="Full transcript" aria-busy={loading && !['waiting_verification', 'waiting_track'].includes(job?.state)}>
        <div className="panel-header">
          <h2>Transcript</h2>
          {result && !loading ? <span className="ready-label"><Check size={13} />Ready for Codex</span> : <span className="panel-meta">{channel.name}</span>}
        </div>
        {douyin && job && !result ? <DouyinProgress job={job} onShowBrowser={showDouyin} onCancel={cancelDouyin} busy={confirming} /> : loading ? <SkeletonLoader stage={stage} /> : result ? (
          <>
            <div className="transcript-summary">
              <div><h3>{result.title}</h3>
                <p className="transcript-meta"><span>{result.transcript_lines.length.toLocaleString()} segments</span><span className="language-label">{result.language}</span></p>
              </div>
              {result.source === 'asr' && <p className="speech-notice">Locally generated speech captions. Wording and timing are estimates; verify names and flagged passages against playback. On-screen text is not included.</p>}
              {douyin && result.media_downloaded === false && <p className="caption-mode-note result-caption-note">Retrieved Douyin timed captions. No audio, video, or speech model was downloaded. Timestamps preserve the caption track’s timing; Douyin public links do not provide reliable exact seeking.</p>}
              {result.local_viewer_url && result.media_downloaded !== false && <p className="field-hint">Click any timestamp to watch the local video. Keep this extractor running while Codex reviews it.</p>}
              <CodexHandoff key={`${result.track_id}-${result.generated_at}`} data={result} active={active} onError={setError} />
            </div>
            <div className="reader-toolbar">
              <div className="search-field"><Search size={17} aria-hidden="true" /><input aria-label="Search transcript" placeholder="Find a word or phrase…" value={search} onChange={(event) => setSearch(event.target.value)} />
                {search && <button className="icon-button" aria-label="Clear search" onClick={() => setSearch('')}><X size={15} /></button>}
              </div>
              <span className="timestamp-label"><Clock3 size={14} />Timestamps included</span>
            </div>
            {search && <p className="search-status" role="status">{visibleLines.length.toLocaleString()} {visibleLines.length === 1 ? 'match' : 'matches'}<span>Copy and downloads include the full transcript.</span></p>}
            <TranscriptDisplay lines={visibleLines} search={search} linkForTime={(seconds) => timestampLink(result, seconds)} />
            <div className="reader-footer"><span>{result.source === 'asr' ? 'Local speech recognition' : result.source === 'platform' ? 'Douyin caption track' : result.source === 'uploaded' ? 'Uploaded captions' : 'YouTube automatic captions'}</span><span>{result.source === 'asr' ? 'Estimated wording and timing.' : 'Original wording, every segment.'}</span></div>
          </>
        ) : <EmptyState platform={platform} hasVideo={Boolean(video)} onExample={() => startNew(EXAMPLE_URL)} />}
      </section>
    </div>
  );
}
