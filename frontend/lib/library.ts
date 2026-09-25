"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiWithHeaders } from "./api";
import { isActive } from "./jobs";
import type { VideoSummary } from "../components/VideoTable";

const ACTIVE_REFRESH_MS = 4000;
export const LIBRARY_PAGE = 100;
// The API serves at most this many rows per call: a longer list comes in several.
const API_PAGE_MAX = 500;

/**
 * The library page by page (n°17): the first 100 videos, then 100 more at each
 * « Afficher plus ». While a job is queued or running, the rows shown refresh
 * themselves so that positions and estimates stay current.
 */
export function useVideoList(query: string | null) {
  const [videos, setVideos] = useState<VideoSummary[] | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [wanted, setWanted] = useState(LIBRARY_PAGE);
  const [error, setError] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  // Typing in the search box sends several requests: only the latest may win.
  const latest = useRef(0);

  // A new search or filter starts again at the first page.
  useEffect(() => { setWanted(LIBRARY_PAGE); }, [query]);

  const load = useCallback(async () => {
    // null: the filters are not known yet (read from the address), nothing to load.
    if (query === null) return;
    const request = ++latest.current;
    const params = new URLSearchParams(query);
    // A query that sets its own limit (the home page's 8 latest) gets exactly that.
    const fixed = params.has("limit");
    const count = fixed ? Number(params.get("limit")) : wanted;
    try {
      const pages = [];
      for (let offset = 0; offset < count; offset += API_PAGE_MAX) {
        const page = new URLSearchParams(params);
        page.set("limit", String(Math.min(API_PAGE_MAX, count - offset)));
        if (offset) page.set("offset", String(offset));
        pages.push(apiWithHeaders<VideoSummary[]>(`/videos?${page}`));
      }
      const results = await Promise.all(pages);
      if (request !== latest.current) return;
      setVideos(results.flatMap(result => result.data));
      const header = results[0]?.headers.get("X-Total-Count");
      setTotal(header ? Number(header) : null);
      setError("");
    } catch (reason) {
      if (request === latest.current) setError(String(reason));
    } finally {
      if (request === latest.current) setLoadingMore(false);
    }
  }, [query, wanted]);

  useEffect(() => { void load(); }, [load]);

  const hasActive = !!videos?.some(video => isActive(video.job));
  useEffect(() => {
    if (!hasActive) return;
    const interval = window.setInterval(() => void load(), ACTIVE_REFRESH_MS);
    return () => window.clearInterval(interval);
  }, [hasActive, load]);

  const loadMore = useCallback(() => { setLoadingMore(true); setWanted(value => value + LIBRARY_PAGE); }, []);
  const hasMore = total !== null && videos !== null && videos.length < total;
  return { videos, total, error, reload: load, loadMore, hasMore, loadingMore };
}
