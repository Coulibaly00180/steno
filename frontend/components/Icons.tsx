import type { ReactNode, SVGProps } from "react";

type IconName = "home" | "library" | "template" | "system" | "settings" | "shield" | "sun" | "moon" | "upload" | "video" | "audio" | "close" | "chevron" | "arrow" | "more" | "download" | "trash" | "retry" | "chat" | "edit" | "sparkle" | "search" | "tag" | "mic" | "link" | "pause" | "stop" | "people" | "thumbUp" | "thumbDown" | "chip" | "check" | "mail" | "scissors" | "series" | "lock" | "gauge";

export function Icon({ name, size = 17, ...props }: SVGProps<SVGSVGElement> & { name: IconName; size?: number }) {
  const paths: Record<IconName, ReactNode> = {
    home: <><path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10v9a1 1 0 0 0 1 1H10v-6h4v6h3.5a1 1 0 0 0 1-1v-9"/></>,
    library: <><rect x="3.3" y="3.3" width="7" height="7" rx="1.4"/><rect x="13.7" y="3.3" width="7" height="7" rx="1.4"/><rect x="3.3" y="13.7" width="7" height="7" rx="1.4"/><rect x="13.7" y="13.7" width="7" height="7" rx="1.4"/></>,
    template: <><rect x="3.3" y="3.3" width="17.4" height="17.4" rx="2"/><path d="M3.3 9.3h17.4M9 9.3V20.7"/></>,
    system: <path d="M3 12h4l2 7 4-14 2 7h6"/>,
    settings: <><circle cx="12" cy="12" r="3.1"/><path d="M12 3v2.4M12 18.6V21M21 12h-2.4M5.4 12H3M18.2 5.8l-1.7 1.7M7.5 16.5l-1.7 1.7M18.2 18.2l-1.7-1.7M7.5 7.5 5.8 5.8"/></>,
    shield: <><path d="M12 2.5 4.5 5.5v6c0 5 3.2 8.3 7.5 10 4.3-1.7 7.5-5 7.5-10v-6Z"/><path d="m8.5 12 2.3 2.3L15.8 9"/></>,
    sun: <><circle cx="12" cy="12" r="4"/><path d="M12 2.6v2.3M12 19.1v2.3M4.9 4.9l1.6 1.6M17.5 17.5l1.6 1.6M2.6 12h2.3M19.1 12h2.3M4.9 19.1l1.6-1.6M17.5 6.5l1.6-1.6"/></>,
    moon: <path d="M19.5 14A8 8 0 1 1 10 4.5a6.4 6.4 0 0 0 9.5 9.5Z"/>,
    upload: <><path d="M8 16.5H6.5A3.5 3.5 0 0 1 5 9.8 5 5 0 0 1 14.7 7 4 4 0 0 1 18 15.5H16"/><path d="M12 20v-7.5M9.2 15.2 12 12.5l2.8 2.7"/></>,
    video: <><rect x="2.5" y="6" width="13" height="12" rx="2"/><path d="m15.5 10.5 5.5-3.2v9.4l-5.5-3.2Z"/></>,
    audio: <path d="M3 12h2l2-6 3 14 3-11 2 5 2-3h4"/>,
    close: <path d="M5 5l14 14M19 5 5 19"/>,
    chevron: <path d="m6 9 6 6 6-6"/>,
    arrow: <path d="M5 12h14M13 6l6 6-6 6"/>,
    more: <><circle cx="5" cy="12" r="1.2" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.2" fill="currentColor" stroke="none"/></>,
    download: <><path d="M12 3v12M7.5 10.5 12 15l4.5-4.5"/><path d="M4 20h16"/></>,
    trash: <><path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13"/></>,
    retry: <><path d="M20 7v5h-5"/><path d="M19 12a7 7 0 1 0-2 5"/></>,
    edit: <><path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17Z"/><path d="m14.5 7.5 3 3"/></>,
    search: <><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/></>,
    tag: <><path d="M3.5 12.3V4.5a1 1 0 0 1 1-1h7.8l8.2 8.2a1.4 1.4 0 0 1 0 2l-6.3 6.3a1.4 1.4 0 0 1-2 0Z"/><circle cx="8" cy="8" r="1.3"/></>,
    mic: <><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7"/></>,
    link: <><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></>,
    pause: <path d="M8.5 5v14M15.5 5v14"/>,
    stop: <rect x="6" y="6" width="12" height="12" rx="2"/>,
    people: <><circle cx="9" cy="8" r="3.2"/><path d="M3.5 19.5a5.5 5.5 0 0 1 11 0"/><circle cx="17" cy="9" r="2.4"/><path d="M15.5 14.2a4.5 4.5 0 0 1 5.5 4.3"/></>,
    thumbUp: <><path d="M7 11v9H4v-9Z"/><path d="M7 11l4-7a2 2 0 0 1 3 2l-1 4h5.5a2 2 0 0 1 2 2.3l-1.2 6A2 2 0 0 1 17.3 20H7"/></>,
    thumbDown: <><path d="M7 13V4H4v9Z"/><path d="M7 13l4 7a2 2 0 0 0 3-2l-1-4h5.5a2 2 0 0 0 2-2.3l-1.2-6A2 2 0 0 0 17.3 4H7"/></>,
    chip: <><rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2.5V6M15 2.5V6M9 18v3.5M15 18v3.5M2.5 9H6M2.5 15H6M18 9h3.5M18 15h3.5"/></>,
    check: <><rect x="3.5" y="3.5" width="17" height="17" rx="3"/><path d="m8 12 3 3 5-6"/></>,
    mail: <><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3.5 6.5 8.5 6.5 8.5-6.5"/></>,
    scissors: <><circle cx="6" cy="6.5" r="2.7"/><circle cx="6" cy="17.5" r="2.7"/><path d="M8.3 8 20 18M8.3 16 20 6"/></>,
    series: <><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M8 2.5v3M16 2.5v3M7.5 13h2M11 13h2M14.5 13h2M7.5 16.5h2"/></>,
    lock: <><rect x="4.5" y="10.5" width="15" height="10" rx="2"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/></>,
    gauge: <><path d="M4 17a8 8 0 1 1 16 0"/><path d="m12 17 4-5.5"/><path d="M6.5 17h1M16.5 17h1M12 9v1"/></>,
    sparkle: <path d="M12 3.5 13.8 9 19.5 10.8 13.8 12.6 12 18.5 10.2 12.6 4.5 10.8 10.2 9Z"/>,
    chat: <><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-5 4v-4.5a2.5 2.5 0 0 1-1-2Z"/><path d="M8 8h8M8 12h5"/></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" {...props}>{paths[name]}</svg>;
}
