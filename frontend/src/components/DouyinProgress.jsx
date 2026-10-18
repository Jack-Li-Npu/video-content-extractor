import { Check, Circle, ExternalLink } from 'lucide-react';
import { failureGuidance, isRunning, progressSteps } from '../lib/douyin-flow';

export default function DouyinProgress({ job, onShowBrowser, onCancel, busy }) {
  const waiting = job.state === 'waiting_verification';
  const choosing = job.state === 'waiting_track';
  const failed = job.state === 'failed';
  const missingCaptions = failed && job.code === 'captions_not_found';
  return (
    <div className="douyin-progress" aria-label="Douyin extraction progress">
      <h3>{waiting ? 'Verify on Douyin, then return here.' : choosing ? 'Choose the captions to retrieve.' : missingCaptions ? 'No timed caption track was exposed.' : failed ? 'The next step needs attention.' : job.state === 'cancelled' ? 'Extraction cancelled.' : 'Your transcript is on its way.'}</h3>
      <p className={failed ? 'progress-error' : ''} role="status">{waiting ? 'This extraction is paused until you finish the check.' : job.message}</p>
      <ol className="progress-steps">
        {progressSteps(job).map((step, index) => <li key={step.label} className={step.done ? 'is-done' : step.current ? 'is-current' : ''} aria-current={step.current ? 'step' : undefined}>
          <span className="progress-symbol">{step.done ? <Check size={15} /> : step.current ? <Circle size={13} /> : String(index + 1).padStart(2, '0')}</span>
          <span>{step.label}</span>
        </li>)}
      </ol>
      {waiting && <div className="progress-guidance"><p>Complete any requested login or identity check yourself on Douyin’s official page, then click <strong>I’ve verified, continue</strong>. Caption extraction does not require playing the video.</p><button className="button button-outline" disabled={busy} onClick={onShowBrowser}><ExternalLink size={15} />Show Douyin window</button></div>}
      {choosing && <p className="progress-guidance">Select a caption track in the source panel, then click <strong>Use selected captions</strong>. This retrieves the selected timed text.</p>}
      {failed && <p className="progress-guidance">{failureGuidance(job)}</p>}
      {job.diagnostic && <details className="progress-diagnostic"><summary>Error details</summary><p>Code: {job.diagnostic.code}{job.diagnostic.worker_exit_code != null && ` · Worker exit: ${job.diagnostic.worker_exit_code}`}</p></details>}
      {isRunning(job) && <button className="text-button cancel-extraction" disabled={busy} onClick={onCancel}>Cancel extraction</button>}
      <p className="progress-footnote">You can switch to YouTube while this job runs. After a page reload, open the Douyin tab to return to it.</p>
    </div>
  );
}
