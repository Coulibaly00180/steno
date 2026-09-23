import "./globals.css";
import AppShell from "../components/AppShell";

export const metadata = {
  title: "Sténo",
  description: "Transcription, traduction et résumé vidéo 100% local",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr">
      <body><AppShell>{children}</AppShell></body>
    </html>
  );
}
