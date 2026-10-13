import test from 'node:test';
import assert from 'node:assert/strict';
import { channelMismatch, channelStatus } from '../src/lib/channels.js';

test('YouTube links cannot start a Douyin job, across supported URL shapes', () => {
  for (const url of [
    'https://www.youtube.com/watch?v=Hrbq66XqtCo',
    'https://youtu.be/Hrbq66XqtCo?t=30',
    'https://m.youtube.com/shorts/Hrbq66XqtCo',
    'https://www.youtube-nocookie.com/embed/Hrbq66XqtCo',
  ]) {
    assert.equal(channelMismatch(url, 'youtube'), '');
    assert.match(channelMismatch(url, 'douyin'), /Use the YouTube tab/);
  }
});

test('Douyin video, jingxuan and share links stay in the Douyin channel', () => {
  for (const url of [
    'https://www.douyin.com/video/7685972770793999667',
    'https://www.douyin.com/jingxuan?modal_id=7685972770793999667',
    'https://v.douyin.com/example/',
  ]) {
    assert.equal(channelMismatch(url, 'douyin'), '');
    assert.match(channelMismatch(url, 'youtube'), /Use the Douyin tab/);
  }
});

test('incomplete and unknown links are left to backend validation', () => {
  for (const url of ['', 'https://', 'not a link', 'https://douyin.com.example.net/video/123']) {
    assert.equal(channelMismatch(url, 'youtube'), '');
  }
});

test('background channel status distinguishes manual action, extraction, failure and completion', () => {
  assert.equal(channelStatus({}), '');
  assert.equal(channelStatus({ loading: true }), 'Extracting');
  assert.equal(channelStatus({ job: { state: 'waiting_verification' }, loading: true }), 'Needs verification');
  assert.equal(channelStatus({ job: { state: 'waiting_track' }, loading: true }), 'Choose captions');
  assert.equal(channelStatus({ job: { state: 'transcribing' } }), 'Extracting');
  assert.equal(channelStatus({ job: { state: 'failed' } }), 'Needs attention');
  assert.equal(channelStatus({ error: 'Blocked' }), 'Needs attention');
  assert.equal(channelStatus({ result: { transcript_lines: [] } }), 'Ready');
  assert.equal(channelStatus({ job: { state: 'cancelled' } }), '');
});
