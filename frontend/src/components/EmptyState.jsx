import { ArrowUpRight, Captions, Clock3, FileText, Monitor } from 'lucide-react';

export default function EmptyState({ platform, hasVideo, onExample }) {
  const douyin = platform === 'douyin';
  return (
    <div className="empty-state">
      <span className="empty-icon" aria-hidden="true">{douyin ? <Monitor size={27} /> : <Captions size={27} />}</span>
      <h3>{hasVideo ? 'Choose a caption track.' : douyin ? 'Your Douyin transcript starts here.' : 'Start with the captions.'}</h3>
      <p className="empty-description">{hasVideo ? 'Select a language in the source panel, then click Get transcript.' : douyin ? 'Paste a public Douyin link to check for timed captions. Complete any requested verification in the separate Douyin window. Some videos expose no caption track.' : 'Paste a YouTube link to read its available captions, then copy the full transcript into Codex.'}</p>
      {!douyin && !hasVideo && <button className="text-button" onClick={onExample}>Try an example<ArrowUpRight size={16} /></button>}
      <div className="empty-features">
        <span>{douyin ? <Monitor size={16} /> : <Captions size={16} />}{douyin ? 'Official browser access' : 'Original caption text'}</span>
        <span><Clock3 size={16} />{douyin ? 'Original caption timing' : 'Clickable timestamps'}</span>
        <span><FileText size={16} />One file for Codex</span>
      </div>
    </div>
  );
}
