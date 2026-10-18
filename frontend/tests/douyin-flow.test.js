import test from 'node:test';
import assert from 'node:assert/strict';
import { DEFAULT_DOUYIN_ALLOW_ASR, failureGuidance, isRunning, primaryAction, progressSteps, speechSetupGuidance } from '../src/lib/douyin-flow.js';

test('Douyin starts in captions-only mode without consenting to a video download', () => {
  assert.equal(DEFAULT_DOUYIN_ALLOW_ASR, false);
});

test('the same primary action resumes after manual verification, then becomes busy', () => {
  assert.equal(primaryAction(null).action, 'start');
  assert.deepEqual(primaryAction({ state: 'waiting_verification' }), { action: 'confirm', label: 'I’ve verified, continue', enabled: true });
  for (const state of ['opening_browser', 'reading_video', 'probing_captions', 'downloading_captions', 'downloading_media', 'transcribing']) {
    assert.equal(primaryAction({ state }).enabled, false);
    assert.equal(isRunning({ state }), true);
  }
});

test('multiple caption tracks require an explicit selection without another login confirmation', () => {
  const job = { state: 'waiting_track' };
  assert.deepEqual(primaryAction(job), { action: 'confirm', label: 'Use selected captions', enabled: false });
  assert.deepEqual(primaryAction(job, 'platform:zh'), { action: 'confirm', label: 'Use selected captions', enabled: true });
  assert.equal(isRunning(job), true);
});

test('a failed speech job retries without starting acquisition again', () => {
  assert.equal(primaryAction({ state: 'failed', can_retry_speech: true }).action, 'retry-speech');
  assert.equal(primaryAction({ state: 'failed', can_retry_speech: false }).action, 'start');
  for (const state of ['completed', 'failed', 'cancelled']) assert.equal(isRunning({ state }), false);
  const steps = progressSteps({ state: 'failed', can_retry_speech: true, video: {} });
  assert.deepEqual(steps.map((step) => step.done), [true, true, true, false, false]);
  assert.equal(steps[3].current, true);
});

test('caption-only completion has no media or speech stage and marks all stages done', () => {
  const steps = progressSteps({ state: 'completed', video: {}, result: { source: 'platform' } });
  assert.ok(steps.every((step) => step.done));
  assert.equal(steps[2].label, 'Retrieve selected caption text');
  assert.ok(steps.every((step) => !/video download|speech/i.test(step.label)));
});

test('caption discovery and timed-text retrieval are separate progress stages', () => {
  const probing = progressSteps({ state: 'probing_captions', video: { tracks: [] }, allow_asr: false });
  assert.equal(probing[1].current, true);
  assert.equal(probing[2].current, false);
  const fetching = progressSteps({ state: 'downloading_captions', video: { tracks: [{ id: 'platform:zh' }] } });
  assert.equal(fetching[1].done, true);
  assert.equal(fetching[2].current, true);
});

test('no exposed captions never implies that a video was downloaded or speech was attempted', () => {
  const steps = progressSteps({ state: 'failed', code: 'captions_not_found', captions_only: true, caption_probe_complete: true, video: { tracks: [] } });
  assert.equal(steps[1].done, true);
  assert.equal(steps[2].current, true);
  assert.equal(steps[2].label, 'No timed caption track exposed');
  assert.ok(steps.every((step) => !/download|speech/i.test(step.label)));
  assert.equal(primaryAction({ state: 'failed', code: 'captions_not_found' }).action, 'start');
});

test('media download progress appears only when the speech fallback actually runs', () => {
  const steps = progressSteps({ state: 'downloading_media', allow_asr: true, video: { tracks: [] } });
  assert.equal(steps[1].done, true);
  assert.equal(steps[2].label, 'Download video for local speech recognition');
  assert.equal(steps[2].current, true);
  assert.equal(steps[3].current, false);
});

test('missing speech setup after caption discovery does not claim a media download', () => {
  const steps = progressSteps({ state: 'failed', code: 'speech_setup_required', allow_asr: true,
    caption_probe_complete: true, video: { tracks: [] } });
  assert.equal(steps[1].done, true);
  assert.equal(steps[2].label, 'Prepare local speech setup');
  assert.equal(steps[2].current, true);
  assert.equal(steps[2].done, false);
});

test('speech setup instructions follow the server platform without enabling a download', () => {
  const windows = speechSetupGuidance({ platform: 'windows', speech: {
    available: false, message: 'Install the local Windows runtime.', setup_hint: 'Run setup-douyin.ps1',
  } });
  assert.equal(windows.available, false);
  assert.equal(windows.setupHint, 'Run setup-douyin.ps1');
  assert.doesNotMatch(windows.message, /Apple|Mac/);
  const ready = speechSetupGuidance({ speech: { available: true, message: 'Local speech is ready.' } });
  assert.equal(ready.available, true);
  assert.equal(DEFAULT_DOUYIN_ALLOW_ASR, false);
  assert.match(speechSetupGuidance(null).setupHint, /Windows.*Apple Silicon/);
});

test('missing captions and missing speech setup explain browser closure without implying login failure', () => {
  assert.match(failureGuidance({ code: 'captions_not_found' }), /not a login error/);
  for (const code of ['speech_setup_required', 'speech_runtime_missing', 'model_missing']) {
    const explanation = failureGuidance({ code });
    assert.match(explanation, /setup is incomplete/);
    assert.match(explanation, /does not mean login failed/);
  }
  assert.match(failureGuidance({ can_retry_speech: true, code: 'model_missing' }), /no new login or download/);
});
