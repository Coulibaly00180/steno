"use client";

import { useState, type RefObject } from "react";
import { API } from "../lib/api";

/**
 * Source video (or audio) with seeking through HTTP Range requests (n°1).
 * When the browser cannot decode the source (MKV, AVI…), the WAV extracted for
 * transcription is played instead so the transcript can still be followed.
 */
export default function MediaPlayer({ videoId, mediaKind, sourceAvailable, audioAvailable, subtitles, translation, mediaRef, onTime }: {
  videoId: string;
  mediaKind: string;
  sourceAvailable: boolean;
  audioAvailable: boolean;
  subtitles: boolean;
  /** Label of the translated subtitle track (n°20), when the video was translated. */
  translation?: string | null;
  mediaRef: RefObject<HTMLMediaElement | null>;
  onTime: (seconds: number) => void;
}) {
  const [fallback, setFallback] = useState(!sourceAvailable);
  const bind = (element: HTMLMediaElement | null) => { mediaRef.current = element; };
  const common = {
    controls: true,
    preload: "metadata" as const,
    onTimeUpdate: (event: { currentTarget: HTMLMediaElement }) => onTime(event.currentTarget.currentTime),
    onSeeked: (event: { currentTarget: HTMLMediaElement }) => onTime(event.currentTarget.currentTime),
  };

  if (fallback) {
    if (!audioAvailable) return <p className="media-note">Lecture impossible : le fichier source n&apos;est plus disponible.</p>;
    return <div className="media-player audio">
      {sourceAvailable && <p className="media-note">Ce format n&apos;est pas lisible par le navigateur : lecture de la piste audio extraite.</p>}
      <audio ref={bind} src={`${API}/videos/${videoId}/audio`} {...common} />
    </div>;
  }
  if (mediaKind === "audio") {
    return <div className="media-player audio"><audio ref={bind} src={`${API}/videos/${videoId}/media`} onError={() => setFallback(true)} {...common} /></div>;
  }
  return <div className="media-player">
    {/* Some codecs (HEVC in Chromium…) load without error but decode no picture: videoWidth stays 0. */}
    <video ref={bind} src={`${API}/videos/${videoId}/media`} onError={() => setFallback(true)} onLoadedMetadata={event => { if (event.currentTarget.videoWidth === 0 && audioAvailable) setFallback(true); }} playsInline {...common}>
      {subtitles && <track kind="subtitles" src={`${API}/videos/${videoId}/exports/transcript.vtt`} label="Transcription" />}
      {subtitles && translation && <track kind="subtitles" src={`${API}/videos/${videoId}/exports/translation.vtt`} label={`Traduction (${translation})`} />}
    </video>
  </div>;
}
