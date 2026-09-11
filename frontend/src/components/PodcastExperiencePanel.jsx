import { useState } from 'react';
import { podcastOf } from '../utils/podcast';
import PodcastAudioPanel from './PodcastAudioPanel';
import PodcastTextPanel from './PodcastTextPanel';

export default function PodcastExperiencePanel({ article, variant, onVariantChange }) {
  return (
    <PodcastExperience
      key={article?.id}
      article={article}
      variant={variant}
      onVariantChange={onVariantChange}
    />
  );
}

function PodcastExperience({ article, variant: controlledVariant, onVariantChange }) {
  const podcast = podcastOf(article);
  const [localVariant, setLocalVariant] = useState(() => (
    podcast?.audio_url ? 'original' : 'digest'
  ));

  if (!podcast) return null;

  const variant = controlledVariant ?? localVariant;
  const guideVisible = variant === 'digest' && Boolean(podcast.condensed_audio_url);
  const handleVariantChange = (nextVariant) => {
    if (controlledVariant === undefined) setLocalVariant(nextVariant);
    onVariantChange?.(nextVariant);
  };

  return (
    <div className={`podcast-experience-panel ${guideVisible ? 'has-guide' : ''}`}>
      <PodcastAudioPanel
        article={article}
        variant={variant}
        onVariantChange={handleVariantChange}
      />
      {guideVisible && (
        <PodcastTextPanel
          episodeId={article.id}
          showDigest
          hiddenTranscriptKinds={['publisher_transcript']}
        />
      )}
    </div>
  );
}
