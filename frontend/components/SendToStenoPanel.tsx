"use client";

import { useEffect, useRef, useState } from "react";
import { bookmarklet } from "../lib/access";
import { Icon } from "./Icons";

/** Feuille de route n° 4, phase 1: send the page being read to Sténo (extension, bookmarklet, phone). */
export default function SendToStenoPanel() {
  const link = useRef<HTMLAnchorElement>(null);
  const [origin, setOrigin] = useState("");

  useEffect(() => {
    setOrigin(window.location.origin);
    // React refuses javascript: links in JSX: the bookmarklet's address is set on the element itself.
    link.current?.setAttribute("href", bookmarklet(window.location.origin));
  }, []);

  return <section className="card settings-section" id="envoyer">
    <h2><Icon name="link" size={15} /> Envoyer à Sténo depuis le navigateur</h2>
    <p className="muted">Pour envoyer la vidéo ou le podcast que vous regardez sans copier de lien. Les pages des plateformes vidéo (YouTube…) demandent l&apos;option « Import de liens » ci-dessous.</p>

    <h3 className="field-label">Extension Chrome, Edge ou Firefox (un clic)</h3>
    <ol className="network-steps">
      <li>Le dossier <code>extension</code> se trouve dans le dossier d&apos;installation de Sténo.</li>
      <li>Chrome ou Edge : ouvrez <code>chrome://extensions</code> (ou <code>edge://extensions</code>), activez le « Mode développeur », puis « Charger l&apos;extension non empaquetée » et choisissez ce dossier. Firefox (128 ou plus) : <code>about:debugging</code> › Ce Firefox › « Charger un module complémentaire temporaire » › <code>manifest.json</code> (à refaire après chaque redémarrage de Firefox).</li>
      <li>Dans les options de l&apos;extension, indiquez l&apos;adresse <strong className="mono">{origin || "…"}</strong>, plus un jeton d&apos;accès (ci-dessus) si vous êtes sur un autre appareil ou si le mot de passe est demandé sur cet ordinateur.</li>
      <li>Un clic sur l&apos;icône envoie l&apos;onglet ; un clic droit sur l&apos;icône (ou sur la page) propose « Envoyer à Sténo avec des options… » pour choisir le template et les intervenants. Une notification signale la fin de l&apos;analyse.</li>
    </ol>
    <p className="field-hint">L&apos;extension ne lit pas les pages visitées : elle n&apos;envoie l&apos;adresse de l&apos;onglet qu&apos;au clic, et seulement à l&apos;adresse de Sténo réglée dans ses options.</p>

    <h3 className="field-label">Favori « Envoyer à Sténo » (sans extension)</h3>
    <p className="field-hint">Faites glisser ce bouton dans la barre des favoris. Sur une page vidéo, un clic dessus ouvre Sténo avec le lien, prêt à analyser.</p>
    {/* href set in the effect above */}
    <a ref={link} className="btn small bookmarklet" onClick={event => event.preventDefault()} draggable>Envoyer à Sténo</a>

    <h3 className="field-label">Depuis un téléphone Android</h3>
    <ol className="network-steps">
      <li>Ouvrez Sténo sur le téléphone par l&apos;adresse HTTPS du réseau local (section « Accès et sécurité »), avec l&apos;autorité locale installée.</li>
      <li>Menu de Chrome › « Ajouter à l&apos;écran d&apos;accueil » (ou « Installer l&apos;application »).</li>
      <li>Dans YouTube ou un lecteur de podcasts : Partager › Sténo. Le lien s&apos;ouvre dans Sténo, prêt à analyser.</li>
    </ol>
  </section>;
}
