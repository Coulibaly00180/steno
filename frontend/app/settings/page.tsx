import Link from "next/link";
import AccessPanel from "../../components/AccessPanel";
import BackupsPanel from "../../components/BackupsPanel";
import GlossaryEditor from "../../components/GlossaryEditor";
import StoragePanel from "../../components/StoragePanel";
import UrlImportPanel from "../../components/UrlImportPanel";
import WatchFolderPanel from "../../components/WatchFolderPanel";

export default function SettingsPage() {
  return <div className="page"><header className="page-header"><h1>Paramètres</h1><p>Configuration active de votre installation locale.</p></header><nav className="settings-toc" aria-label="Sections"><a href="#acces">Accès et sécurité</a><a href="#dossier-surveille">Dossier surveillé</a><a href="#import-liens">Import de liens</a><a href="#stockage">Espace disque</a><a href="#sauvegardes">Sauvegardes</a></nav><AccessPanel /><GlossaryEditor /><WatchFolderPanel /><UrlImportPanel /><StoragePanel /><BackupsPanel /><div className="settings-grid"><section className="card"><h2>Intelligence artificielle</h2><p className="muted" style={{ fontSize: 13, lineHeight: 1.6 }}>Modèle de langage, modèle de transcription, vitesse mesurée et mémoire de la carte graphique : voir la page <Link className="text-link" href="/models">Modèles</Link>.</p></section><section className="card"><h2>Limites d’analyse</h2><div className="setting-row"><span>Durée maximale</span><span>6 heures</span></div><div className="setting-row"><span>Taille maximale</span><span>2 Go</span></div><div className="setting-row"><span>Traitement</span><span>100 % local</span></div></section><section className="card"><h2>Stockage</h2><div className="setting-row"><span>Fichiers</span><span>/data</span></div><div className="setting-row"><span>Base</span><span>PostgreSQL 17</span></div><div className="setting-row"><span>Queue</span><span>Redis + RQ</span></div></section><section className="card"><h2>Confidentialité</h2><p className="muted" style={{ fontSize: 13, lineHeight: 1.6 }}>Les vidéos, transcriptions et conversations restent sur cet appareil. Les services sont publiés uniquement sur l’interface locale 127.0.0.1.</p></section></div></div>;
}
