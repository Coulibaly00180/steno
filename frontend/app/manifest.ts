import type { MetadataRoute } from "next";

/**
 * Sténo installable on a phone (feuille de route n° 4, phase 1), from the HTTPS address of the
 * local network (docs/acces-reseau.md). Once installed, Android lists it in the share sheet:
 * the shared link opens /envoyer.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Sténo",
    short_name: "Sténo",
    description: "Transcription, traduction et résumé, 100 % en local",
    lang: "fr",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#111827",
    theme_color: "#111827",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
    share_target: {
      action: "/envoyer",
      method: "GET",
      enctype: "application/x-www-form-urlencoded",
      params: { title: "title", text: "text", url: "url" },
    },
  };
}
