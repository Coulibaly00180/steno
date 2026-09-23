"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { isActive } from "./jobs";
import type { VideoSummary } from "../components/VideoTable";

const ACTIVE_REFRESH_MS = 4000;

/**
 * The library in one call (n°17). While a job is queued or running, the list
 * refreshes itself so that positions and estimates stay current.
 */
export function useVideoList(query: string) {
  const [videos, setVideos] = useState<VideoSummary[] | null>(null);
  const [error, setError] = useState("");
  // Typing in the search box sends several requests: only the latest may win.
  const latest = useRef(0);

  const load = useCallback(async () => {
    const request = ++latest.current;
    try {
      const rows = await api<VideoSummary[]>(`/videos${query ? `?${query}` : ""}`);
      if (request === latest.current) { setVideos(rows); setError(""); }
    } catch (reason) {
      if (request === latest.current) setError(String(reason));
    }
  }, [query]);

  useEffect(() => { void load(); }, [load]);

  const hasActive = !!videos?.some(video => isActive(video.job));
  useEffect(() => {
    if (!hasActive) return;
    const interval = window.setInterval(() => void load(), ACTIVE_REFRESH_MS);
    return () => window.clearInterval(interval);
  }, [hasActive, load]);

  return { videos, error, reload: load };
}
